from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List

from agno.tools import tool

from tools.mmUnderstandingTools.mmut_implement import (
    image_understanding,
    upload_video_to_ark,
    video_understanding,
)


_VIDEO_SUFFIXES = {
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
    ".MKV",
}

_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".PNG",
    ".JPG",
    ".JPEG",
    ".WEBP",
    ".GIF",
}


@tool
def watch_media_tool(media_paths: List[str], watch_prompt: str) -> Dict[str, Any]:
    """
    在 sandbox 内实现的“观看/理解媒体”工具（与 Assistant 无关）。

    - 不提供缓存：去重/缓存逻辑由上层 user_simulator 的 context_builder 完成
    - 视频：上传到 Ark -> 调用 video_understanding
    - 图片：调用 image_understanding
    """
    results: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []

    base_dir = os.environ.get("SANDBOX_MEDIA_BASE_DIR") or ""

    def _to_abs_path(p: str) -> str:
        pp = Path(p)
        if pp.is_absolute():
            return str(pp.resolve())
        if base_dir:
            return str((Path(base_dir) / pp).resolve())
        return str(pp.resolve())

    for raw_path in media_paths:
        if raw_path is None:
            continue
        path_str = str(raw_path).strip()
        if not path_str:
            continue

        suffix = Path(path_str).suffix
        try:
            abs_path = _to_abs_path(path_str)
            p = Path(abs_path)
            if not p.exists() or not p.is_file():
                raise FileNotFoundError(f"Media file not found: {abs_path}")

            if suffix in _VIDEO_SUFFIXES:
                video_id, _ = upload_video_to_ark(str(p))
                # 上传后可能需要一些处理时间；与工具实现对齐
                time.sleep(6)
                desc = video_understanding(video_id, watch_prompt)
                results.append(
                    {
                        "path": abs_path,
                        "type": "video",
                        "description": (desc or "").strip(),
                    }
                )
            elif suffix in _IMAGE_SUFFIXES:
                desc = image_understanding(str(p), prompt=watch_prompt)
                results.append(
                    {
                        "path": abs_path,
                        "type": "image",
                        "description": (desc or "").strip(),
                    }
                )
            else:
                raise ValueError(f"Unsupported media suffix: {suffix}")
        except Exception as e:
            errors.append({"path": path_str, "error": str(e)})

    return {"results": results, "errors": errors}

