import os
from agno.tools import tool
from pathlib import Path
from typing import List, Tuple, Dict, Any, Union, Optional
from utils.trace_recorder import recorder

import time
from .. import constants as tools_constants
from .mmut_implement import image_understanding
from .mmut_implement import (
    upload_video_to_ark,
    video_understanding,
    multimodal_analysis,
    analyze_segment_relevance,
)
from utils.story_gen_tools import extract_script_entities

@tool
@recorder.record
def video_understanding_tool(vidio_url, prompt):
    """
    Use this tool to perform detailed full-video understanding and return a rich textual description.

    IMPORTANT: Before calling this tool, verify that vidio_url is a valid video file path:
    - The path must exist and be a file (not a directory)
    - The file must be a video file (common extensions: .mp4, .mov, .avi, etc.)
    - If video_path from Runtime Inputs is empty, invalid, or missing, you MUST call getUserMessageTool first to ask the user for the correct video path, rather than calling this tool with an invalid path.

    Args:
        vidio_url: The url/path of the video to understand. Must be a valid video file path.
        prompt: The prompt or instruction for how to understand/describe the video (e.g., focus on characters, actions, positions).
    Returns:
        A detailed natural-language description (string) of the entire video content.
    Raises:
        ValueError: If vidio_url is empty, invalid, or points to a directory instead of a video file.
    """
    # Validate video path before proceeding
    if not vidio_url or not vidio_url.strip():
        raise ValueError(
            "video_path is empty or not provided. "
            "If the task requires a video, please call getUserMessageTool first to ask the user for the video path. "
            "If this is a pure generation task without source video, skip video_understanding_tool and proceed with planning."
        )

    video_path = Path(vidio_url.strip())
    if not video_path.exists():
        raise ValueError(
            f"Video file does not exist: {vidio_url}. "
            "Please call getUserMessageTool to ask the user for the correct video path, "
            "or verify the video_path from Runtime Inputs."
        )

    if video_path.is_dir():
        raise ValueError(
            f"Invalid video path: {vidio_url} is a directory, not a video file. "
            "Please call getUserMessageTool to ask the user for the correct video file path. "
            "The path should point to a video file (e.g., .mp4, .mov), not a directory."
        )

    # Check if it's likely a video file (basic extension check)
    video_extensions = {
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".flv",
        ".wmv",
        ".m4v",
        ".MP4",
        ".MOV",
        ".AVI",
    }
    if video_path.suffix not in video_extensions:
        raise ValueError(
            f"File does not appear to be a video file: {vidio_url} (extension: {video_path.suffix}). "
            "Please call getUserMessageTool to ask the user for the correct video file path."
        )

    video_id, _ = upload_video_to_ark(str(video_path))
    time.sleep(6)
    understanding_str = video_understanding(video_id, prompt)
    tools_constants.FULL_VIDEO_UNDERSTANDING_CONTENT = understanding_str
    return understanding_str


@tool
@recorder.record
def image_understanding_tool(image_url: Union[str, List[str]]) -> Dict[str, Any]:
    """
    Use this tool to perform image understanding and return rich textual description(s).
    Args:
        image_url: Single image or list of images. Each can be: local path, image URL (http(s)), or Ark image_id.
    Returns:
        {"results": [{"image": url_or_path, "description": "..."}, ...], "descriptions": ["...", ...]}.
    """
    return image_understanding(image_url)




#Atomic operation
# @tool
# @recorder.record
# def Multi_model_understanding_tool(
#     video_understanding_result: str,
#     image_understanding_result: Dict[str, Any],
#     InitUserMessage: str,
# ) -> Dict[str, Any]:
#     """
#     Use the results of video_understanding_tool and image_understanding_tool to judge whether InitUserMessage is ambiguous.
#     Call this only after you have already called video_understanding_tool and image_understanding_tool; pass their return values here.
#     If the result has is_ambiguous=true, you MUST call getUserMessageTool (pass InitUserMessage yourself from context; user will fill clarified_user_info).
#     Args:
#         video_understanding_result: The string returned by video_understanding_tool (full-video description).
#         image_understanding_result: The dict returned by image_understanding_tool (with "results" and "descriptions").
#         InitUserMessage: The user's editing instruction to be checked for clarity.
#     Returns:
#         {"is_ambiguous": bool, "reason": str, "suggestion": str}. When is_ambiguous is true, call getUserMessageTool.
#     """
#     return judge_edit_prompt_clarity(
#         video_understanding_result, image_understanding_result, InitUserMessage
#     )

@tool
@recorder.record
def Multi_model_understanding_tool(
    video_understanding_result: Optional[str] = None,
    image_understanding_result: Optional[Dict[str, Any]] = None,
    prompt: str = "",
) -> Any:
    """
    Use multimodal understanding outputs to perform analysis according to the user's prompt.
    Call this only after you have already called video_understanding_tool and image_understanding_tool.
    Args:
        video_understanding_result: Optional text returned by video_understanding_tool.
        image_understanding_result: Optional dict returned by image_understanding_tool.
        prompt: The user's input prompt to be analyzed.
    Returns:
        Return format is determined by the user's prompt.
    """
    prompt_text = (prompt or "").strip()
    if not prompt_text:
        raise ValueError("prompt is empty; please provide a user prompt for multimodal analysis.")
    video_text = (video_understanding_result or "").strip()
    image_result = image_understanding_result or {"results": [], "descriptions": []}
    return multimodal_analysis(
        video_text,
        image_result,
        prompt_text,
    )




##理解+执行prompt来拆解这个工具实现的功能
# @tool
# @recorder.record
# def analyze_segment_relevance_tool(
#     segment_list: List[Dict[str, Any]], InitUserMessage: str
# ) -> List[Dict[str, Any]]:
#     """
#     Analyze the relevance of the video segments,based on the InitUserMessage, check whether each segmented clip requires editing. If a clip needs editing, add a detailed description of the required modifications; if no editing is needed, leave the clip modification details empty.
#     Args:
#         segment_list: The list of segments. MUST be the return value of video_split_tool (each segment must have segment_name, the local path to the cut video file). Do not pass the output of detect_and_resegment_shots_tool or video_understanding_tool.
#         InitUserMessage: The prompt to analyze the segments.
#     Returns:
#     """
#     print(f"Analyzing segment relevance with InitUserMessage: {InitUserMessage}")
#     return analyze_segment_relevance(segment_list, InitUserMessage)




__all__ = [
    "video_understanding_tool",
    "image_understanding_tool",
    "Multi_model_understanding_tool",
    # "analyze_segment_relevance_tool",
]
