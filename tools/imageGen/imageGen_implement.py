from agno.tools import tool
from utils.trace_recorder import recorder
import os
import re
import sys
import json
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Union


from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime.types.images.images import SequentialImageGenerationOptions

# 图生模型
IMAGE_MODEL = "doubao-seedream-5-0-260128"


# 风格后缀与关键词
STYLE_SUFFIX_COMIC = "插画级，线条复杂，色块分明"
STYLE_KEYWORDS = ("动漫", "漫画", "插画", "二次元")

# TOS 配置（用于本地参考图上传为可访问 URL，与 seedance_edit 一致）
TOS_AK = os.getenv("TOS_ACCESS_KEY")
TOS_SK = os.getenv("TOS_SECRET_KEY")
TOS_ENDPOINT = "tos-cn-beijing.volces.com"
TOS_REGION = "cn-beijing"
TOS_BUCKET = "visualgen"

def _download_with_retry(
    url: str,
    filepath: str,
    max_retries: int = 3,
    base_delay_s: float = 1.0,
) -> bool:
    """
    下载图片并重试，避免短暂网络抖动导致单次下载失败。
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            urllib.request.urlretrieve(url, filepath)
            return True
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                # 线性退避：1s, 2s, 3s...
                time.sleep(base_delay_s * attempt)
    if last_err is not None:
        print(f"  [storyAssetTools] 下载失败，已重试 {max_retries} 次: {last_err}", flush=True)
    return False

def _upload_file_to_tos(
    local_path: str,
    remote_prefix: str = "story_gen_images",
    expires: int = 72000,
) -> str:
    """上传本地文件到 TOS 并返回签名 URL，供 API 访问。参考 seedance_edit 的 TOS 上传逻辑。"""
    try:
        import tos
        from tos import HttpMethodType
    except ImportError:
        raise ImportError("使用 TOS 上传请安装: pip install tos")

    if not TOS_AK or not TOS_SK:
        raise ValueError("参考图为本地路径时需设置环境变量 TOS_ACCESS_KEY 和 TOS_SECRET_KEY")

    if not os.path.exists(local_path):
        raise FileNotFoundError(f"文件不存在: {local_path}")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = os.path.basename(local_path)
    remote_path = f"{remote_prefix}/{timestamp}_{filename}"

    client = tos.TosClientV2(TOS_AK, TOS_SK, TOS_ENDPOINT, TOS_REGION)
    file_size = os.path.getsize(local_path)
    if file_size > 20 * 1024 * 1024:
        client.upload_file(TOS_BUCKET, remote_path, local_path)
    else:
        client.put_object_from_file(TOS_BUCKET, remote_path, local_path)

    output = client.pre_signed_url(
        HttpMethodType.Http_Method_Get,
        TOS_BUCKET,
        remote_path,
        expires=expires,
    )
    return output.signed_url


def _resolve_reference_image_urls(reference_image: Union[str, List[str]]) -> List[str]:
    """将参考图入参（本地路径或 URL）规范为 URL 列表。本地路径会上传至 TOS 后返回签名 URL。"""
    if isinstance(reference_image, str):
        reference_image = [reference_image]
    urls: List[str] = []
    for item in reference_image or []:
        s = (item or "").strip()
        if not s:
            continue
        if s.startswith("http://") or s.startswith("https://"):
            urls.append(s)
        else:
            try:
                url = _upload_file_to_tos(s)
                urls.append(url)
            except Exception as e:
                print(f"  [story_gen_tools] 参考图上传跳过 {s[:50]}...: {e}", flush=True)
    return urls



# ---------- 工具2：图生（文本 + 可选图像参考 + 可选组图，原子工具）----------
def generate_image(
    text: str,
    reference_image: Optional[Union[str, List[str]]] = None,
    output_dir: Optional[str] = None,
    size: str = "2K",
    max_images: Optional[int] = None,
    filename_prefix: Optional[str] = None,
    style_suffix: Optional[str] = None,
    style_keywords: Optional[tuple] = None,
) -> List[str]:
    """
    根据单条文本和可选参考图生成一张或多张图片（原子工具）。

    Args:
        text: 图生提示词/描述（必填）。
        reference_image: 可选。参考图：单张或列表，每项为本地路径或 URL；本地文件会上传至 TOS 得到可访问 URL 后传入 API。
        output_dir: 图片保存目录，不传则使用当前脚本所在目录。
        size: 图生尺寸，默认 "2K"。
        max_images: 可选。大于 1 时启用组图，一次生成多张图并全部落盘。
        filename_prefix: 可选。输出文件名前缀，便于区分多次调用；组图时保存为 prefix_1.png, prefix_2.png ...
        style_suffix: 若提示词中无风格关键词则追加此后缀；不传则用默认。
        style_keywords: 判断是否已含风格的关键词元组；不传则用默认。

    Returns:
        保存后的本地图片路径列表；单张为 [path]，多张为 [path1, path2, ...]；失败返回 []。
    """
    p = (text or "").strip()
    if not p:
        return []

    suffix = style_suffix if style_suffix is not None else STYLE_SUFFIX_COMIC
    keywords = style_keywords if style_keywords is not None else STYLE_KEYWORDS
    if not any(kw in p for kw in keywords):
        p = p + suffix

    image_urls: List[str] = []
    if reference_image:
        image_urls = _resolve_reference_image_urls(reference_image)

    client = Ark(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.getenv("ARK_API_KEY"),
    )
    current_dir = os.path.dirname(os.path.abspath(__file__))
    save_dir = output_dir if output_dir else current_dir
    os.makedirs(save_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prefix = re.sub(r"[^\w\-]", "_", (filename_prefix or "").strip())[:50]

    api_params: Dict[str, Any] = {
        "model": IMAGE_MODEL,
        "prompt": p,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if image_urls:
        api_params["image"] = image_urls
    if max_images is not None and max_images > 1:
        api_params["sequential_image_generation"] = "auto"
        api_params["sequential_image_generation_options"] = SequentialImageGenerationOptions(
            max_images=max_images
        )

    try:
        resp = client.images.generate(**api_params)
    except Exception:
        return []

    saved: List[str] = []
    for i, item in enumerate(resp.data or [], start=1):
        url = getattr(item, "url", None)
        if not url:
            continue
        if len(resp.data) > 1:
            basename = f"{safe_prefix}_{i}.png" if safe_prefix else f"gen_{timestamp}_{i}.png"
        else:
            basename = f"{safe_prefix}_{timestamp}.png" if safe_prefix else f"gen_{timestamp}.png"
        filepath = os.path.join(save_dir, basename)
        try:
            if _download_with_retry(url, filepath, max_retries=3, base_delay_s=1.0):
                saved.append(filepath)
        except Exception:
            pass
    return saved

# ========== Tools (agno @tool) ==========

@tool
@recorder.record
def generate_image_tool(
    text: str,
    reference_image: Optional[Union[str, List[str]]] = None,
    output_dir: Optional[str] = None,
    size: str = "2K",
    max_images: Optional[int] = None,
    filename_prefix: Optional[str] = None,
    style_suffix: Optional[str] = None,
    style_keywords: Optional[tuple] = None,
) -> List[str]:
    """
    Generate images based on text and optional reference image.
    Args:
        text: The text prompt/description for image generation (required).
        reference_image: Optional reference image(s) for generation. Can be a single path/URL or a list. Local paths will be uploaded to TOS.
        output_dir: The directory to save the generated images. Defaults to current script directory.
        size: The size of the generated images, default "2K".
        max_images: Optional. When greater than 1, enables batch generation to produce multiple images at once.
        filename_prefix: Optional. Prefix for output filenames to distinguish multiple calls. For batch generation, files are saved as prefix_1.png, prefix_2.png, etc.
        style_suffix: Optional. Suffix appended to prompt if no style keywords are present. Defaults to "插画级，线条复杂，色块分明".
        style_keywords: Optional. Tuple of keywords to check if prompt already contains style info. Defaults to ("动漫", "漫画", "插画", "二次元").
    Returns: The list of generated image paths. Single image returns [path], multiple images return [path1, path2, ...]. Returns [] on failure.
    """
    return generate_image(
        text=text,
        reference_image=reference_image,
        output_dir=output_dir,
        size=size,
        max_images=max_images,
        filename_prefix=filename_prefix,
        style_suffix=style_suffix,
        style_keywords=style_keywords,
    )