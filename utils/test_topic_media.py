#!/usr/bin/env python3
"""
测试脚本：为热点话题拉取并下载参考视频，验证路径写入。

用法：
    python utils/test_topic_media.py --username <username>
    python utils/test_topic_media.py --username <username> --limit 2
    python utils/test_topic_media.py --username <username> --limit 1 --platform bilibili
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_ENV_FILE = PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from utils.topic_media import fetch_topic_video, build_media_field, topic_video_path


def main():
    parser = argparse.ArgumentParser(description="测试热点话题媒体下载")
    parser.add_argument("--username", required=True)
    parser.add_argument("--limit", type=int, default=3, help="测试前 N 条话题，默认 3")
    parser.add_argument("--platform", default="", help="平台关键词过滤，如 bilibili / 知乎 / 豆瓣")
    args = parser.parse_args()

    from tikhub import TikHub
    token = os.environ.get("TIKHUB_API_TOKEN", "")
    if not token:
        print("缺少 TIKHUB_API_TOKEN", file=sys.stderr)
        sys.exit(1)
    client = TikHub(api_key=token)

    state_path = PROJECT_ROOT / "workspace" / args.username / "hot_topics_state.json"
    if not state_path.exists():
        print(f"state 文件不存在：{state_path}", file=sys.stderr)
        sys.exit(1)

    topics = json.loads(state_path.read_text(encoding="utf-8")).get("topics", [])
    if args.platform:
        topics = [t for t in topics if args.platform.lower() in t.get("platform", "").lower()]
    topics = topics[: args.limit]

    if not topics:
        print("无匹配话题")
        return

    media_root = PROJECT_ROOT / "workspace" / args.username / "hot_topics_media"
    print(f"媒体保存目录：{media_root}\n")

    ok, fail = 0, 0
    for topic in topics:
        print(f"{'─'*50}")
        print(f"话题：{topic['title'][:50]}")
        print(f"平台：{topic.get('platform')}  URL：{topic.get('url','')[:60]}")

        path = fetch_topic_video(client, topic, media_root)
        media = build_media_field(path)

        if media:
            print(f"  ref_video : {media['ref_video']}")
            print(f"  fetched_at: {media['fetched_at']}")
            ok += 1
        else:
            print("  ✗ 下载失败，media=null")
            fail += 1
        time.sleep(1)

    print(f"\n{'='*50}")
    print(f"完成 | 成功 {ok} | 失败 {fail}")
    print(f"\n期望路径示例：{topic_video_path(media_root, topics[0]['title'])}")


if __name__ == "__main__":
    main()
