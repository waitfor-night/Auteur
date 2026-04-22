from __future__ import annotations
import math
import shutil
import subprocess
import time
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Union, Any

import cv2
import numpy as np
from scenedetect import open_video, SceneManager, FrameTimecode
from scenedetect.detectors import AdaptiveDetector, ContentDetector

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Data Structures
# -----------------------------------------------------------------------------

@dataclass
class SplitConfig:
    """分段参数配置"""
    min_clip_dur: float = 4.0      # 最小片段时长(秒)
    max_clip_dur: float = 15.0     # 最大片段时长(秒)
    similarity_threshold: float = 0.55  # 相似度阈值 (0.0-1.0), 越高切得越碎
    detection_threshold: float = 3.0    # 镜头检测阈值
    min_scene_len: int = 15        # 最小场景帧数
    detector: str = "adaptive"     # 检测算法: 'adaptive' 或 'content'
    save_keyframes: bool = False   # 是否保存关键帧图片
    ffmpeg_batch_size: int = 4     # 并行导出数量
    verbose: bool = True           # 是否打印详细日志

@dataclass
class Shot:
    index: int
    start_sec: float
    end_sec: float

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec

@dataclass
class Clip:
    clip_id: int
    start_sec: float
    end_sec: float
    shot_count: int = 1
    split_from_clip_id: Optional[int] = None

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec

# -----------------------------------------------------------------------------
# Core Logic
# -----------------------------------------------------------------------------

class VideoSplitter:
    def __init__(self, video_path: Union[str, Path], output_dir: Union[str, Path], config: SplitConfig = SplitConfig()):
        self.video_path = Path(video_path).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.config = config
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        if not shutil.which("ffmpeg"):
            raise RuntimeError("FFmpeg not found. Please install FFmpeg and add it to system PATH.")
        if not self.video_path.exists():
            raise FileNotFoundError(f"Input video not found: {self.video_path}")

    def _log(self, msg: str):
        if self.config.verbose:
            logger.info(msg)

    def _detect_shots(self) -> Tuple[List[Shot], float, float]:
        """步骤1: 镜头检测"""
        video = open_video(str(self.video_path))
        sm = SceneManager()

        if self.config.detector == "adaptive":
            sm.add_detector(AdaptiveDetector(
                adaptive_threshold=self.config.detection_threshold,
                min_scene_len=self.config.min_scene_len,
            ))
        else:
            sm.add_detector(ContentDetector(
                threshold=self.config.detection_threshold,
                min_scene_len=self.config.min_scene_len,
            ))

        # self._log("Detecting shots...")
        sm.detect_scenes(video, show_progress=self.config.verbose)
        scene_list = sm.get_scene_list()

        fps = video.base_timecode.framerate
        total_dur = video.duration.get_seconds()

        if not scene_list:
            return [Shot(0, 0.0, total_dur)], total_dur, fps

        shots = [
            Shot(i, s.get_seconds(), e.get_seconds())
            for i, (s, e) in enumerate(scene_list)
        ]
        self._log(f"Detected {len(shots)} shots ({total_dur:.1f}s, {fps:.1f}fps)")
        return shots, total_dur, fps

    def _compute_histogram(self, frame: np.ndarray, bins: int = 32) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [bins, bins], [0, 180, 0, 256])
        hist = hist.flatten().astype(np.float32)
        cv2.normalize(hist, hist)
        return hist

    def _compute_similarity(self, shots: List[Shot]) -> np.ndarray:
        """步骤2: 计算相邻镜头相似度 (优化版: 单次顺序解码)"""
        if len(shots) <= 1:
            return np.array([], dtype=np.float32)

        self._log("Computing adjacent similarity...")
        samples_per_shot = 2
        resize_height = 160
        
        # 规划采样时间点
        plan = []
        for shot in shots:
            for k in range(samples_per_shot):
                t = shot.start_sec + shot.duration * (k + 0.5) / samples_per_shot
                plan.append((shot.index, t))
        plan.sort(key=lambda x: x[1])

        shot_hists: Dict[int, List[np.ndarray]] = {s.index: [] for s in shots}
        
        cap = cv2.VideoCapture(str(self.video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        current_frame = 0

        for shot_idx, t in plan:
            target_frame = int(t * fps)
            skip = target_frame - current_frame
            
            # 智能Seek: 距离远则Seek，距离近则Grab
            if skip > 30:
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                current_frame = target_frame
            else:
                while current_frame < target_frame:
                    cap.grab()
                    current_frame += 1

            ret, frame = cap.read()
            current_frame += 1

            if ret and frame is not None:
                h, w = frame.shape[:2]
                if h > resize_height:
                    scale = resize_height / h
                    frame = cv2.resize(frame, (int(w * scale), resize_height), interpolation=cv2.INTER_AREA)
                shot_hists[shot_idx].append(self._compute_histogram(frame))

        cap.release()

        # 计算每个 Shot 的平均特征
        descriptors = []
        for shot in shots:
            hists = shot_hists[shot.index]
            descriptors.append(np.mean(hists, axis=0) if hists else None)

        # 计算相邻相似度
        n = len(descriptors)
        adj_sim = np.zeros(n - 1, dtype=np.float32)
        for i in range(n - 1):
            if descriptors[i] is not None and descriptors[i + 1] is not None:
                score = cv2.compareHist(descriptors[i], descriptors[i + 1], cv2.HISTCMP_CORREL)
                adj_sim[i] = float(np.clip((score + 1.0) / 2.0, 0.0, 1.0))
        
        return adj_sim

    def _merge_and_split(self, shots: List[Shot], adj_sim: np.ndarray) -> List[Clip]:
        """步骤3: 合并与拆分逻辑"""
        if not shots:
            return []

        # Pass 1: 相似度合并
        groups = []
        g_start, g_end, g_count = shots[0].start_sec, shots[0].end_sec, 1

        for i in range(1, len(shots)):
            sim = adj_sim[i - 1] if i - 1 < len(adj_sim) else 0.0
            merged_dur = shots[i].end_sec - g_start

            if sim >= self.config.similarity_threshold and merged_dur <= self.config.max_clip_dur:
                g_end = shots[i].end_sec
                g_count += 1
            else:
                groups.append((g_start, g_end, g_count))
                g_start, g_end, g_count = shots[i].start_sec, shots[i].end_sec, shots[i].shot_count if hasattr(shots[i], 'shot_count') else 1
                g_count = 1
        groups.append((g_start, g_end, g_count))

        # Pass 2: 拯救过短片段 (双向吸收)
        merged = []
        # 向后吸收
        for start, end, count in groups:
            if (end - start) < self.config.min_clip_dur and merged:
                prev_s, prev_e, prev_c = merged[-1]
                if (end - prev_s) <= self.config.max_clip_dur:
                    merged[-1] = (prev_s, end, prev_c + count)
                    continue
            merged.append((start, end, count))
        
        # 向前吸收
        rescued = []
        i = 0
        while i < len(merged):
            start, end, count = merged[i]
            if (end - start) < self.config.min_clip_dur and i + 1 < len(merged):
                nxt_s, nxt_e, nxt_c = merged[i + 1]
                if (nxt_e - start) <= self.config.max_clip_dur:
                    merged[i + 1] = (start, nxt_e, count + nxt_c)
                    i += 1
                    continue
            rescued.append((start, end, count))
            i += 1

        # Pass 3: 强制切分过长片段
        clips = []
        cid = 1
        for start, end, count in rescued:
            dur = end - start
            if dur <= self.config.max_clip_dur:
                clips.append(Clip(cid, start, end, count))
                cid += 1
            else:
                n_pieces = math.ceil(dur / self.config.max_clip_dur)
                seg_dur = dur / n_pieces
                parent_id = cid
                for j in range(n_pieces):
                    seg_s = start + j * seg_dur
                    seg_e = start + (j + 1) * seg_dur if j < n_pieces - 1 else end
                    clips.append(Clip(
                        cid, seg_s, seg_e, count,
                        split_from_clip_id=parent_id if j > 0 else None,
                    ))
                    cid += 1
        return clips

    def _export_clips(self, clips: List[Clip], fps: float) -> Tuple[List[str], List[Dict[str, Any]]]:
        """步骤4: 执行FFmpeg导出，返回路径列表与 tools 一致的分镜信息列表。"""
        stem = self.video_path.stem
        generated_files = []
        cmds = []

        # 准备命令和预期的输出路径
        for clip in clips:
            out_name = f"{stem}-Clip-{clip.clip_id:03d}.mp4"
            out_file = self.output_dir / out_name
            generated_files.append(str(out_file))
            
            # FFmpeg 命令: -ss 放在 -i 前面为了加速，-avoid_negative_ts 确保时间戳正确
            cmd = (
                f'ffmpeg -nostdin -y -ss {clip.start_sec:.4f} -to {clip.end_sec:.4f} '
                f'-i "{self.video_path}" -c copy -avoid_negative_ts make_zero '
                f'-movflags +faststart "{out_file}" 2>/dev/null'
            )
            cmds.append(cmd)

        self._log(f"Exporting {len(clips)} clips via FFmpeg...")
        
        # 批量执行
        batch_size = self.config.ffmpeg_batch_size
        for i in range(0, len(cmds), batch_size):
            batch = cmds[i:i + batch_size]
            combined = " & ".join(batch) + " & wait"
            subprocess.run(combined, shell=True, check=False)

        # 分镜信息：与 tools.py 中 video_split_tool / get_split_video_inforamtion_from_scene_list 一致
        segment_list = []
        for clip, path in zip(clips, generated_files):
            start_tc = FrameTimecode(timecode=clip.start_sec, fps=fps)
            end_tc = FrameTimecode(timecode=clip.end_sec, fps=fps)
            segment_list.append({
                "segment_id": clip.clip_id,
                "start_time": start_tc.get_timecode(),
                "end_time": end_tc.get_timecode(),
                "segment_name": path,
                "frame_num": [start_tc.frame_num, end_tc.frame_num],
            })

        # 导出元数据 (可选)
        mapping = [
            {
                "clip_id": c.clip_id,
                "file_path": str(self.output_dir / f"{stem}-Clip-{c.clip_id:03d}.mp4"),
                "duration": round(c.duration, 3),
                "start": round(c.start_sec, 3),
                "end": round(c.end_sec, 3)
            } for c in clips
        ]
        with open(self.output_dir / "clip_mapping.json", "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2)

        return generated_files, segment_list

    def run(self) -> List[Dict[str, Any]]:
        """执行全流程，返回与 tools.py 中 video_split_tool 一致的分镜列表（segment_id, start_time, end_time, segment_name, frame_num）。"""
        t0 = time.time()
        
        # 1. Detect
        shots, total_dur, fps = self._detect_shots()
        
        # 2. Similarity
        adj_sim = self._compute_similarity(shots)
        
        # 3. Merge
        clips = self._merge_and_split(shots, adj_sim)
        
        # 4. Export
        if not clips:
            self._log("No clips generated. Copying original.")
            out_path = self.output_dir / f"{self.video_path.stem}_full.mp4"
            shutil.copy(self.video_path, out_path)
            start_tc = FrameTimecode(timecode=0, fps=fps)
            end_tc = FrameTimecode(timecode=total_dur, fps=fps)
            return [{
                "segment_id": 1,
                "start_time": start_tc.get_timecode(),
                "end_time": end_tc.get_timecode(),
                "segment_name": str(out_path),
                "frame_num": [start_tc.frame_num, end_tc.frame_num],
            }]

        output_paths, segment_list = self._export_clips(clips, fps)
        self._log(f"Done. {len(segment_list)} segments generated in {time.time()-t0:.2f}s")
        return segment_list

# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def auto_split_video(
    video_path: str, 
    output_dir: str = "./output",
    min_dur: float = 4.0,
    max_dur: float = 15.0,
    similarity: float = 0.55,
    verbose: bool = True
) -> List[Dict[str, Any]]:
    """
    端到端视频分段函数。

    返回格式与 tools.py 中 video_split_tool 一致：每项为 segment_id, start_time, end_time, segment_name, frame_num。
    
    Args:
        video_path: 输入视频文件路径
        output_dir: 输出文件夹路径
        min_dur: 最小片段时长(秒)
        max_dur: 最大片段时长(秒)
        similarity: 镜头相似度合并阈值 (0-1)，越高越难合并
        verbose: 是否打印日志

    Returns:
        List[Dict]: 分镜列表，每项含 segment_id, start_time, end_time, segment_name, frame_num（与 tools 一致）
    """
    config = SplitConfig(
        min_clip_dur=min_dur,
        max_clip_dur=max_dur,
        similarity_threshold=similarity,
        verbose=verbose
    )
    
    splitter = VideoSplitter(video_path, output_dir, config)
    return splitter.run()

# -----------------------------------------------------------------------------
# CLI Usage (Optional)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    
    # Example call
    video_path = "/data/home/hechengxin/data2/gqb/project/MS_New/multi-shot-multi-object-long-video-edit/rawdata/1/1月12日(14)-1.mp4"
    output_dir = str(Path(video_path).resolve().parent)  # 输出到原视频同文件夹
    segment_list = auto_split_video(video_path, output_dir)
    print("Segment list (same format as tools):", json.dumps(segment_list, indent=2, ensure_ascii=False))