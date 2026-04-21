import os
from volcenginesdkarkruntime import Ark
import time
from openai import OpenAI
from typing import List, Dict, Any, Union, Optional
from .. import constants as tools_constants
import requests
import tempfile
import json
from utils.story_gen_tools import extract_script_entities

from prompts import ANALYZE_SEGMENT_RELEVANCE_PROMPT

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
    use_prompt = prompt if prompt is not None else tools_constants.IMAGE_UNDERSTANDING_PROMPT
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

    prompt_text = tools_constants.JUDGE_EDIT_PROMPT_CLARITY_PROMPT.format(
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


def multimodal_analysis(
    video_understanding_result: str,
    image_understanding_result: Dict[str, Any],
    prompt: str,
) -> Any:
    """
    根据用户 prompt 对视频/图片理解结果做综合多模态分析。
    输出格式不做固定限制，尽量严格按用户 prompt 要求返回。
    """
    video_text = (video_understanding_result or "").strip() or "（无视频描述）"
    desc_list = (
        image_understanding_result.get("descriptions")
        if isinstance(image_understanding_result, dict)
        else []
    )
    if not desc_list and isinstance(image_understanding_result, dict):
        desc_list = [
            r.get("description", "")
            for r in image_understanding_result.get("results", [])
            if isinstance(r, dict)
        ]
    image_descriptions = (
        "\n".join(
            [f"参考图{i+1}: {d or '（无描述）'}" for i, d in enumerate(desc_list)]
        )
        or "（无参考图描述）"
    )
    user_prompt = (prompt or "").strip() or "（无用户prompt）"

    analysis_prompt = f"""
你是一个视频编辑多模态分析助手。请严格围绕“用户prompt”进行分析，结合视频与参考图描述输出最终结果。
输出格式不要自作主张，请严格遵循用户prompt对格式、字段、语气和细节粒度的要求。

【用户prompt】
{user_prompt}

【视频理解结果】
{video_text}

【图片理解结果】
{image_descriptions}

请直接输出最终内容本体，不要附加多余解释。
""".strip()

    client = OpenAI(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ.get("ARK_API_KEY"),
    )
    try:
        response = client.responses.create(
            model="doubao-seed-1-8-251228",
            input=[{"role": "user", "content": [{"type": "input_text", "text": analysis_prompt}]}],
        )
        out = response.output or []
        text = ""
        if len(out) > 1 and getattr(out[1], "content", None):
            content = out[1].content
            if content and hasattr(content[0], "text"):
                text = (content[0].text or "").strip()
        if text:
            return text
    except Exception as e:
        print(f"multimodal_prompt_analysis 调用失败: {e}")

    return "多模态分析接口异常，请重试。"


# ==============Analyze Segment Relevance==================


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
    relevant_prompt = ANALYZE_SEGMENT_RELEVANCE_PROMPT.format(InitUserMessage=InitUserMessage, full_video_understanding_content=tools_constants.FULL_VIDEO_UNDERSTANDING_CONTENT or "")
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
        if not segment_name and tools_constants.RUN_CONTEXT_VIDEO_PATH and segment_id is not None:
            video_dir = os.path.dirname(tools_constants.RUN_CONTEXT_VIDEO_PATH)
            base_name = os.path.splitext(os.path.basename(tools_constants.RUN_CONTEXT_VIDEO_PATH))[0]
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
