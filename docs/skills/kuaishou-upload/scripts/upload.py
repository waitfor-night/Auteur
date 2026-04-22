"""
Kuaishou video uploader via sau CLI.
Usage:
    python3 upload.py --account <your-account>-kuaishou --file video.mp4 \
        --title "标题" --tags "tag1,tag2"

Requires:
    - sau CLI on PATH (social-auto-upload installed as editable package)
    - Valid cookie: social-auto-upload/cookies/kuaishou_<account>.json
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

    cmd = [sau, "kuaishou", "upload-video",
           "--account", account,
           "--file", file,
           "--title", title,
           "--tags", tags]
    if desc:
        cmd += ["--desc", desc]
    if schedule:
        cmd += ["--schedule", schedule]

    for attempt in range(1, max_retries + 1):
        print(f"[kuaishou] attempt {attempt}/{max_retries}: {title[:30]}")
        try:
            result = subprocess.run(cmd, timeout=300)
            if result.returncode == 0:
                print("[kuaishou] upload succeeded")
                return True
            print(f"[kuaishou] exit code {result.returncode}, retrying...")
        except subprocess.TimeoutExpired:
            print("[kuaishou] timed out after 5 min, retrying...")
        except Exception as e:
            print(f"[kuaishou] error: {e}")

    print("[kuaishou] all attempts failed")
    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--account",     required=True,       help="sau 账号名，如 myname-kuaishou")
    p.add_argument("--file",        required=True,       help="视频文件路径")
    p.add_argument("--title",       required=True,       help="视频标题")
    p.add_argument("--tags",        required=True,       help="逗号分隔的标签，如 tag1,tag2")
    p.add_argument("--desc",        default="",          help="可选描述文字")
    p.add_argument("--schedule",    default="",          help="定时发布，格式 'YYYY-MM-DD HH:MM'")
    p.add_argument("--max-retries", type=int, default=3, help="最大重试次数")
    args = p.parse_args()

    ok = upload(args.account, args.file, args.title, args.tags,
                args.desc, args.schedule, args.max_retries)
    sys.exit(0 if ok else 1)
