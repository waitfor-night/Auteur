import os
from agno.tools import tool
from pathlib import Path
from typing import List, Dict, Any
from utils.trace_recorder import recorder
from scenedetect import detect, ContentDetector, SceneList
from scenedetect.scene_manager import SceneList

from .phy_implement import _seconds_to_timecode
from utils.video_utils import get_video_metadata_ffprobe
from ..logicSplitTools.logic_implement import _ensure_scene_list


from .phy_implement import get_split_video_inforamtion_from_scene_list,split_video_ffmpeg_return_paths,_build_duration_based_segments
from utils.auto_video_splite import auto_split_video
from .phy_implement import merge_video

@tool
@recorder.record
def get_video_metadata(video_url: str) -> Dict[str, Any]:
    """
    Get video metadata (duration, width, height, fps) via ffprobe.
    Returns dict with duration_s, width, height, fps; raises on error.
    Args:
        video_url: The path of the video file to get metadata.
    Returns:
        Dict with duration_s, width, height, fps.
    """
    return get_video_metadata_ffprobe(video_url)





@tool
@recorder.record
def video_split_tool(
    video_path: str,
    segments: List[Any],
) -> List[Dict[str, Any]]:
    """
    Actual segmentation of the video.
    Cut the video into segments according to the segment list; output files in the same directory as the source video, named <basename>-Scene-001.mp4, etc.

    Args:
        video_path: Path to the source video file.
        segments: Scene list from video_scene_logic_split_tool or detect_and_resegment_shots_tool. Can be the raw return value (SceneList) or a serialized form (e.g. list of [start_sec, end_sec] or [timecode_str, timecode_str] per scene); video_path is used to get fps when converting.
    Returns:
        List of segments with segment_id, start_time, end_time, segment_name, frame_num.
    """
    segments = _ensure_scene_list(segments, video_path)
    output_dir = os.path.dirname(video_path)
    segment_paths = split_video_ffmpeg_return_paths(
        video_path, segments, output_dir=output_dir
    )
    return get_split_video_inforamtion_from_scene_list(
        video_path, segments, segment_paths
    )





@tool
@recorder.record
def split_video_by_duration_tool(
    video_path: str,
    time_length: float,
) -> List[Dict[str, Any]]:
    """
    Split the video into segments by fixed duration (no scene detection). Use this for 续写/延长 when the video is longer than time_length: only the **last** segment will be used as reference for generation; the preceding segments will be kept as-is (KEEP).

    Args:
        video_path: Path to the source video file.
        time_length: Max duration per segment in seconds (same as the runtime time_length). Segments are [0, time_length], [time_length, 2*time_length], ..., last segment may be shorter.
    Returns:
        List of segments with segment_id, start_time, end_time, segment_name, frame_num. Same format as video_split_tool. When duration <= time_length, returns one segment with segment_name = video_path (no file is created).
    """
    meta = get_video_metadata_ffprobe(video_path)
    duration_s = meta["duration_s"]
    if duration_s <= time_length:
        # No split: whole video is one segment (use as reference only)
        return [
            {
                "segment_id": 1,
                "start_time": "00:00:00.000",
                "end_time": _seconds_to_timecode(duration_s),
                "segment_name": video_path,
                "frame_num": [0, int(duration_s * (meta.get("fps") or 25))],
            }
        ]
    segs = _build_duration_based_segments(duration_s, time_length)
    segments = _ensure_scene_list(segs, video_path)
    output_dir = os.path.dirname(video_path)
    segment_paths = split_video_ffmpeg_return_paths(
        video_path, segments, output_dir=output_dir
    )
    return get_split_video_inforamtion_from_scene_list(
        video_path, segments, segment_paths
    )


@tool
@recorder.record
def auto_video_split_tool(
    video_path: str,
) -> List[Dict[str, Any]]:
    """
    Auto split the video into segments.
    Args:
        video_path: Path to the source video file.
        out_dir: Path to the output directory.
    Returns:
        The list of segments.
    """
    output_dir = str(Path(video_path).resolve().parent)
    return auto_split_video(video_path, output_dir)

@tool
@recorder.record
def merge_video_tool(video_paths: List[str], save_path: str = "output.mp4") -> str:
    """
    Merge the video segments into a complete video.
    Args:
        video_paths: 视频片段路径列表。
        save_path: 保存后的本地路径。
    Returns: 保存后的本地路径 save_path,默认为output.mp4
    """
    return merge_video(video_paths, save_path)

__all__ = [
    "get_video_metadata",
    "video_split_tool",
    "split_video_by_duration_tool",
    "auto_video_split_tool",
    "merge_video_tool",
]
