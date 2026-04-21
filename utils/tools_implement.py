import base64
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
import requests
import ast
import glob
from scenedetect import FrameTimecode
from scenedetect.scene_manager import SceneList
from scenedetect import detect, ContentDetector, split_video_ffmpeg
from volcenginesdkarkruntime import Ark
from openai import OpenAI
from typing import Any, Dict, List, Union, Optional

from prompts import ANALYZE_SEGMENT_RELEVANCE_PROMPT
from utils.video_utils import get_video_metadata_ffprobe
from typing import Tuple
from moviepy import VideoFileClip, concatenate_videoclips, ColorClip, CompositeVideoClip

FULL_VIDEO_UNDERSTANDING_CONTENT = None
RUN_CONTEXT_IMAGE_PATHS = None
RUN_CONTEXT_VIDEO_PATH = None


def upload_video_to_ark(video_path):
    client = Ark(api_key=os.environ.get("ARK_API_KEY"))
    file = client.files.create(
        file=open(video_path, "rb"),
        purpose="user_data",
        preprocess_configs={
            "video": {
                "fps": 1.0,  # define the sampling fps of the video, default is 1.0
            }
        },
    )
    return file.id, file.filename


def video_understanding(video_id, prompt, max_retries: int = 15, wait_seconds: int = 5):
    """Call video understanding API. If file is still in 'processing' (403), wait and retry."""
    client = OpenAI(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ.get("ARK_API_KEY"),
    )
    for attempt in range(max_retries):
        try:
            response = client.responses.create(
                model="doubao-seed-1-8-251228",
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_video", "file_id": video_id},
                            {"type": "input_text", "text": prompt},
                        ],
                    }
                ],
            )
            return response.output[1].content[0].text
        except Exception as e:
            err_str = str(e).lower()
            if "invalid state" in err_str and "processing" in err_str:
                if attempt == max_retries - 1:
                    raise
                time.sleep(wait_seconds)
                continue
            raise

# ========== 图像理解用的默认 prompt（宽松版）：不限制长度与格式，返回尽可能详细的描述==========
#========== image_understanding_tool==========
# 图像理解用的默认 prompt（宽松版）：不限制长度与格式，返回尽可能详细的描述
IMAGE_UNDERSTANDING_PROMPT = (
    "请详细描述这张图片的内容，可包括人物、场景、物体、动作、颜色、布局、氛围等。"
    "根据需要自由描述，无需限制长度或格式，直接输出自然语言描述即可。"
)


def _image_understanding_core(image_id: str, prompt: str) -> str:
    """内部：调用多模态 API 对已上传到 Ark 的图片进行理解，返回文字描述。"""
    client = OpenAI(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ.get("ARK_API_KEY"),
    )
    try:
        response = client.responses.create(
            model="doubao-seed-1-8-251228",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "file_id": image_id},
                        {"type": "input_text", "text": prompt},
                    ],
                }
            ],
        )
        out = response.output or []
        text = ""
        if len(out) > 1 and getattr(out[1], "content", None):
            content = out[1].content
            if content and hasattr(content[0], "text"):
                text = content[0].text or ""
        return (text or "").strip()
    except Exception as e:
        print(f"image_understanding 失败 (image_id={image_id[:20]}...): {e}")
        return ""


def _resolve_image_to_ark_id(image_input: str) -> str:
    """将图片输入（本地路径或 URL）解析为 Ark image_id；若已是 id 则直接返回。"""
    s = (image_input or "").strip()
    if not s:
        raise ValueError("image_input 为空")
    if "/" in s or s.startswith("http://") or s.startswith("https://"):
        if s.startswith("http://") or s.startswith("https://"):
            r = requests.get(s, timeout=30)
            r.raise_for_status()
            ext = ".jpg"
            for e in [".png", ".jpeg", ".webp", ".gif"]:
                if e in s.lower() or (r.headers.get("content-type") or "").lower().find(e[1:]) >= 0:
                    ext = e
                    break
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as f:
                f.write(r.content)
                path = f.name
            try:
                image_id, _ = _upload_single_image_to_ark(path)
                return image_id
            finally:
                try:
                    os.unlink(path)
                except Exception:
                    pass
        else:
            image_id, _ = _upload_single_image_to_ark(s)
            return image_id
    return s


def image_understanding(
    image_input: Union[str, List[str]],
    prompt: Optional[str] = None,
) -> Union[str, Dict[str, Any]]:
    """
    对图片进行理解，返回文字描述。
    参数：
      - image_input: 单张或列表。每项可为：Ark 的 image_id，或本地路径，或图片 URL。
      - prompt: 可选；不传则使用默认宽松 prompt，描述内容与长度不受限。
    返回：
      - 单张时返回 str（描述）；
      - 多张时返回 {"results": [{"image": item, "description": desc}, ...], "descriptions": [desc, ...]}。
    """
    use_prompt = prompt if prompt is not None else IMAGE_UNDERSTANDING_PROMPT
    if isinstance(image_input, list):
        results = []
        descriptions = []
        for item in image_input:
            item = (item or "").strip()
            if not item:
                results.append({"image": item, "description": ""})
                descriptions.append("")
                continue
            try:
                image_id = _resolve_image_to_ark_id(item)
                desc = _image_understanding_core(image_id, use_prompt)
            except Exception as e:
                print(f"image_understanding 解析或理解失败 (image={item[:50]}...): {e}")
                desc = ""
            results.append({"image": item, "description": desc})
            descriptions.append(desc)
        return {"results": results, "descriptions": descriptions}
    else:
        image_input = (image_input or "").strip()
        if not image_input:
            return ""
        try:
            image_id = _resolve_image_to_ark_id(image_input)
            return _image_understanding_core(image_id, use_prompt)
        except Exception as e:
            print(f"image_understanding 解析或理解失败 (image={image_input[:50]}...): {e}")
            return ""


def _upload_single_image_to_ark(image_path: str):
    """上传单张图片到 Ark，返回 (image_id, image_filename)。"""
    client = Ark(api_key=os.environ.get("ARK_API_KEY"))
    file = client.files.create(
        file=open(image_path, "rb"),
        purpose="user_data",
    )
    return file.id, file.filename

#========== Multi_model_understanding_tool==========
#   多模态理解：根据视频理解、图片理解结果与 InitUserMessage 判断 InitUserMessage 是否模糊
JUDGE_EDIT_PROMPT_CLARITY_PROMPT = """你是一个视频编辑指令审核助手。请根据下面三部分内容，判断「编辑指令」是否足够清晰、可执行。

## 视频内容描述
{video_understanding}

## 参考图描述（按顺序）
{image_descriptions}

## 编辑指令（InitUserMessage）
{InitUserMessage}

请判断：该编辑指令是否模糊或不明确？（例如：未说明要改视频中的谁、未说明参考哪张图、未说明改成什么样子、指令过短如「改一下」等。）
仅输出一个 JSON 对象，不要其他文字，格式如下：
{{"is_ambiguous": true或false, "reason": "简短理由", "suggestion": "若模糊则建议用户补充说明的内容，否则可为空字符串"}}
"""


def judge_edit_prompt_clarity(
    video_understanding_result: str,
    image_understanding_result: Dict[str, Any],
    InitUserMessage: str,
) -> Dict[str, Any]:
    """
    根据视频理解结果、图片理解结果与 InitUserMessage，判断编辑指令是否模糊。
    仅使用前两个工具得到的文本/结构化结果，不再次上传视频或图片。
    返回 {"is_ambiguous": bool, "reason": str, "suggestion": str}，若解析失败则 is_ambiguous 默认为 True（建议请求用户输入）。
    """
    video_text = (video_understanding_result or "").strip() or "（无视频描述）"
    desc_list = image_understanding_result.get("descriptions") if isinstance(image_understanding_result, dict) else []
    if not desc_list and isinstance(image_understanding_result, dict):
        desc_list = [r.get("description", "") for r in image_understanding_result.get("results", [])]
    image_descriptions = "\n".join([f"参考图{i+1}: {d or '（无描述）'}" for i, d in enumerate(desc_list)]) or "（无参考图描述）"
    init_msg = (InitUserMessage or "").strip() or "（无编辑指令）"

    prompt_text = JUDGE_EDIT_PROMPT_CLARITY_PROMPT.format(
        video_understanding=video_text,
        image_descriptions=image_descriptions,
        InitUserMessage=init_msg,
    )
    client = OpenAI(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ.get("ARK_API_KEY"),
    )
    try:
        response = client.responses.create(
            model="doubao-seed-1-8-251228",
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt_text}]}],
        )
        out = response.output or []
        text = ""
        if len(out) > 1 and getattr(out[1], "content", None):
            content = out[1].content
            if content and hasattr(content[0], "text"):
                text = (content[0].text or "").strip()
        if text:
            # 尝试从返回中提取 JSON
            try:
                # 允许被 markdown 代码块包裹
                if "```" in text:
                    parts = text.split("```")
                    for p in parts:
                        p = p.strip()
                        if p.startswith("json"):
                            p = p[4:].strip()
                        if p.startswith("{"):
                            obj = json.loads(p)
                            return {
                                "is_ambiguous": bool(obj.get("is_ambiguous", True)),
                                "reason": str(obj.get("reason", "")),
                                "suggestion": str(obj.get("suggestion", "")),
                            }
                obj = json.loads(text)
                return {
                    "is_ambiguous": bool(obj.get("is_ambiguous", True)),
                    "reason": str(obj.get("reason", "")),
                    "suggestion": str(obj.get("suggestion", "")),
                }
            except (json.JSONDecodeError, ValueError):
                pass
    except Exception as e:
        print(f"judge_edit_prompt_clarity 调用失败: {e}")
    return {"is_ambiguous": True, "reason": "判断接口异常，建议向用户确认编辑指令", "suggestion": "请明确说明要修改视频中的谁、参考哪张图、改成什么样子。"}



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

# ==========视频逻辑划分函数，保证scence_list非空==========
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


# ========== 视频逻辑划分检测具体实现，检测划分片段的长度是否符合要求，并返回场景列表==========

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


# ========== 实际视频划分实现==========
from scenedetect import detect, ContentDetector
from scenedetect.video_splitter import (
    split_video_ffmpeg,
    default_formatter,
    SceneMetadata,
    VideoMetadata,
)
from pathlib import Path

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



# ==========视频片段相关性分析==========

def analyze_segment_relevance(
    segment_list: List[Dict[str, Any]], InitUserMessage: str
) -> List[Dict[str, Any]]:
    """
    Analyze relevance of video segments by uploading each segment and calling video understanding API.

    Args:
        segment_list (List[Dict[str, Any]]): List of segment dictionaries, each containing:
            - segment_id (int): Unique identifier for the segment
            - segment_name (str): Path to the segment video file
        InitUserMessage (str): Prompt text for video understanding analysis (user's initial edit instruction).

    Returns:
        List[Dict[str, Any]]: Updated segment list with file_id added to each segment:
            [
                {
                    "segment_id": 1,
                    "segment_name": "rawdata/10/1月12日(12)-1_segment_1.mp4",
                    "file_id": "file_xxx",  # Cloud file ID after upload
                    "main_subject": "...",
                    "orientation": "...",
                    "event": "...",
                    "editing_required": True,
                    "editing_prompt": "...",
                },
                ...
            ]"""
    relevant_prompt = ANALYZE_SEGMENT_RELEVANCE_PROMPT.format(InitUserMessage=InitUserMessage, full_video_understanding_content=FULL_VIDEO_UNDERSTANDING_CONTENT or "")
    print('\n')
    print(f"relevant_prompt: {relevant_prompt}")
    print('\n')
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    updated_segments = []
    # upload video to ark
    for segment in segment_list:
        segment_id = segment.get("segment_id")
        segment_name = segment.get("segment_name")
        # If model passed the output of detect_and_resegment_shots_tool (no segment_name), infer from RUN_CONTEXT_VIDEO_PATH
        if not segment_name and RUN_CONTEXT_VIDEO_PATH and segment_id is not None:
            video_dir = os.path.dirname(RUN_CONTEXT_VIDEO_PATH)
            base_name = os.path.splitext(os.path.basename(RUN_CONTEXT_VIDEO_PATH))[0]
            for pattern in (
                f"{base_name}_segment_{segment_id}.mp4",
                f"{base_name}-Scene-{segment_id:03d}.mp4",
            ):
                inferred = os.path.join(video_dir, pattern)
                if os.path.exists(inferred):
                    segment_name = inferred
                    segment["segment_name"] = inferred
                    break
        if not segment_name:
            print(f"Warning: segment {segment_id} has no segment_name, skipping...")
            continue
        # 相对路径时尝试基于项目根解析，便于 upload 与 exists 检查
        if not os.path.isabs(segment_name) and not os.path.exists(segment_name):
            abs_path = os.path.join(project_root, segment_name)
            if os.path.exists(abs_path):
                segment_name = abs_path
        if not os.path.exists(segment_name):
            print(
                f"Warning: Video file not found for segment {segment_id}: {segment_name}, skipping..."
            )
            continue

        try:
            # Step 1: Upload video to ARK
            print(f"Uploading segment {segment_id}: {segment_name}")
            file_id, file_filename = upload_video_to_ark(segment_name)
            print(f"Uploaded successfully, file_id: {file_id}")

            # Step 2: Create updated segment dictionary and add file_id
            updated_segment = segment.copy()
            updated_segment["file_id"] = file_id
            updated_segments.append(updated_segment)

        except Exception as e:
            error_msg = str(e)
            print(f"Error processing segment {segment_id}: {error_msg}")
            # Still add the segment without file_id if processing fails
            updated_segment = segment.copy()
            updated_segment["file_id"] = None
            updated_segments.append(updated_segment)

    # Wait for Ark to finish processing uploaded files before first API call
    if updated_segments and any(s.get("file_id") for s in updated_segments):
        time.sleep(8)

    # call video understanding api
    for segment in updated_segments:
        segment_id = segment.get("segment_id")
        file_id = segment.get("file_id")
        if not file_id:
            print(f"Warning: segment {segment_id} has no file_id, skipping...")
            continue
        try:
            print(f"Analyzing segment {segment_id} with video understanding API...")
            #TODO try to pass full video message to guide the video understanding api
            relevant_result = video_understanding(file_id, relevant_prompt)
            relevant_result = json.loads(relevant_result)
            # Extract fields from the result (result may be a list or dict)
            if isinstance(relevant_result, list) and len(relevant_result) > 0:
                result_item = relevant_result[0]
            else:
                result_item = relevant_result
            segment["event"] = result_item.get("event", "")
            segment["main_subject"] = result_item.get("main_subject", "")
            segment["orientation"] = result_item.get("orientation", "")
            segment["editing_required"] = result_item.get(
                "editing_required", result_item.get("editing required", False)
            )
            segment["editing_prompt"] = result_item.get("editing_prompt", "")
            print(f"Analysis completed for segment {segment_id}")
        except Exception as e:
            error_msg = str(e)
            print(f"Error analyzing segment {segment_id}: {error_msg}")
            segment["event"] = ""
            segment["main_subject"] = ""
            segment["orientation"] = ""
            segment["editing_required"] = False
            segment["editing_prompt"] = ""
    print(f"Updated segments: {updated_segments}")
    return updated_segments


# ========== 视频编辑相关Tools==========
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
    if RUN_CONTEXT_VIDEO_PATH:
        video_dir = os.path.dirname(os.path.abspath(RUN_CONTEXT_VIDEO_PATH))
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
   
