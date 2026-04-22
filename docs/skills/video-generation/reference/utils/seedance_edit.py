"""
Seedance2Edit 视频编辑工具

基于火山引擎 Ark content_generation API，接口与 tools_implement.edit_video 兼容。
传入本地视频路径、参考图路径和编辑提示词，完成视频编辑并返回输出视频与关键帧路径。
视频通过 TOS 上传，时长由 get_video_metadata_ffprobe 获取。

API 限制：参考视频不少于 2s，输出视频不少于 4s。
本实现通过扩展/裁剪使上层用户无感知，输出时长始终与输入参考视频时长一致。

环境变量:
- ARK_API_KEY: Ark API 密钥
- TOS_ACCESS_KEY, TOS_SECRET_KEY: TOS 上传所需
"""

import math
import os
import subprocess
import tempfile
import time
import base64
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple, Union

from volcenginesdkarkruntime import Ark

from utils.video_utils import get_video_metadata_ffprobe

# Seedance API 限制：视频像素数 ≤ 927408
MAX_VIDEO_PIXELS = 927408

# TOS 配置
TOS_AK = os.getenv("TOS_ACCESS_KEY")
TOS_SK = os.getenv("TOS_SECRET_KEY")
TOS_ENDPOINT = "tos-cn-beijing.volces.com"
TOS_REGION = "cn-beijing"
TOS_BUCKET = "visualgen"


def _encode_file_to_base64(file_path: str) -> str:
    """将本地文件编码为 base64 格式"""
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _get_mime_type(file_path: str) -> str:
    """根据文件扩展名获取 MIME 类型"""
    ext = Path(file_path).suffix.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
    }
    return mime_types.get(ext, "application/octet-stream")


def _upload_video_to_tos(
    local_path: str, remote_path: Optional[str] = None, expires: int = 72000
) -> Optional[str]:
    """上传视频到 TOS 并返回签名 URL"""
    try:
        import tos
        from tos import HttpMethodType
    except ImportError:
        raise ImportError("使用 TOS 上传请安装: pip install tos")

    if not TOS_AK or not TOS_SK:
        raise ValueError("请设置环境变量 TOS_ACCESS_KEY 和 TOS_SECRET_KEY")

    if not os.path.exists(local_path):
        raise FileNotFoundError(f"视频文件不存在: {local_path}")

    if remote_path is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(local_path)
        remote_path = f"videos/{timestamp}_{filename}"

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


def _download_output_video(url: str, output_path: str) -> None:
    """下载任务输出视频到本地"""
    urllib.request.urlretrieve(url, output_path)


def _crop_video(video_path: str, duration: float) -> None:
    """裁剪视频到指定时长。先写临时文件再替换，避免覆盖原文件。"""
    parent = Path(video_path).resolve().parent
    fd, temp_path = tempfile.mkstemp(suffix=".mp4", dir=str(parent))
    os.close(fd)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-t",
                str(duration),
                "-c",
                "copy",
                temp_path,
            ],
            check=True,
            capture_output=True,
        )
        os.replace(temp_path, video_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _extend_video(video_path: str, target_duration: int) -> str:
    """
    拓展视频到指定时长（通过循环播放）。不覆盖原文件。
    返回扩展后视频的临时文件路径，调用方负责在完成后删除。
    """
    fd, temp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                video_path,
                "-t",
                str(target_duration),
                "-c",
                "copy",
                temp_path,
            ],
            check=True,
            capture_output=True,
        )
        return temp_path
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _extract_first_frame(video_path: str, keyframe_path: str) -> None:
    """从视频提取第一帧作为关键帧"""
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-q:v", "2", keyframe_path],
        check=True,
        capture_output=True,
    )


def _extract_last_frame(video_path: str, output_path: str) -> None:
    """
    从视频提取最后一帧，保存为图片。用于多段纯生成时，将上一段结尾画面作为下一段的参考图以提升连贯性。
    调用方负责在完成后删除 output_path（若为临时文件）。
    """
    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"视频不存在: {video_path}")
    # 使用绝对路径，避免 cwd 导致输出写到别处
    out_abs = os.path.abspath(output_path)

    def try_sseof() -> bool:
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-sseof",
                    "-0.04",
                    "-i",
                    video_path,
                    "-vframes",
                    "1",
                    "-q:v",
                    "2",
                    out_abs,
                ],
                check=True,
                capture_output=True,
            )
            return os.path.exists(out_abs) and os.path.getsize(out_abs) > 0
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def try_ss_seek() -> None:
        meta = get_video_metadata_ffprobe(video_path)
        dur = meta.get("duration_s") or 0
        seek = max(0.0, float(dur) - 0.04)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(seek),
                "-i",
                video_path,
                "-vframes",
                "1",
                "-q:v",
                "2",
                out_abs,
            ],
            check=True,
            capture_output=True,
        )

    if not try_sseof():
        try_ss_seek()
    if not os.path.exists(out_abs) or os.path.getsize(out_abs) == 0:
        raise RuntimeError(f"提取最后一帧失败，输出为空: {output_path}")


def _resize_video_if_needed(
    video_path: str, max_pixels: int = MAX_VIDEO_PIXELS
) -> Tuple[str, Optional[str]]:
    """
    若视频像素数超过限制，缩放到临时文件并返回 (临时路径, 临时路径)；
    否则返回 (原路径, None)。
    调用方负责在完成后删除临时文件。
    """
    meta = get_video_metadata_ffprobe(video_path)
    w, h = meta["width"], meta["height"]
    if w is None or h is None:
        return video_path, None
    if w * h <= max_pixels:
        return video_path, None
    scale = math.sqrt(max_pixels / (w * h))
    new_w = max(2, int(w * scale) // 2 * 2)
    new_h = max(2, int(h * scale) // 2 * 2)
    while new_w * new_h > max_pixels:
        new_w = max(2, new_w - 2)
        new_h = max(2, new_h - 2)
    parent = Path(video_path).resolve().parent
    fd, temp_path = tempfile.mkstemp(suffix=".mp4", dir=str(parent))
    os.close(fd)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-vf",
                f"scale={new_w}:{new_h}",
                "-c:a",
                "copy",
                temp_path,
            ],
            check=True,
            capture_output=True,
        )
        return temp_path, temp_path
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def seedance_generate(
    video_path: Optional[Union[str, List[str]]],
    image_paths: List[str],
    generate_prompt: str,
    video_ratio: str = "16:9",
    video_resolution: str = "480p",
    save_path: str = "output.mp4",
    target_duration_s: Optional[float] = None,
    poll_interval: int = 10,
    max_queued_seconds: int = 600,
) -> Tuple[str, str, str]:
    """
    调用 Seedance2Edit API 进行视频编辑，接口与 edit_video 兼容。

    Args:
        video_path: 参考视频的本地路径，可为单个路径（str）或路径列表（List[str]）；为空（None、空字符串或空列表）时不使用参考视频，仅凭文本与参考图生成，此时必须指定 target_duration_s。多视频时按顺序对应 [视频1]、[视频2] 等。
        image_paths: 参考图本地路径列表（可选），为空时不填充 reference_image；有则顺序对应 [图1]、[图2]、[图3]，可在 user_message 中用 [图N] 引用
        generate_prompt: 生成指令，如 "[图1]的脸换成[图2]的风格，视频卡通风格"
        video_ratio: 输出视频比例，默认 "16:9"
        video_resolution: 输出分辨率，默认 "480p"
        save_path: 结果视频保存路径，默认 "output.mp4"
        target_duration_s: 目标输出时长（秒）。有参考视频时为空则与输入一致；无参考视频时必填。有值则生成该时长（API 至少 4s，不足 4s 会先请求 4s 再裁剪）。
        poll_interval: 轮询任务状态的间隔秒数，默认 10
        max_queued_seconds: 任务持续处于 queued 状态超过此时长（秒）则视为死锁并抛错，默认 300（5 分钟）

    Returns:
        (video_save_path, first_frame_save_path,last_frame_save_path): 保存后的本地路径、第一帧保存路径、最后一帧保存路径；若任务成功但未返回视频则返回 ("", "", "")

    Raises:
        FileNotFoundError: 视频或参考图（若提供）不存在
        ValueError: video_path 为空时未指定 target_duration_s，或 target_duration_s <= 0
        RuntimeError: 任务失败、任务长时间处于 queued 死锁、或 API 调用异常
    """
    # 归一化：支持单个路径或路径列表
    if video_path is None or (
        isinstance(video_path, str) and not str(video_path).strip()
    ):
        video_paths: List[str] = []
    elif isinstance(video_path, str):
        video_paths = [str(video_path).strip()]
    else:
        video_paths = [str(p).strip() for p in video_path if p and str(p).strip()]
    has_reference_video = len(video_paths) > 0
    for p in video_paths:
        if not os.path.exists(p):
            raise FileNotFoundError(f"视频文件不存在: {p}")
    if target_duration_s is not None and target_duration_s <= 0:
        raise ValueError("target_duration_s 需大于 0")
    if not has_reference_video and target_duration_s is None:
        raise ValueError("video_path 为空时必须指定 target_duration_s")
    # 参考图可选：为空时不填充 reference_image 部分 content
    if image_paths:
        for p in image_paths:
            if not os.path.exists(p):
                raise FileNotFoundError(f"参考图不存在: {p}")

    reference_video_content: List[dict] = []
    all_temp_paths: List[Optional[str]] = []
    if has_reference_video:
        # 视频时长由第一个参考视频获取，保留浮点精度用于最终裁剪
        first_meta = get_video_metadata_ffprobe(video_paths[0])
        original_duration = first_meta["duration_s"]
        output_duration = (
            original_duration if target_duration_s is None else target_duration_s
        )
        # API 限制：参考视频至少 2s、输出至少 4s。本实现对上层隐藏这些限制。
        for path in video_paths:
            meta = get_video_metadata_ffprobe(path)
            extended_video_path = None
            if meta["duration_s"] < 2:
                extended_video_path = _extend_video(path, 2)
                path = extended_video_path
                all_temp_paths.append(extended_video_path)
            path, resized_video_path = _resize_video_if_needed(path)
            if resized_video_path:
                all_temp_paths.append(resized_video_path)
            video_url = _upload_video_to_tos(path)
            if not video_url:
                raise RuntimeError("TOS 上传视频失败")
            reference_video_content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": video_url},
                    "role": "reference_video",
                }
            )
        for temp_path in all_temp_paths:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
    else:
        output_duration = target_duration_s

    # 请求时长满足 API 下限 4s，若目标 < 4s 则先请求 4s 再裁剪
    duration = max(4, math.ceil(output_duration))
    need_crop_output = True

    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        raise ValueError("请设置环境变量 ARK_API_KEY")

    # 构建 content 数组：text +（可选）多张 reference_image +（可选）reference_video
    content = [{"type": "text", "text": generate_prompt}]

    # 参考图（可选）：为空时不填充；有则 base64，顺序对应 [图1]、[图2]、[图3]...
    if image_paths:
        for img_path in image_paths:
            image_base64 = _encode_file_to_base64(img_path)
            mime_type = _get_mime_type(img_path)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                    "role": "reference_image",
                }
            )

    # 参考视频（可选）：支持多个，按顺序对应 [视频1]、[视频2]...
    content.extend(reference_video_content)

    # 创建任务
    client = Ark(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=api_key,
    )

    create_result = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-fast-260128",  # doubao-seedance-2-0-260128
        content=content,
        ratio=video_ratio,
        duration=duration,
        resolution=video_resolution,
    )
    task_id = create_result.id

    # 轮询任务状态；持续 queued 超时视为死锁
    queued_since: Optional[float] = None
    while True:
        get_result = client.content_generation.tasks.get(task_id=task_id)
        status = get_result.status

        if status == "queued":
            now = time.time()
            if queued_since is None:
                queued_since = now
            elif now - queued_since >= max_queued_seconds:
                raise RuntimeError(
                    "Seedance2API 任务长时间处于 queued 状态，可能已死锁，请重新提交任务。"
                )
        else:
            queued_since = None

        if status == "succeeded":
            output_video_url = None
            if hasattr(get_result, "content") and get_result.content:
                task_content = get_result.content
                if hasattr(task_content, "video_url"):
                    output_video_url = task_content.video_url
                elif isinstance(task_content, dict) and "video_url" in task_content:
                    output_video_url = task_content["video_url"]

            # 容忍输出为空：未返回视频 URL 时返回空路径，不抛错
            if not output_video_url:
                return "", ""

            video_save_path = save_path
            _download_output_video(output_video_url, video_save_path)
            # 裁剪到目标时长（与输入一致或 target_duration_s 指定值）
            if need_crop_output:
                _crop_video(video_save_path, output_duration)
            # 与 edit_video 兼容：提取第一帧作为关键帧（仅当视频文件存在且路径有效时）
            if os.path.exists(video_save_path):
                # 保证关键帧路径与视频路径不同且带 .jpg，避免纯生成时 save_path 无扩展名导致抽帧输入/输出同路径
                first_frame_save_path = (
                    str(Path(video_save_path).with_suffix("")) + "_firstframe.jpg"
                )
                last_frame_save_path = (
                    str(Path(video_save_path).with_suffix("")) + "_lastframe.jpg"
                )
                _extract_first_frame(video_save_path, first_frame_save_path)
                _extract_last_frame(video_save_path, last_frame_save_path)
            else:
                first_frame_save_path = (
                    str(Path(video_save_path).with_suffix("")) + "_firstframe.jpg"
                )
                last_frame_save_path = (
                    str(Path(video_save_path).with_suffix("")) + "_lastframe.jpg"
                )
            return video_save_path, first_frame_save_path, last_frame_save_path

        elif status == "failed":
            err_msg = getattr(get_result, "error", None) or str(get_result)
            raise RuntimeError(f"Seedance2Edit 任务失败: {err_msg}")

        time.sleep(poll_interval)
        print(f"Seedance2API 任务状态: {status}")


if __name__ == "__main__":
    # 无参考视频时传 None 或 ""，并必须指定 target_duration_s
    video_path = [
        #"/root/work/temp/multi-shot-multi-object-long-video-edit/workspace/output/test/generated_0_edited.mp4",
        #"/root/work/temp/multi-shot-multi-object-long-video-edit/workspace/output/default/generated_seg3_edited.mp4",
    ]  # 或本地路径如 "rawdata/motion/refvideo-Scene-001.mp4"
    image_paths = [
        #"/data/home/hechengxin/data2/ysy/multi-shot-multi-object-long-video-edit/rawdata/motion/refimg.png"
    ]
    generate_prompt = """请你完成这一集剧本的创作。{
      "scene_number": 1,
      "heading": "外 德尔斐镇山坡 - 日",
      "location": "德尔斐镇山坡",
      "time": "日",
      "int_ext": "外",
      "action_lines": [
        "初夏的微风拂过德尔斐镇附近的山坡，嫩绿的草叶轻轻晃动。",
        "平凡少女薇拉仰面躺在草地上，闭着双眼，脸上带着一丝百无聊赖的神情，任由微风拂过她的发丝。",
        "男孩萨奇坐在薇拉身旁的一块石头上，双手撑在身侧，侧头看向薇拉。",
        "薇拉突然以坐位体前屈的姿势猛地坐起身，眼睛亮晶晶的，语气里满是憧憬。",
        "萨奇无奈地翻了个白眼，嘴角却带着笑意。",
        "薇拉垮下脸，又一次抱怨起来。",
        "萨奇摸了摸后脑勺，想起什么似的开口。",
        "薇拉立刻坐直身子，皱着眉反驳。",
        "虽然嘴上这么说，薇拉还是拍了拍身上的草屑，站起身来。",
        "萨奇看了看手腕上的虚拟手表，想起家里的规矩，也站起身准备离开。"
      ]
    }
    [
    {
      "panel_id": 1,
      "scene_id": 1,
      "shot_number": 1,
      "shot_type": "全景",
      "camera_move": "固定",
      "content": "初夏德尔斐镇的山坡全景，嫩绿草坡在微风里泛起层层涟漪，远处可见小镇白顶屋舍的模糊轮廓。薇拉仰面躺在画面中下侧的草地上，萨奇坐在她身旁的浅灰色岩石上，侧头看向她。光线是柔和的正午漫射光，整体色调以暖绿和淡蓝为主，明亮清新。",
      "dialogue": "",
      "duration": 5,
      "notes": "音效：风吹草叶的沙沙声；BGM：轻缓的原声吉他曲；调色：清新暖调，增强草叶的嫩绿质感。"
    },
    {
      "panel_id": 2,
      "scene_id": 1,
      "shot_number": 2,
      "shot_type": "特写",
      "camera_move": "固定",
      "content": "薇拉的脸部特写，闭着双眼，眼尾慵懒下垂，脸颊被阳光晒得微微泛红，几缕发丝被风拂过贴在耳侧，神情百无聊赖，嘴角耷拉着。",
      "dialogue": "",
      "duration": 3,
      "notes": "音效：微风拂过发丝的轻响；BGM：延续前一镜头的吉他曲；光线：侧光打亮脸部轮廓，突出皮肤的通透感。"
    },
    {
      "panel_id": 3,
      "scene_id": 1,
      "shot_number": 3,
      "shot_type": "中景",
      "camera_move": "固定",
      "content": "萨奇的侧面中景，他坐在岩石上，双手撑在身侧的石面上，身体微微前倾，侧头看向画面左侧的薇拉，嘴角噙着一丝不易察觉的笑意，岩石缝隙里钻出几株淡紫色的小野花。",
      "dialogue": "",
      "duration": 2,
      "notes": "音效：草叶沙沙声持续；BGM：保持轻缓节奏。"
    },
    {
      "panel_id": 4,
      "scene_id": 1,
      "shot_number": 4,
      "shot_type": "中景",
      "camera_move": "推",
      "content": "镜头从薇拉的上半身远景推至中景，她突然以坐位体前屈的姿势猛地坐起身，上半身向前倾，眼睛骤然睁开，瞳孔亮晶晶的，脸上的百无聊赖瞬间被憧憬取代，阳光在她发梢镀上金边。",
      "dialogue": "薇拉: 有时候我会想啊，这个小镇是不是太无聊了？",
      "duration": 4,
      "notes": "音效：薇拉坐起身带动草叶的哗啦声；BGM：吉他曲旋律微微上扬；镜头推镜速度稍快，突出动作的突然性。"
    },
    """
    video_ratio = "16:9"
    video_resolution = "480p"
    save_path = "output.mp4"
    target_duration_s = 15
    # _extract_last_frame("/root/work/temp/multi-shot-multi-object-long-video-edit/output.mp4", "/root/work/temp/multi-shot-multi-object-long-video-edit/output_lastframe.jpg")
    seedance_generate(
        video_path,
        [],
        generate_prompt,
        video_ratio,
        video_resolution,
        save_path,
        target_duration_s,
    )
