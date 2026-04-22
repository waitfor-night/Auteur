"""
TikTok uploader: tries headless first, falls back to headful on failure.
Each attempt runs in a subprocess to avoid Playwright sync/async event-loop
pollution across retries.

Usage:
    python3 upload.py --file video.mp4 --description "描述 #tag" --cookies cookies.txt
"""
import argparse
import os
import subprocess
import sys


def _attempt_script(file: str, description: str, cookies: str, headless: bool) -> str:
    """Return a self-contained Python snippet for one upload attempt."""
    headless_str = "True" if headless else "False"
    return f"""
import os, sys
os.environ.setdefault("DISPLAY", "{os.environ.get("DISPLAY", ":0")}")
from tiktok_uploader.upload import TikTokUploader
uploader = TikTokUploader(cookies={cookies!r}, browser="chromium", headless={headless_str})
result = uploader.upload_video(filename={file!r}, description={description!r})
sys.exit(0 if result else 1)
"""


def upload(file: str, description: str, cookies: str, max_retries: int = 2) -> bool:
    modes = [
        ("headless", True),
        ("headful",  False),
    ]

    for mode_name, headless in modes:
        for attempt in range(1, max_retries + 1):
            print(f"[tiktok] {mode_name} mode, attempt {attempt}/{max_retries}")
            try:
                script = _attempt_script(file, description, cookies, headless)
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    timeout=180,
                )
                if result.returncode == 0:
                    print(f"[tiktok] uploaded successfully ({mode_name})")
                    return True
                print(f"[tiktok] upload returned non-zero, retrying...")
            except subprocess.TimeoutExpired:
                print(f"[tiktok] timed out after 3 min, retrying...")
            except Exception as e:
                print(f"[tiktok] error: {e}")

        print(f"[tiktok] {mode_name} mode failed after {max_retries} attempts, switching mode...")

    print("[tiktok] all modes exhausted, upload failed")
    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--file",        required=True,  help="视频文件路径")
    p.add_argument("--description", required=True,  help="描述文字（含 #tag）")
    p.add_argument("--cookies",     default="cookies.txt", help="cookies 文件路径")
    p.add_argument("--max-retries", type=int, default=2,   help="每种模式最大重试次数")
    args = p.parse_args()

    ok = upload(args.file, args.description, args.cookies, args.max_retries)
    sys.exit(0 if ok else 1)
