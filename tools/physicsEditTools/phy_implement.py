import os
from pathlib import Path
from typing import List, Dict, Any, Tuple
from scenedetect import detect, ContentDetector, SceneList
from scenedetect.scene_manager import SceneList 
from .. import constants as tools_constants
from scenedetect import detect, ContentDetector
from scenedetect.video_splitter import (
    split_video_ffmpeg,
    default_formatter,
    SceneMetadata,
    VideoMetadata,
)
from pathlib import Path
from moviepy import VideoFileClip, concatenate_videoclips, ColorClip, CompositeVideoClip
import base64
import json
import time
import uuid
import requests


def _build_duration_based_segments(
    duration_s: float, time_length: float
) -> List[List[float]]:
    """Build segment boundaries [start_sec, end_sec] by splitting duration into chunks of at most time_length."""
    if duration_s <= 0 or time_length <= 0:
        return [[0.0, duration_s]] if duration_s > 0 else []
    segs = []
    t = 0.0
    while t < duration_s:
        end = min(t + time_length, duration_s)
        segs.append([t, end])
        t = end
    return segs


#存在一个默认输出地址
def split_video_ffmpeg_return_paths(
    video_path,
    scene_list,
    output_dir=None,
    output_file_template='$VIDEO_NAME-Scene-$SCENE_NUMBER.mp4',
    video_name=None,
    formatter=None,
    **kwargs,
):
    """
    调用 PySceneDetect 的 split_video_ffmpeg，然后直接返回“理论上生成的文件路径列表”
    不扫描文件夹，不检查文件是否存在
    """

    # 1. 先真正执行切分
    rc = split_video_ffmpeg(
        video_path,
        scene_list,
        output_dir=output_dir,
        output_file_template=output_file_template,
        video_name=video_name,
        formatter=formatter,
        **kwargs,
    )

    if rc != 0:
        raise RuntimeError(f"split_video_ffmpeg failed, return code={rc}")

    # 2. 用和 PySceneDetect 完全一致的规则生成文件名
    used_formatter = formatter or default_formatter(output_file_template)
    resolved_video_name = video_name or Path(video_path).stem

    video_meta = VideoMetadata(
        resolved_video_name,
        Path(video_path),
        total_scenes=len(scene_list),
    )

    out_dir = Path(output_dir) if output_dir else Path.cwd()

    output_paths = []
    for idx, (start, end) in enumerate(scene_list):
        scene_meta = SceneMetadata(idx, start, end)
        filename = used_formatter(video_meta, scene_meta)
        output_paths.append(str(out_dir / filename))

    return output_paths

def get_split_video_inforamtion_from_scene_list(video_path, scene_list,segment_paths):
    """
    根据原视频路径与场景列表，获取切分后各段子视频信息，以与 prompts 中 JSON 结构一致的列表返回。
    通过匹配 scenedetect 默认命名格式 '{视频名}-Scene-*.mp4' 读取输出目录中的文件。
    :param video_path: 原视频路径
    :param scene_list: SceneList from video_scene_logic_split_tool (or scenedetect.detect). Each element is (start_timecode, end_timecode) for one scene.
    :param segment_paths: 切分后子视频路径列表
    :return: 切分后子视频信息列表，每项含 segment_id, start_time, end_time, segment_name, frame_num
    """
    output_dir = os.path.dirname(video_path)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    split_video_paths = segment_paths
    result = []
    for i, (path, scene) in enumerate(zip(split_video_paths, scene_list)):
        start_tc, end_tc = scene
        result.append({
            "segment_id": i + 1,
            "start_time": start_tc.get_timecode(),
            "end_time": end_tc.get_timecode(),
            "segment_name": path,
            "frame_num": [start_tc.frame_num, end_tc.frame_num],
        })
    return result


# ========== 视频合并具体实现函数==========
VIDEO_EDIT_API_KEY = os.environ.get("VIDEO_EDIT_API")
VIDEO_EDIT_WS = os.environ.get("VIDEO_EDIT_WS")
VIDEO_EDIT_BASE_URL = "https://prompt-pilot.cn-beijing.volces.com/video-pilot"


def _video_to_base64(video_path: str) -> str:
    with open(video_path, "rb") as f:
        return f"data:video/mp4;base64,{base64.b64encode(f.read()).decode('utf-8')}"


def _image_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    if image_path.lower().endswith(".png"):
        return f"data:image/png;base64,{data}"
    if image_path.lower().endswith((".jpg", ".jpeg")):
        return f"data:image/jpeg;base64,{data}"
    if image_path.lower().endswith(".webp"):
        return f"data:image/webp;base64,{data}"
    return f"data:image/png;base64,{data}"


def submit_task(
    video_path: str,
    image_paths: List[str],
    user_message: str,
    video_ratio: str = "16:9",
    video_resolution: str = "480p",
) -> str:
    headers = {
        "Authorization": f"Bearer {VIDEO_EDIT_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "RequestId": str(uuid.uuid4()),
        "WorkspaceId": VIDEO_EDIT_WS,
        "RefVideoUrl": _video_to_base64(video_path),
        "UserMessage": user_message,
        "RefImages": [_image_to_base64(img) for img in image_paths],
        "Model": "doubao-seedance-1-5-pro-251215",
        "GenerateAudio": True,
        "TimeBudget": 3,
        "VideoRatio": video_ratio,
        "VideoResolution": video_resolution,
        "ImitationSetting": "imitative",
    }
    response = requests.post(
        f"{VIDEO_EDIT_BASE_URL}?Version=1.0&Action=ImitateAndGenerateVideo",
        headers=headers,
        json=data,
    )
    result = response.json()
    print("提交任务响应:", json.dumps(result, indent=2, ensure_ascii=False))
    if isinstance(result, dict) and result.get("Error"):
        raise Exception(str(result.get("Error")))
    task_id = (
        result.get("Result", {}).get("TaskId") if isinstance(result, dict) else None
    )
    if not task_id:
        raise KeyError("TaskId")
    return task_id


def _get_task_result(task_id: str) -> dict:
    headers = {
        "Authorization": f"Bearer {VIDEO_EDIT_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "RequestId": str(uuid.uuid4()),
        "WorkspaceId": VIDEO_EDIT_WS,
        "TaskId": task_id,
    }
    response = requests.post(
        f"{VIDEO_EDIT_BASE_URL}?Version=1.0&Action=GetTaskResult",
        headers=headers,
        json=data,
    )
    return response.json()


def wait_for_task(
    task_id: str, video_path: str, max_wait: int = 1000, check_interval: int = 10
) -> dict:
    """轮询等待任务完成。max_wait: 最大等待时间（秒），check_interval: 检查间隔（秒）。"""
    start_time = time.time()
    last_full_log = 0.0
    while time.time() - start_time < max_wait:
        outer = _get_task_result(task_id)
        inner = outer.get("Result", {}) if isinstance(outer, dict) else {}
        status = (inner.get("TaskStatus") or outer.get("Status") or "").strip().lower()
        elapsed = int(time.time() - start_time)
        # 每 60 秒或状态变化时打印完整响应，避免刷屏
        if elapsed - int(last_full_log) >= 60 or status in ("completed", "failed"):
            print(
                f"\n任务状态 (已等待 {elapsed}s): {json.dumps(outer, indent=2, ensure_ascii=False)}"
            )
            last_full_log = elapsed
        if status == "completed":
            print("\n✅ 任务完成！")
            return inner
        if status == "failed":
            print("\n❌ 任务失败！")
            return inner
        if status in ("pending", "processing", ""):
            print(f"⏳ 任务进行中... (已等待 {elapsed}s)，{check_interval}s 后重试")
        else:
            print(f"⚠️ 未知状态: {status} (已等待 {elapsed}s)")
        time.sleep(check_interval)
    raise TimeoutError(f"任务{task_id}，视频路径{video_path}，超时（{max_wait}秒）")


def download_video(video_url: str, save_path: str = "output.mp4") -> None:
    print(f"\n📥 下载视频到: {save_path}")
    response = requests.get(video_url, stream=True)
    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"✅ 视频已保存: {save_path}")


def download_image(image_url: str, save_path: str = "output.jpg") -> None:
    print(f"\n📥 下载图片到: {save_path}")
    response = requests.get(image_url, stream=True)
    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"✅ 图片已保存: {save_path}")


def edit_video(
    video_path: str,
    image_paths: List[str],
    user_message: str,
    video_ratio: str = "16:9",
    video_resolution: str = "1080p",
    save_path: str = "output.mp4",
) -> Tuple[str, str]:
    """
    调用视频编辑 API：提交任务、等待完成、下载结果。
    Returns: 保存后的本地路径 save_path, 关键帧保存路径 key_frame_save_path
    """
    task_id = submit_task(
        video_path, image_paths, user_message, video_ratio, video_resolution
    )
    result = wait_for_task(task_id, video_path)
    if result.get("TaskStatus") == "completed":
        video_url = result.get("FullVideo") or result.get("ResultUrl")
        key_frame_url = result.get("VideoSegments")[0].get("KeyframeURL")
        if video_url and key_frame_url:
            video_save_path = save_path
            download_video(video_url, video_save_path)
            key_frame_save_path = video_save_path.replace(".mp4", "_keyframe.jpg")
            download_image(key_frame_url, key_frame_save_path)
            return video_save_path, key_frame_save_path
        else:
            raise RuntimeError("未找到结果视频 URL 或关键帧 URL")
    raise RuntimeError(f"任务失败: {result}")


# ========== 视频合并具体实现函数==========
def _align_clip_to_target_moviepy(clip, path: str, target_fps: float, target_w: int, target_h: int):
    """
    若 path 带 edited 或已与目标一致则返回原 clip；否则将 clip 缩放到目标长宽（保持比例黑边填充）并统一帧率。
    """
    basename = os.path.basename(path)
    is_edited = "edited" in basename.lower()
    same_size = clip.w == target_w and clip.h == target_h
    same_fps = abs(clip.fps - target_fps) < 0.01
    if is_edited or (same_size and same_fps):
        return clip
    scale = min(target_w / clip.w, target_h / clip.h)
    new_w = int(round(clip.w * scale))
    new_h = int(round(clip.h * scale))
    resized = clip.resized((new_w, new_h))
    bg = ColorClip(size=(target_w, target_h), color=(0, 0, 0)).with_duration(resized.duration)
    centered = resized.with_position("center")
    composed = CompositeVideoClip([bg, centered]).with_duration(resized.duration).with_fps(target_fps)
    return composed

def merge_video(video_paths: List[str], save_path: str = None) -> str:
    """
    按传入 list 顺序合并视频片段。合并前将名称中不带 edited 的视频的帧率、分辨率
    与带 edited 的片段对齐，再拼接输出。
    Args:
        video_paths: 视频片段路径列表，按顺序拼接。
        save_path: 保存后的本地路径。为 None 时自动设为「第一个视频同目录下的 前缀_merge.mp4」，
                   前缀为第一个路径文件名中 -Scene- 之前的部分，若无 -Scene- 则为去掉扩展名的文件名。
    Returns: 保存后的本地路径 save_path
    """
    if not video_paths:
        raise ValueError("video_paths 不能为空")
    if save_path is None:
        first_path = os.path.abspath(video_paths[0])
        video_dir = os.path.dirname(first_path)
        basename = os.path.basename(first_path)
        prefix = basename.split("-Scene-")[0] if "-Scene-" in basename else os.path.splitext(basename)[0]
        save_path = os.path.join(video_dir, f"{prefix}_merge.mp4")
    if tools_constants.RUN_CONTEXT_VIDEO_PATH:
        video_dir = os.path.dirname(os.path.abspath(tools_constants.RUN_CONTEXT_VIDEO_PATH))
        save_path = os.path.join(video_dir, os.path.basename(save_path))
    else:
        save_path = os.path.abspath(save_path)
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

    paths_abs = [os.path.abspath(p) for p in video_paths]
    for p in paths_abs:
        if not os.path.exists(p):
            raise FileNotFoundError(f"视频文件不存在: {p}")

    clips = [VideoFileClip(p) for p in paths_abs]
    edited_idx = next((i for i, p in enumerate(paths_abs) if "edited" in os.path.basename(p).lower()), 0)
    ref = clips[edited_idx]
    target_fps, target_w, target_h = ref.fps, ref.w, ref.h

    unified = [
        _align_clip_to_target_moviepy(c, p, target_fps, target_w, target_h)
        for c, p in zip(clips, paths_abs)
    ]
    final_clip = concatenate_videoclips(unified)
    try:
        final_clip.write_videofile(save_path, codec="libx264")
        return save_path
    finally:
        final_clip.close()
        for i in range(len(unified)):
            if unified[i] is not clips[i]:
                try:
                    unified[i].close()
                except Exception:
                    pass
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
   


def _seconds_to_timecode(sec: float) -> str:
    """Format seconds as HH:MM:SS.fff."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60  # seconds (float, 0..60)
    return f"{h:02d}:{m:02d}:{int(s):02d}.{int((s % 1) * 1000):03d}"