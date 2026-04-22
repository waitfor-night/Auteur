"""
Douyin video uploader via sau CLI.
Usage:
    python3 upload.py --account <your-account>-douyin --file video.mp4 --title "标题" --tags "tag1,tag2"

Requires:
    - sau CLI on PATH (social-auto-upload installed as editable package)
    - Valid cookie: social-auto-upload/cookies/douyin_<account>.json
    - DISPLAY=:0 in WSL (Playwright needs headed mode for Douyin)
    - System proxy at http://127.0.0.1:7897 (Douyin creator portal requires proxy)
"""
import argparse
import os
import shutil
import subprocess
import sys


def find_sau() -> str:
    sau = shutil.which("sau")
    if sau:
        return sau
    # common locations when installed in project venv
    candidates = [
        os.path.expanduser("~/.local/bin/sau"),
        os.path.expanduser("~/.venv/bin/sau"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "sau CLI not found. Install social-auto-upload: pip install -e /path/to/social-auto-upload"
    )


def upload(account: str, file: str, title: str, tags: str,
           desc: str = "", schedule: str = "", max_retries: int = 3) -> bool:
    sau = find_sau()
    os.environ.setdefault("DISPLAY", ":0")

    cmd = [sau, "douyin", "upload-video",
           "--account", account,
           "--file", file,
           "--title", title,
           "--tags", tags]
    if desc:
        cmd += ["--desc", desc]
    if schedule:
        cmd += ["--schedule", schedule]

    for attempt in range(1, max_retries + 1):
        print(f"[douyin] attempt {attempt}/{max_retries}: {title[:30]}")
        try:
            result = subprocess.run(cmd, timeout=300)
            if result.returncode == 0:
                print("[douyin] upload succeeded")
                return True
            print(f"[douyin] exit code {result.returncode}, retrying...")
        except subprocess.TimeoutExpired:
            print("[douyin] timed out after 5 min, retrying...")
        except Exception as e:
            print(f"[douyin] error: {e}")

    print("[douyin] all attempts failed")
    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--account",     required=True,  help="sau account name, e.g. myname-douyin")
    p.add_argument("--file",        required=True,  help="视频文件路径")
    p.add_argument("--title",       required=True,  help="视频标题")
    p.add_argument("--tags",        required=True,  help="逗号分隔的标签，如 tag1,tag2")
    p.add_argument("--desc",        default="",     help="可选描述")
    p.add_argument("--schedule",    default="",     help="定时发布时间，格式 'YYYY-MM-DD HH:MM'")
    p.add_argument("--max-retries", type=int, default=3, help="最大重试次数")
    args = p.parse_args()

    ok = upload(args.account, args.file, args.title, args.tags,
                args.desc, args.schedule, args.max_retries)
    sys.exit(0 if ok else 1)
