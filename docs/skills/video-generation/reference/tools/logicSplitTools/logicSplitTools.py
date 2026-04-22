import os
from pathlib import Path
from typing import List, Dict, Any
from agno.tools import tool
from utils.trace_recorder import recorder

from scenedetect import detect, ContentDetector, SceneList
from scenedetect.scene_manager import SceneList
from utils.auto_video_splite import SplitConfig, VideoSplitter
from utils.auto_video_splite import auto_split_video
from .logic_implement import ensure_at_least_one_scene, _ensure_scene_list
from .logic_implement import _detect_and_resegment_shots

@tool
@recorder.record
def video_scene_logic_split_tool(
    video_path: str,
    use_auto_split_pipeline: bool,
) -> SceneList:
    """
    Split the video into scenes.
    When use_auto_split_pipeline is False (default), use ContentDetector and return a SceneList.
    When use_auto_split_pipeline is True, use the auto_video_splite pipeline (shot detect + similarity + merge/split)
    and return a serializable list of [start_sec, end_sec] segments (in seconds).
    Args:
        video_path: Path to the source video file.
        use_auto_split_pipeline: Whether to use auto_video_splite pipeline to split the video.
    Returns:
        SceneList or List[List[float, float]]: logical scene list.
    """
    if not use_auto_split_pipeline:
        scene_list = detect(video_path, ContentDetector())
        scene_list = ensure_at_least_one_scene(video_path, scene_list)
        return scene_list
    print(
        "use_auto_split_pipeline is True, use auto_video_splite pipeline to split the video"
    )
    config = SplitConfig()
    output_dir = (
        Path(video_path).resolve().parent
    )  # 仅用于构造 VideoSplitter，不在此处导出文件
    splitter = VideoSplitter(video_path, output_dir, config)

    # 1. Detect 初步逻辑划分
    shots, total_dur, fps = splitter._detect_shots()
    # 2. Similarity 相似度计算
    adj_sim = splitter._compute_similarity(shots)
    # 3. Merge 进一步逻辑划分与合并/拆分
    clips = splitter._merge_and_split(shots, adj_sim)

    # 将 Clip 列表转换为可序列化的 [[start_sec, end_sec], ...] 形式
    if not clips:
        # 与 auto_video_splite.run 中的退化逻辑一致：整个视频作为一个段
        return [[0.0, float(total_dur)]]
    return [[float(c.start_sec), float(c.end_sec)] for c in clips]



@tool
@recorder.record
def detect_and_resegment_shots_tool(
    scene_list: List[Any],
    time_length: float,
    video_path: str,
) -> List[Any]:
    """
    Resegment long scenes by max duration; use before actual split so segment length is bounded.
    Args:
        scene_list: Scene list from video_scene_logic_split_tool. When called by the agent it may be serialized (e.g. list of [start_sec, end_sec] or [timecode_str, timecode_str] per scene).
        time_length: Max scene duration in seconds; scenes longer than this are split into sub-scenes.
        video_path: Path to the video (used to get fps when scene_list is in serialized form).
    Returns:
        New scene list (pass to video_split_tool for actual cut).
    """
    scene_list = _ensure_scene_list(scene_list, video_path)
    result = _detect_and_resegment_shots(scene_list, time_length)
    # Return serializable form so the agent can pass it to video_split_tool
    return [[tc0.get_seconds(), tc1.get_seconds()] for tc0, tc1 in result]

__all__ = ["video_scene_logic_split_tool", "detect_and_resegment_shots_tool"]