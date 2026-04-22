from typing import Any, List
from utils.video_utils import get_video_metadata_ffprobe
from scenedetect.scene_manager import SceneList
from scenedetect import FrameTimecode

import time 

# ========== 视频分片列表，时间分析函数==========
def _parse_sec_part(sec_part: str) -> float:
    """解析秒部分 'SS' 或 'SS.fff' 为秒数（float）。"""
    sec_part = sec_part.strip()
    if "." in sec_part:
        sec_str, frac_str = sec_part.split(".", 1)
        return int(sec_str) + float("0." + frac_str)
    return float(int(sec_part))


def timecode_to_seconds(ts: str) -> float:
    """
    解析时间字符串为秒数。支持：
    - mm:ss 或 mm:ss.ff（2 段）
    - HH:MM:SS 或 HH:MM:SS.fff（3 段，如 00:00:00.000）
    """
    ts = ts.strip()
    parts = ts.split(":")
    if len(parts) == 2:
        minutes = int(parts[0])
        seconds = _parse_sec_part(parts[1])
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = _parse_sec_part(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"无效时间格式: {ts}, 期望 mm:ss[.ff] 或 HH:MM:SS[.fff]")


def mmss_to_seconds(ts: str) -> float:
    """解析 mm:ss 或 mm:ss.ff 或 HH:MM:SS.fff 为秒数。兼容 timecode 格式。"""
    return timecode_to_seconds(ts)

def _seconds_to_timecode(sec: float) -> str:
    """Format seconds as HH:MM:SS.fff."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60  # seconds (float, 0..60)
    return f"{h:02d}:{m:02d}:{int(s):02d}.{int((s % 1) * 1000):03d}"



def _scene_list_from_serializable(serializable: List[Any], video_path: str) -> list:
    """
    将 Agent 传入的可序列化 scene_list（如 [[0, 8.01]] 或 [['00:00:00.000', '00:00:08.000']]）
    转为 list of [FrameTimecode, FrameTimecode]，需 video_path 取 fps。
    """
    if not serializable:
        return []
    meta = get_video_metadata_ffprobe(video_path)
    fps = meta["fps"]
    result = []
    for pair in serializable:
        if not pair or len(pair) < 2:
            continue
        start_val, end_val = pair[0], pair[1]
        if isinstance(start_val, (int, float)) and isinstance(end_val, (int, float)):
            start_s = float(start_val)
            end_s = float(end_val)
        else:
            start_s = timecode_to_seconds(str(start_val))
            end_s = timecode_to_seconds(str(end_val))
        result.append(
            [
                FrameTimecode(timecode=start_s, fps=fps),
                FrameTimecode(timecode=end_s, fps=fps),
            ]
        )
    return result

def _ensure_scene_list(scene_list: Any, video_path: str) -> list:
    """
    若 scene_list 已是 list of [FrameTimecode, FrameTimecode] 则原样返回；
    否则当作可序列化格式，用 video_path 取 fps 转成 SceneList。
    """
    if not scene_list:
        return []
    first = scene_list[0]
    if not first or len(first) < 2:
        return []
    if hasattr(first[0], "get_seconds") and hasattr(first[0], "get_framerate"):
        return list(scene_list)
    return _scene_list_from_serializable(list(scene_list), video_path)



def ensure_at_least_one_scene(video_path: str, scene_list: list) -> list:
    """
    若 detect() 未检测到任何镜头切换（单镜头视频），则将整片视为一个场景，
    便于后续按 max_duration 做二次划分。
    """
    if scene_list:
        return scene_list
    meta = get_video_metadata_ffprobe(video_path)
    fps = meta["fps"]
    duration_s = meta["duration_s"]
    start_tc = FrameTimecode(0, fps)
    end_tc = FrameTimecode(timecode=duration_s, fps=fps)
    return [[start_tc, end_tc]]



def _detect_and_resegment_shots(
    scene_list: SceneList, time_length: float
) -> SceneList:
    """
    检测长度较长的场景并按 time_length（秒）切分为子场景，返回新的 SceneList，便于后续用 split_video_ffmpeg 做实际分割。
    """
    if not scene_list:
        return []
    fps = scene_list[0][0].get_framerate()
    refined = []
    for start_tc, end_tc in scene_list:
        start_s = start_tc.get_seconds()
        end_s = end_tc.get_seconds()
        duration = end_s - start_s
        if duration <= time_length:
            refined.append([start_tc, end_tc])
        else:
            cur = start_s
            while cur < end_s:
                sub_end = min(cur + time_length, end_s)
                if sub_end <= cur:
                    break
                sub_start_tc = FrameTimecode(timecode=cur, fps=fps)
                sub_end_tc = FrameTimecode(timecode=sub_end, fps=fps)
                refined.append([sub_start_tc, sub_end_tc])
                cur = sub_end
    return refined


