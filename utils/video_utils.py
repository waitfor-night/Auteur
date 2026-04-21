import json
import os
import subprocess
from typing import Any, Dict

from volcenginesdkarkruntime import Ark

client = Ark(api_key=os.environ.get("ARK_API_KEY"))

MMODEL_ID = ["doubao-seedance-1-5-pro-251215", "doubao-seed-1-8-251228"]


def get_video_metadata_ffprobe(video_path: str) -> Dict[str, Any]:
    """使用 ffprobe 获取视频元数据。返回 { duration_s, width, height, fps }，失败时抛出异常。"""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        raise FileNotFoundError("ffprobe not found in PATH; please install ffmpeg")
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr or 'unknown error'}")
    try:
        probe_data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse ffprobe output: {e}")
    video_stream = None
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break
    if not video_stream:
        raise RuntimeError("No video stream found in file")
    width = video_stream.get("width")
    height = video_stream.get("height")
    fps_str = video_stream.get("r_frame_rate", "0/1")
    if "/" in fps_str:
        num, den = map(float, fps_str.split("/"))
        fps = num / den if den != 0 else 0.0
    else:
        fps = float(fps_str) if fps_str else 0.0
    duration = None
    if "format" in probe_data and "duration" in probe_data["format"]:
        duration = float(probe_data["format"]["duration"])
    elif "duration" in video_stream:
        duration = float(video_stream["duration"])
    if duration is None:
        raise RuntimeError("Could not determine video duration")
    return {
        "duration_s": round(duration, 2),
        "width": width,
        "height": height,
        "fps": round(fps, 2),
    }


def get_video_url_by_task_id(task_id):
    resp = client.content_generation.tasks.get(
        task_id=task_id,
    )
    return resp.content.video_url#.items[0].content.video_url

if __name__ == "__main__":
    print(get_video_url_by_task_id("cgt-20260129160037-pp59s"))