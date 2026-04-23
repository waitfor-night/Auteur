#!/usr/bin/env python3
"""
热点聚合工具：通过 TikHub SDK 并行拉取抖音 / 知乎 / B站 / TikTok 热榜。

用法：
    python3 fetch_topics.py                   # 全平台并行，默认取前 10
    python3 fetch_topics.py --top 5           # 每平台前 5 条
    python3 fetch_topics.py --json            # JSON 输出
    python3 fetch_topics.py --douyin          # 仅抖音热搜
    python3 fetch_topics.py --zhihu           # 仅知乎热榜
    python3 fetch_topics.py --bilibili        # 仅 B 站热搜
    python3 fetch_topics.py --tiktok          # 仅 TikTok 趋势
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


# ---------------------------------------------------------------------------
# TikHub client
# ---------------------------------------------------------------------------

def _get_tikhub_client():
    api_key = os.environ.get("TIKHUB_API_TOKEN", "")
    if not api_key:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("TIKHUB_API_TOKEN="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        raise RuntimeError("TIKHUB_API_TOKEN 未设置")
    from tikhub import TikHub
    return TikHub(api_key=api_key)


# ---------------------------------------------------------------------------
# 各平台热榜
# ---------------------------------------------------------------------------

def fetch_douyin_hot(client, top: int | None = None) -> dict:
    """抖音热搜榜，返回 {"抖音热搜": [items]}。"""
    r = client.douyin_web.fetch_hot_search_result()
    word_list = r.get("data", {}).get("data", {}).get("word_list", [])
    if top is not None:
        word_list = word_list[:top]
    items = []
    for idx, item in enumerate(word_list):
        word = item.get("word", "")
        group_id = item.get("group_id", "")
        url = (
            f"https://www.douyin.com/video/{group_id}"
            if group_id
            else f"https://www.douyin.com/search/{urllib.parse.quote(word)}"
        )
        items.append({
            "rank": item.get("position", idx + 1),
            "title": word,
            "url": url,
            "hot_value": item.get("hot_value", 0),
        })
    return {"抖音热搜": items}


def fetch_zhihu_hot(client, top: int | None = None) -> dict:
    """知乎热榜，返回 {"知乎热榜": [items]}。"""
    r = client.zhihu_web.fetch_hot_list()
    raw_items = r.get("data", {}).get("data", [])
    if top is not None:
        raw_items = raw_items[:top]
    items = []
    for idx, item in enumerate(raw_items):
        target = item.get("target", {})
        title = target.get("title", "")
        api_url = target.get("url", "")
        qid = re.search(r"/questions/(\d+)", api_url)
        url = f"https://www.zhihu.com/question/{qid.group(1)}" if qid else api_url
        items.append({
            "rank": idx + 1,
            "title": title,
            "url": url,
        })
    return {"知乎热榜": items}


def fetch_bilibili_hot(client, top: int | None = None) -> dict:
    """B站热搜榜，返回 {"B站热搜": [items]}。"""
    limit = top or 50
    r = client.bilibili_web.fetch_hot_search(limit=limit)
    raw_list = (
        r.get("data", {}).get("data", {}).get("trending", {}).get("list", [])
    )
    if top is not None:
        raw_list = raw_list[:top]
    items = []
    for idx, item in enumerate(raw_list):
        kw = item.get("keyword", "")
        show_name = item.get("show_name", kw)
        url = f"https://search.bilibili.com/all?keyword={urllib.parse.quote(kw)}"
        items.append({
            "rank": idx + 1,
            "title": show_name,
            "url": url,
            "hot_value": item.get("heat_score", 0),
        })
    return {"B站热搜": items}


def fetch_tiktok_trending(client, top: int | None = None) -> dict:
    """TikTok 热词榜，返回 {"TikTok趋势": [items]}。"""
    r = client.tiktok_web.fetch_trending_searchwords()
    raw_list = r.get("data", {}).get("trending_search_words", [])
    if top is not None:
        raw_list = raw_list[:top]
    items = []
    for idx, item in enumerate(raw_list):
        word = item.get("trendingSearchWord", "")
        url = f"https://www.tiktok.com/search?q={urllib.parse.quote(word)}"
        items.append({
            "rank": idx + 1,
            "title": word,
            "url": url,
        })
    return {"TikTok趋势": items}


# ---------------------------------------------------------------------------
# 汇总接口（供 hot_topics_cron.py 调用）
# ---------------------------------------------------------------------------

def fetch_all_hot(top: int | None = None) -> dict:
    """并行拉取全平台热榜，返回 {platform: [items]}。"""
    client = _get_tikhub_client()
    tasks = {
        "抖音热搜":  lambda: fetch_douyin_hot(client, top),
        "知乎热榜":  lambda: fetch_zhihu_hot(client, top),
        "B站热搜":   lambda: fetch_bilibili_hot(client, top),
        "TikTok趋势": lambda: fetch_tiktok_trending(client, top),
    }
    platforms: dict = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                result = fut.result()
                platforms.update(result)
                count = sum(len(v) for v in result.values())
                print(f"  ✓ {name}（{count} 条）", file=sys.stderr)
            except Exception as e:
                print(f"  ✗ {name} 失败：{e}", file=sys.stderr)
    return platforms


# ---------------------------------------------------------------------------
# 工具函数（供外部调用）
# ---------------------------------------------------------------------------

def truncate_items(platforms: dict, top: int | None) -> dict:
    if top is None:
        return platforms
    return {p: items[:top] for p, items in platforms.items()}


def filter_items(platforms: dict, keywords: list[str]) -> dict:
    if not keywords:
        return platforms
    kw_lower = [k.lower() for k in keywords]
    return {
        platform: [item for item in items if any(kw in item["title"].lower() for kw in kw_lower)]
        for platform, items in platforms.items()
        if any(kw in item["title"].lower() for kw in kw_lower for item in items)
    }


def print_platforms(platforms: dict) -> None:
    for platform, items in platforms.items():
        print(f"\n【{platform}】")
        for item in items:
            hot_str = f"  🔥{item['hot_value']:,}" if item.get("hot_value") else ""
            print(f"  {item['rank']:2d}. {item['title']}{hot_str}")
            if item.get("url"):
                print(f"      {item['url']}")


# ---------------------------------------------------------------------------
# main（CLI 调试用）
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="热点聚合（TikHub）")
    parser.add_argument("--top", type=int, default=10, help="每平台前 N 条（默认 10）")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON 输出")
    parser.add_argument("--filter", dest="filter_kw", default="", help="关键词过滤，逗号分隔")
    parser.add_argument("--douyin",   action="store_true", help="仅拉取抖音热搜")
    parser.add_argument("--zhihu",    action="store_true", help="仅拉取知乎热榜")
    parser.add_argument("--bilibili", action="store_true", help="仅拉取 B 站热搜")
    parser.add_argument("--tiktok",   action="store_true", help="仅拉取 TikTok 趋势")
    args = parser.parse_args()

    client = _get_tikhub_client()
    only_flags = {args.douyin, args.zhihu, args.bilibili, args.tiktok}
    any_flag = any(only_flags)

    tasks: dict[str, callable] = {}
    if not any_flag or args.douyin:
        tasks["抖音热搜"]  = lambda: fetch_douyin_hot(client, args.top)
    if not any_flag or args.zhihu:
        tasks["知乎热榜"]  = lambda: fetch_zhihu_hot(client, args.top)
    if not any_flag or args.bilibili:
        tasks["B站热搜"]   = lambda: fetch_bilibili_hot(client, args.top)
    if not any_flag or args.tiktok:
        tasks["TikTok趋势"] = lambda: fetch_tiktok_trending(client, args.top)

    print(f"并行拉取：{list(tasks.keys())} ...", file=sys.stderr)
    platforms: dict = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                result = fut.result()
                platforms.update(result)
                count = sum(len(v) for v in result.values())
                print(f"  ✓ {name}（{count} 条）", file=sys.stderr)
            except Exception as e:
                print(f"  ✗ {name} 失败：{e}", file=sys.stderr)

    if not platforms:
        print("未获取到任何热点数据", file=sys.stderr)
        sys.exit(1)

    filter_keywords = [k.strip() for k in args.filter_kw.split(",") if k.strip()]
    if filter_keywords:
        platforms = filter_items(platforms, filter_keywords)

    if args.as_json:
        print(json.dumps(platforms, ensure_ascii=False, indent=2))
    else:
        print_platforms(platforms)


if __name__ == "__main__":
    main()
