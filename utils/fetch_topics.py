#!/usr/bin/env python3
"""
热点聚合工具：摸摸鱼热榜 + 抖音热搜/关键词搜索 + B站关键词搜索。
所有独立数据源并行拉取，多关键词也并行执行。

用法：
    python3 fetch_topics.py                              # 摸摸鱼全平台，全量
    python3 fetch_topics.py --top 5                      # 每平台只看前 5 条
    python3 fetch_topics.py --filter AI,人工智能          # 仅显示含关键词的条目
    python3 fetch_topics.py --json                       # 输出 JSON
    python3 fetch_topics.py --code MSwxOA==              # 自定义 RSS code
    python3 fetch_topics.py --douyin                     # 仅拉取抖音热搜榜
    python3 fetch_topics.py --douyin --top 10            # 抖音热搜前 10
    python3 fetch_topics.py --douyin-search --search 搞笑    # 抖音关键词搜索
    python3 fetch_topics.py --bilibili --search 游戏         # B站关键词搜索
    python3 fetch_topics.py -b -s 游戏 --order click         # B站按播放量排序
    python3 fetch_topics.py --all --top 10               # 抖音热搜 + 摸摸鱼并行拉取
"""

import argparse
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

RSS_URL = "https://momoyu.cc/api/hot/rss?code={code}"
DOUYIN_HOT_URL = (
    "https://www.douyin.com/aweme/v1/web/hot/search/list/"
    "?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1"
)
BILIBILI_SEARCH_URL = (
    "https://api.bilibili.com/x/web-interface/search/type"
    "?search_type=video&keyword={keyword}&order={order}&page={page}&page_size={page_size}"
)
DOUYIN_SEARCH_URL = (
    "https://www.douyin.com/aweme/v1/web/search/item/"
    "?device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&search_channel=aweme_video_web&keyword={keyword}"
    "&search_source=normal_search&query_correct_type=1&is_filter_search=0"
    "&offset={offset}&count={count}&need_filter_settings=1&sort_type={sort_type}"
)

# 默认 code：知乎/微博/今日头条/B站/博客园/贴吧/豆瓣/B站综合热门
DEFAULT_CODE = "MSwyLDMsNjksNTAsMTgsODMsNSw0OSw5NA=="

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_DOUYIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
}


# ---------------------------------------------------------------------------
# 数据获取函数
# ---------------------------------------------------------------------------

def fetch_rss(code: str) -> str:
    url = RSS_URL.format(code=code)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
        return resp.read().decode("utf-8")


def fetch_douyin_hot(top: int | None = None) -> dict:
    """拉取抖音热搜榜，返回 {"抖音热搜": [{rank, title, url, search_url, hot_value}]}。"""
    req = urllib.request.Request(DOUYIN_HOT_URL, headers=_DOUYIN_HEADERS)
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    word_list = data.get("data", {}).get("word_list", [])
    if top is not None:
        word_list = word_list[:top]

    items = []
    for item in word_list:
        word = item.get("word", "")
        group_id = item.get("group_id", "")
        url = (
            f"https://www.douyin.com/video/{group_id}"
            if group_id
            else f"https://www.douyin.com/search/{urllib.parse.quote(word)}"
        )
        search_url = f"https://www.douyin.com/search/{urllib.parse.quote(word)}" if word else ""
        items.append({
            "rank": item.get("position", 0),
            "title": word,
            "url": url,
            "search_url": search_url,
            "hot_value": item.get("hot_value", 0),
        })
    return {"抖音热搜": items}


def search_douyin_single(keyword: str, top: int = 20, sort_type: int = 1) -> list[dict]:
    """搜索单个关键词的抖音视频，支持翻页，返回列表。"""
    results = []
    offset = 0
    count = min(top, 20)

    while len(results) < top:
        url = DOUYIN_SEARCH_URL.format(
            keyword=urllib.parse.quote(keyword),
            offset=offset,
            count=count,
            sort_type=sort_type,
        )
        req = urllib.request.Request(url, headers=_DOUYIN_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"抖音搜索请求失败（{keyword}）：{e}", file=sys.stderr)
            break

        raw_list = [
            item.get("aweme_info", {})
            for item in (data.get("data") or [])
            if item.get("type") == 1
        ]
        if not raw_list:
            break

        for item in raw_list:
            aweme_id = item.get("aweme_id", "")
            stats = item.get("statistics", {})
            results.append({
                "rank": len(results) + 1,
                "title": item.get("desc", ""),
                "url": f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
                "digg": stats.get("digg_count", 0),
                "comment": stats.get("comment_count", 0),
                "share": stats.get("share_count", 0),
            })
            if len(results) >= top:
                break

        if not data.get("has_more"):
            break
        offset = data.get("cursor", offset + count)

    return results


def search_douyin(keywords: list[str], top_each: int = 20, sort_type: int = 1) -> dict:
    """并行搜索多个关键词，合并去重后按点赞排序，返回 {"抖音搜索": [...]}。"""
    if not keywords:
        return {}

    print(f"并行搜索抖音（{len(keywords)} 个关键词）...", file=sys.stderr)
    all_items: list[dict] = []
    seen: set[str] = set()

    with ThreadPoolExecutor(max_workers=len(keywords)) as ex:
        futures = {ex.submit(search_douyin_single, kw, top_each, sort_type): kw for kw in keywords}
        for fut in as_completed(futures):
            kw = futures[fut]
            try:
                items = fut.result()
                print(f"  抖音[{kw}] 返回 {len(items)} 条", file=sys.stderr)
                for item in items:
                    if item["url"] and item["url"] not in seen:
                        seen.add(item["url"])
                        all_items.append(item)
            except Exception as e:
                print(f"  抖音[{kw}] 失败：{e}", file=sys.stderr)

    all_items.sort(key=lambda x: x["digg"], reverse=True)
    for i, item in enumerate(all_items, 1):
        item["rank"] = i
    return {"抖音搜索": all_items}


def search_bilibili_single(keyword: str, top: int = 20, order: str = "scores") -> list[dict]:
    """搜索单个关键词的B站视频，支持翻页，返回列表。"""
    results = []
    page = 1
    page_size = min(top, 20)

    while len(results) < top:
        url = BILIBILI_SEARCH_URL.format(
            keyword=urllib.parse.quote(keyword),
            order=order,
            page=page,
            page_size=page_size,
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://www.bilibili.com/search?keyword={urllib.parse.quote(keyword)}",
            "Origin": "https://www.bilibili.com",
            "Cookie": "buvid3=AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE12345infoc",
        })
        try:
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"B站搜索请求失败（{keyword}）：{e}", file=sys.stderr)
            break

        items = data.get("data", {}).get("result", [])
        if not items:
            break

        for item in items:
            bvid = item.get("bvid", "")
            title = re.sub(r"<[^>]+>", "", item.get("title", ""))
            results.append({
                "rank": len(results) + 1,
                "title": title,
                "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                "play": item.get("play", 0),
                "like": item.get("like", 0),
                "danmaku": item.get("danmaku", item.get("video_review", 0)),
                "favorites": item.get("favorites", 0),
            })
            if len(results) >= top:
                break

        total_pages = data.get("data", {}).get("numPages", 1)
        if page >= total_pages:
            break
        page += 1

    return results


def search_bilibili(keywords: list[str], top_each: int = 20, order: str = "scores") -> dict:
    """并行搜索多个关键词，合并去重后按播放量排序，返回 {"B站搜索": [...]}。"""
    if not keywords:
        return {}

    print(f"并行搜索B站（{len(keywords)} 个关键词）...", file=sys.stderr)
    all_items: list[dict] = []
    seen: set[str] = set()

    with ThreadPoolExecutor(max_workers=len(keywords)) as ex:
        futures = {ex.submit(search_bilibili_single, kw, top_each, order): kw for kw in keywords}
        for fut in as_completed(futures):
            kw = futures[fut]
            try:
                items = fut.result()
                print(f"  B站[{kw}] 返回 {len(items)} 条", file=sys.stderr)
                for item in items:
                    if item["url"] and item["url"] not in seen:
                        seen.add(item["url"])
                        all_items.append(item)
            except Exception as e:
                print(f"  B站[{kw}] 失败：{e}", file=sys.stderr)

    all_items.sort(key=lambda x: x["play"], reverse=True)
    for i, item in enumerate(all_items, 1):
        item["rank"] = i
    return {"B站搜索": all_items}


# ---------------------------------------------------------------------------
# RSS 解析
# ---------------------------------------------------------------------------

def parse_items(xml_text: str) -> dict:
    """解析摸摸鱼 RSS，返回 {平台: [{rank, title, url}]}。"""
    root = ET.fromstring(xml_text)
    description = root.find(".//item/description")
    if description is None or not description.text:
        return {}

    html = description.text
    platforms: dict = {}
    current_platform = None

    for tag, content in re.findall(r"<(h2|p)>(.*?)</\1>", html, re.DOTALL):
        if tag == "h2":
            current_platform = re.sub(r"<.*?>", "", content).strip()
            platforms[current_platform] = []
        elif tag == "p" and current_platform is not None:
            a = re.search(r'<a href="(.*?)".*?>(.*?)</a>', content)
            if a:
                url, raw_title = a.group(1), a.group(2)
                title = re.sub(r"^\d+\.\s*", "", raw_title).strip()
                rank_m = re.match(r"^(\d+)\.", raw_title)
                rank = int(rank_m.group(1)) if rank_m else 0
                platforms[current_platform].append({"rank": rank, "title": title, "url": url})

    return platforms


# ---------------------------------------------------------------------------
# 过滤 / 截断 / 打印
# ---------------------------------------------------------------------------

def filter_items(platforms: dict, keywords: list[str]) -> dict:
    if not keywords:
        return platforms
    kw_lower = [k.lower() for k in keywords]
    return {
        platform: [item for item in items if any(kw in item["title"].lower() for kw in kw_lower)]
        for platform, items in platforms.items()
        if any(kw in item["title"].lower() for kw in kw_lower for item in items)
    }


def truncate_items(platforms: dict, top: int | None) -> dict:
    if top is None:
        return platforms
    return {p: items[:top] for p, items in platforms.items()}


def print_platforms(platforms: dict) -> None:
    for platform, items in platforms.items():
        print(f"\n【{platform}】")
        for item in items:
            hot_str = f"  🔥{item['hot_value']:,}" if item.get("hot_value") else ""
            stat_str = ""
            if item.get("digg") is not None:
                digg, comment = item.get("digg", 0), item.get("comment", 0)
                stat_str = (
                    f"  👍{digg/10000:.1f}万" if digg >= 10000 else f"  👍{digg}"
                ) + (
                    f"  💬{comment/10000:.1f}万" if comment >= 10000 else f"  💬{comment}"
                )
            elif item.get("play") is not None and "hot_value" not in item:
                play, like = item.get("play", 0), item.get("like", 0)
                stat_str = (
                    f"  ▶{play/10000:.1f}万" if play >= 10000 else f"  ▶{play}"
                ) + (
                    f"  👍{like/10000:.1f}万" if like >= 10000 else f"  👍{like}"
                )
            print(f"  {item['rank']:2d}. {item['title']}{hot_str}{stat_str}")
            if item.get("url"):
                print(f"      {item['url']}")
            if item.get("search_url"):
                print(f"      搜索: {item['search_url']}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="热点聚合（并行拉取）")
    parser.add_argument("--code", default=DEFAULT_CODE, help="摸摸鱼 RSS code")
    parser.add_argument("--filter", dest="filter_kw", default="",
                        help="关键词过滤，逗号分隔，例如：AI,人工智能")
    parser.add_argument("--top", type=int, default=None,
                        help="每个平台最多显示前 N 条，默认全量")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="以 JSON 格式输出")
    parser.add_argument("--douyin", action="store_true",
                        help="拉取抖音热搜榜")
    parser.add_argument("--douyin-search", dest="douyin_search", action="store_true",
                        help="按关键词搜索抖音视频（需配合 --search）")
    parser.add_argument("--bilibili", "-b", action="store_true",
                        help="搜索B站视频（需配合 --search）")
    parser.add_argument("--search", "-s", nargs="+", default=[],
                        help="搜索关键词，支持多个，例如：--search 游戏 '2026游戏' '热门游戏'")
    parser.add_argument("--order", default="scores",
                        choices=["scores", "click", "pubdate", "dm", "stow"],
                        help="B站排序：scores=综合, click=播放量, pubdate=最新（默认 scores）")
    parser.add_argument("--all", dest="all_sources", action="store_true",
                        help="强制拉取全部非搜索源（默认行为，通常无需显式指定）")
    args = parser.parse_args()

    filter_keywords = [k.strip() for k in args.filter_kw.split(",") if k.strip()]
    search_keywords = args.search or (filter_keywords[:1] if filter_keywords else [])

    # 收集需要并行执行的任务
    tasks: dict[str, callable] = {}

    if args.douyin_search:
        if not search_keywords:
            print("错误：--douyin-search 需要配合 --search 关键词", file=sys.stderr)
        else:
            tasks["douyin_search"] = lambda: search_douyin(
                search_keywords, top_each=args.top or 20
            )

    if args.bilibili:
        if not search_keywords:
            print("错误：--bilibili 需要配合 --search 关键词", file=sys.stderr)
        else:
            tasks["bilibili"] = lambda: search_bilibili(
                search_keywords, top_each=args.top or 20, order=args.order
            )

    # 未指定任何具体数据源时，默认拉取全部（抖音热搜 + 摸摸鱼 RSS）
    default_all = not any([args.douyin, args.douyin_search, args.bilibili, args.all_sources])

    if args.douyin or args.all_sources or default_all:
        tasks["douyin_hot"] = lambda: fetch_douyin_hot(top=args.top)

    if args.all_sources or default_all or (not args.douyin and not args.bilibili and not args.douyin_search):
        tasks["rss"] = lambda: parse_items(fetch_rss(args.code))

    # 并行执行所有任务
    platforms: dict = {}
    if len(tasks) == 1:
        # 单任务直接执行，不必开线程池
        name, fn = next(iter(tasks.items()))
        label = {"rss": f"摸摸鱼热榜(code={args.code})", "douyin_hot": "抖音热搜",
                 "douyin_search": f"抖音搜索{search_keywords}",
                 "bilibili": f"B站搜索{search_keywords}"}[name]
        print(f"拉取 {label}...", file=sys.stderr)
        try:
            platforms.update(fn())
        except Exception as e:
            print(f"失败：{e}", file=sys.stderr)
            sys.exit(1)
    else:
        task_labels = {
            "rss": f"摸摸鱼热榜(code={args.code})",
            "douyin_hot": "抖音热搜",
            "douyin_search": f"抖音搜索{search_keywords}",
            "bilibili": f"B站搜索{search_keywords}",
        }
        print(f"并行拉取 {len(tasks)} 个数据源：{[task_labels[k] for k in tasks]}...", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
            futures = {ex.submit(fn): name for name, fn in tasks.items()}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    result = fut.result()
                    if result:
                        platforms.update(result)
                        print(f"  ✓ {task_labels[name]} 完成（{sum(len(v) for v in result.values())} 条）",
                              file=sys.stderr)
                    else:
                        print(f"  ✗ {task_labels[name]} 返回空数据", file=sys.stderr)
                except Exception as e:
                    print(f"  ✗ {task_labels[name]} 失败：{e}", file=sys.stderr)

    if not platforms:
        print("未获取到任何热点数据", file=sys.stderr)
        sys.exit(1)

    if filter_keywords:
        print(f"关键词过滤：{filter_keywords}", file=sys.stderr)
        platforms = filter_items(platforms, filter_keywords)
        if not platforms:
            print(f"未找到包含关键词 {filter_keywords} 的热点", file=sys.stderr)
            sys.exit(0)

    # 摸摸鱼来源需截断（搜索类在拉取时已按 top 限制）
    if "rss" in tasks:
        rss_keys = set(platforms.keys()) - {"抖音热搜", "抖音搜索", "B站搜索"}
        rss_part = {k: platforms[k] for k in rss_keys}
        other_part = {k: platforms[k] for k in platforms if k not in rss_keys}
        rss_part = truncate_items(rss_part, args.top)
        platforms = {**rss_part, **other_part}

    if args.as_json:
        print(json.dumps(platforms, ensure_ascii=False, indent=2))
    else:
        print_platforms(platforms)


if __name__ == "__main__":
    main()
