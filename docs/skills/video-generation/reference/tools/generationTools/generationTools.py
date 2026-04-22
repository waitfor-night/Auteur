import os
from agno.tools import tool
from pathlib import Path
from typing import List, Tuple, Optional, Union
from utils.trace_recorder import recorder
from tools.generationTools.gt_implement import seedance_generate_with_retry
from utils.story_gen_tools import generate_image
from concurrent.futures import ThreadPoolExecutor, as_completed

@tool()
@recorder.record
def video_generate_tool(
    video_path: Optional[Union[str, List[str]]],
    image_paths: List[str],
    generate_prompt: str,
    segment_id: int | None = None,
    video_ratio: str = "16:9",
    video_resolution: str = "480p",
    save_path: str | None = None,
    target_duration_s: float | None = None,
) -> Tuple[str, str, str, str]:
    """
    调用视频编辑 API 对视频进行编辑（如移除物体、替换内容等），支持单视频或多视频参考。
    Args:
        video_path: 待编辑视频的本地路径，可为单个路径（str）或路径列表（List[str]）；为空（None 或空列表）时表示纯生成。多视频时按顺序对应 [视频1]、[视频2] 等。
        image_paths: 参考图片路径列表，可为空。
        generate_prompt: 传给 Seedance等模型的生成/编辑提示词，自由描述期望效果（如风格、替换、移除等）；可引用 [图1]/[图2]、[视频1]/[视频2] 等占位符。
        segment_id: 片段 ID，用于文件命名区分不同片段。
        video_ratio: 输出视频比例，默认 "16:9"可选["16:9","9:16","1:1","4:3","3:4","21:9"]。
        video_resolution: 输出分辨率，默认 "480p"可选[480p,720p]。
        save_path: 结果视频保存路径，默认以第一个参考视频路径加 _seg{segment_id}_edited 后缀，无参考视频时为 generated_seg{segment_id}_edited.mp4。
        target_duration_s: 目标输出时长（秒）。为空则与输入视频同长；有值则生成该时长，不足 4s 会先请求 4s 再裁剪。
    Returns:
        (video_path, first_frame_path, middle_frame_path, last_frame_path): 视频保存路径、首帧路径、中间帧路径、尾帧路径。
    """
    if save_path is None:
        seg_suffix = f"_seg{segment_id}" if segment_id is not None else ""
        first_path = (
            video_path
            if isinstance(video_path, str)
            else (video_path[0] if video_path else None)
        )
        if first_path and str(first_path).strip():
            root, ext = os.path.splitext(first_path)
            save_path = f"{root}{seg_suffix}_edited{ext}"
        else:
            save_path = f"generated{seg_suffix}_edited.mp4"
    return seedance_generate_with_retry(
        video_path=video_path,
        image_paths=image_paths,
        user_message=generate_prompt,
        video_ratio=video_ratio,
        video_resolution=video_resolution,
        save_path=save_path,
        target_duration_s=target_duration_s,
    )


@tool
@recorder.record
def batch_video_generate_tool(
    video_paths: List[str],
    image_paths: List[List[str]],
    user_messages: List[str],
    segment_ids: List[int] | None = None,
    video_ratio: str = "16:9",
    video_resolution: str = "480p",
    save_path: str | None = None,
    target_duration_s: List[float] | None = None,
) -> List[dict]:
    """
    并发编辑/生成多个视频，每个视频的参考图和编辑指令不同。
    Args:
        video_paths: The list of video paths to edit. 空字符串或 None 表示纯生成。
        image_paths: The list of image paths to use for editing (one list per video).
        user_messages: The list of user messages to use for editing.
        segment_ids: Optional list of segment IDs for tracking. 用于分批调用时追踪每个结果对应的原始 segment_id，便于按顺序合并。
        video_ratio: The video ratio to use for editing, optional["16:9","9:16","1:1","4:3","3:4","21:9"].
        video_resolution: The video resolution to use for editing, optional[480p,720p].
        target_duration_s: 各视频目标输出时长（秒）列表，与 video_paths 一一对应。为空则每个视频按各自输入时长；有值时长度须与 video_paths 一致，不足 4s 会先请求 4s 再裁剪。
    Returns:
        List of dicts: {"segment_id": int, "video_path": str, "first_frame_path": str, "last_frame_path": str}
        segment_id 来自传入的 segment_ids（若未传入则为批次内索引 0,1,2...）。
    """
    n = len(video_paths)
    if not (n == len(image_paths) == len(user_messages)):
        raise ValueError("video_paths, image_paths, user_messages 长度必须一致")
    if target_duration_s is not None and len(target_duration_s) != n:
        raise ValueError("target_duration_s 长度须与 video_paths 一致")
    if segment_ids is not None and len(segment_ids) != n:
        raise ValueError("segment_ids 长度须与 video_paths 一致")

    # 若未传入 segment_ids，使用批次内索引
    if segment_ids is None:
        segment_ids = list(range(n))

    results = [None] * n  # 预分配以保持顺序

    def _edit_one(i: int) -> Tuple[int, dict]:
        vp = video_paths[i]
        imgs = image_paths[i]
        msg = user_messages[i]
        seg_id = segment_ids[i]
        # 使用 segment_id 区分不同片段，避免命名冲突
        if vp and str(vp).strip():
            root, ext = os.path.splitext(vp)
            one_save_path = f"{root}_seg{seg_id}_edited{ext}"
        else:
            one_save_path = f"generated_seg{seg_id}_edited.mp4"
        t_dur = target_duration_s[i] if target_duration_s is not None else None
        video_path, first_frame, middle_frame, last_frame = seedance_generate_with_retry(
            video_path=vp,
            image_paths=imgs,
            user_message=msg,
            video_ratio=video_ratio,
            video_resolution=video_resolution,
            save_path=one_save_path,
            target_duration_s=t_dur,
        )
        return i, {
            "segment_id": seg_id,
            "video_path": video_path,
            "first_frame_path": first_frame,
            "middle_frame_path": middle_frame,
            "last_frame_path": last_frame,
        }

    print(f"[batch_video_generate_tool] 并发生成/编辑 {n} 个视频...")

    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(_edit_one, i) for i in range(n)]
        for future in as_completed(futures):
            idx, res = future.result()
            results[idx] = res
            print(
                f"[batch_video_generate_tool] segment {res['segment_id']} 完成: {res['video_path']}"
            )

    return results
    
__all__ = ["video_generate_tool", "batch_video_generate_tool"]
