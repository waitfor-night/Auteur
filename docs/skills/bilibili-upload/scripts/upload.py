"""
Bilibili video uploader via sau CLI.
Usage:
    python3 upload.py --account <your-account>-bilibili --file video.mp4 \
        --title "标题" --desc "描述" --tags "tag1,tag2" --tid 188

tid 常用值（B站分区）：
    188  科技（通用）
    95   数码
    231  计算机技术
    201  科学科普
    17   单机游戏
    171  电子竞技

Requires:
    - sau CLI on PATH (social-auto-upload installed as editable package)
    - Valid cookie: social-auto-upload/cookies/bilibili_<account>.json
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


def upload(account: str, file: str, title: str, desc: str, tags: str,
           tid: int = 188, schedule: str = "", max_retries: int = 3) -> bool:
    sau = find_sau()

    cmd = [sau, "bilibili", "upload-video",
           "--account", account,
           "--file", file,
           "--title", title,
           "--desc", desc,
           "--tid", str(tid),
           "--tags", tags]
    if schedule:
        cmd += ["--schedule", schedule]

    for attempt in range(1, max_retries + 1):
        print(f"[bilibili] attempt {attempt}/{max_retries}: {title[:30]}")
        try:
            result = subprocess.run(cmd, timeout=600)
            if result.returncode == 0:
                print("[bilibili] upload succeeded")
                return True
            print(f"[bilibili] exit code {result.returncode}, retrying...")
        except subprocess.TimeoutExpired:
            print("[bilibili] timed out after 10 min, retrying...")
        except Exception as e:
            print(f"[bilibili] error: {e}")

    print("[bilibili] all attempts failed")
    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--account",     required=True,       help="sau 账号名，如 myname-bilibili")
    p.add_argument("--file",        required=True,       help="视频文件路径")
    p.add_argument("--title",       required=True,       help="视频标题")
    p.add_argument("--desc",        required=True,       help="视频描述（B站必填）")
    p.add_argument("--tags",        required=True,       help="逗号分隔的标签，如 tag1,tag2")
    p.add_argument("--tid",         type=int, default=188, help="B站分区 ID（默认 188=科技）")
    p.add_argument("--schedule",    default="",          help="定时发布，格式 'YYYY-MM-DD HH:MM'")
    p.add_argument("--max-retries", type=int, default=3, help="最大重试次数")
    args = p.parse_args()

    ok = upload(args.account, args.file, args.title, args.desc,
                args.tags, args.tid, args.schedule, args.max_retries)
    sys.exit(0 if ok else 1)
