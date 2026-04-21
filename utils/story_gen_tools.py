"""
剧本分析与图生原子工具。

1. extract_script_entities: 从剧本中抽取核心人物与场景描述。
2. generate_image: 根据单条文本和可选参考图生成图片（原子工具），支持组图；main 中可对多个人物/场景分次调用并汇总路径。
"""
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
# 用于规划/抽取的对话模型
CHAT_MODEL = "doubao-seed-2-0-pro-260215"

# 风格后缀与关键词
STYLE_SUFFIX_COMIC = "插画级，线条复杂，色块分明"
STYLE_KEYWORDS = ("动漫", "漫画", "插画", "二次元")

# TOS 配置（用于本地参考图上传为可访问 URL，与 seedance_edit 一致）
TOS_AK = os.getenv("TOS_ACCESS_KEY")
TOS_SK = os.getenv("TOS_SECRET_KEY")
TOS_ENDPOINT = "tos-cn-beijing.volces.com"
TOS_REGION = "cn-beijing"
TOS_BUCKET = "visualgen"


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


# ---------- 工具1：从剧本抽取核心人物与场景 ----------
EXTRACT_ENTITIES_SYSTEM = """你是一个剧本分析专家。用户会给出一段剧本/分镜描述。

你的任务：从剧本中抽取「核心人物」和「场景」两类信息，且只输出一个 JSON 对象，不要其他文字。

要求：
1. 核心人物：列出剧本中的主要角色，每人包含 name（称呼/名字）、description（简短形象或身份描述）。
2. 场景：列出剧本中出现的核心场景，每项为简短描述（时间、地点、环境等）。

输出格式严格为（不要 markdown 代码块包裹，直接输出 JSON）：
{"characters": [{"name": "角色名", "description": "简短描述"}], "scenes": ["场景1描述", "场景2描述", ...]}"""


def extract_script_entities(script: str) -> Dict[str, Any]:
    """
    根据剧本文本，抽取其中的核心人物和场景描述。

    Args:
        script: 剧本/分镜的完整文本。

    Returns:
        包含 "characters" 和 "scenes" 的字典：
        - characters: [{"name": str, "description": str}, ...]
        - scenes: [str, ...]
        若解析失败则返回 {"characters": [], "scenes": []}。
    """
    script = (script or "").strip()
    if not script:
        return {"characters": [], "scenes": []}

    client = Ark(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.getenv("ARK_API_KEY"),
    )
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": EXTRACT_ENTITIES_SYSTEM},
            {"role": "user", "content": script},
        ],
        temperature=0.3,
    )
    content = (resp.choices[0].message.content or "").strip()
    if "```" in content:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if match:
            content = match.group(1).strip()
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            return {"characters": [], "scenes": []}
        characters = data.get("characters")
        scenes = data.get("scenes")
        if not isinstance(characters, list):
            characters = []
        if not isinstance(scenes, list):
            scenes = []
        return {"characters": characters, "scenes": scenes}
    except json.JSONDecodeError:
        return {"characters": [], "scenes": []}


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
            #TODO: 下载图片有可能失败，改为多次重试
            urllib.request.urlretrieve(url, filepath)
            saved.append(filepath)
        except Exception:
            pass
    return saved


# if __name__ == "__main__":
#     if not os.getenv("ARK_API_KEY"):
#         print("错误: 未设置环境变量 ARK_API_KEY，请先设置后再运行。", flush=True)
#         sys.exit(1)

#     # 参数在 main 内填写，不使用命令行参数
#     script_path = os.path.join(
#         os.path.dirname(__file__),
#         "..",
#         "..",
#         "context-guided-preference-following",
#         "data",
#         "eval",
#         "agent_story",
#         "浮浪记剧本.txt",
#     )
#     project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#     output_dir = os.path.join(project_root, "output", "story_gen")

#     if not os.path.exists(script_path):
#         print(f"错误: 剧本文件不存在: {script_path}", flush=True)
#         sys.exit(1)

#     with open(script_path, "r", encoding="utf-8") as f:
#         script = f.read()

#     entities = extract_script_entities(script)
#     print("抽取的人物:", json.dumps(entities.get("characters", []), ensure_ascii=False, indent=2))
#     print("抽取的场景:", json.dumps(entities.get("scenes", []), ensure_ascii=False, indent=2))

#     all_paths: List[str] = []
#     for c in entities.get("characters", []):
#         name = (c.get("name") or "").strip()
#         desc = (c.get("description") or "").strip()
#         text = f"{name} {desc}".strip() if name else desc
#         if not text:
#             continue
#         paths = generate_image(
#             text=text,
#             output_dir=output_dir,
#             filename_prefix=f"char_{name}" if name else "char",
#         )
#         all_paths.extend(paths)
#         for path in paths:
#             print(f"  角色 [{name}] -> {path}", flush=True)

#     for i, scene_desc in enumerate(entities.get("scenes", []), start=1):
#         text = (scene_desc if isinstance(scene_desc, str) else str(scene_desc)).strip()
#         if not text:
#             continue
#         paths = generate_image(
#             text=text,
#             output_dir=output_dir,
#             filename_prefix=f"scene_{i}",
#         )
#         all_paths.extend(paths)
#         for path in paths:
#             print(f"  场景 [{i}] -> {path}", flush=True)

#     print("所有生成的图片路径:", all_paths)


if __name__ == "__main__":
    output_images = generate_image(
        text="基于参考图绘制一个Q版简约手绘小人，脸瘦一点，粗黑毛躁的手绘轮廓线，面部仅用两个黑色方块代表眼睛，无其他五官细节；色彩平涂为主，少量色块区分体积，整体呈现模糊随性的涂鸦质感；人物居中，纯白背景，风格呆萌简洁，保留角色动态姿势。",
        output_dir="workspace/output/test_story_gen_tools",
        filename_prefix="test_story_gen_tools",
        style_suffix="插画级，线条复杂，色块分明",
        style_keywords=("动漫", "漫画", "插画", "二次元"),
        max_images=1,
        size="2K",
        reference_image=["/root/work/temp/multi-shot-multi-object-long-video-edit/workspace/output/test_story_gen_tools/test_story_gen_tools_20260316_130700.png"],
    )
    print(output_images)