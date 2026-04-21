#!/usr/bin/env python3
"""
xhs_refresh_tikhub.py — 使用 TikHub API 刷新小红书帖子指标。

替代 xhs_refresh_cron.py，全程通过 TikHub PyPI 包（tikhub）拉取数据，
不再依赖 xiaohongshu-mcp HTTP 调用。

流程：
  1. get_user_notes_v2(user_id) → 获取账号发布的全部帖子列表（含 note_id）
  2. 对 post_id 为空的 active 条目：按标题匹配 → update_platform_post_id
  3. 对 post_id 非空的 active 条目：
       get_note_info_v4(note_id) → 基础指标
       get_note_comments + get_note_comment_replies → 评论树
       update_platform_stats（仅有变化时写入）

用法：
  python utils/xhs_refresh_tikhub.py --username zhaili [--platform xiaohongshu]
  python utils/xhs_refresh_tikhub.py --username zhaili --backfill-only

环境变量：
  TIKHUB_API_TOKEN  TikHub API Token（必填）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# ── 路径修正，使 utils 包可导入 ──────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.xhs_log_io_v2 import (
    get_entries_for_refresh,
    get_entries_pending_id,
    update_platform_post_id,
    update_platform_stats,
)

PLATFORM = "xiaohongshu"
_TIKHUB_TOKEN = os.environ.get("TIKHUB_API_TOKEN", "")


# ──────────────────────────────────────────────
# TikHub 工具函数
# ──────────────────────────────────────────────

def _get_client():
    """初始化 TikHub 客户端（延迟导入，未安装时给出友好错误）。"""
    try:
        import tikhub
    except ImportError:
        print("[tikhub] 未安装 tikhub，请运行：pip install tikhub", file=sys.stderr)
        sys.exit(1)
    if not _TIKHUB_TOKEN:
        print("[tikhub] 缺少 TIKHUB_API_TOKEN 环境变量", file=sys.stderr)
        sys.exit(1)
    return tikhub.Client(token=_TIKHUB_TOKEN)


def fetch_user_notes(client, user_id: str, max_pages: int = 10) -> list[dict]:
    """拉取用户主页全部帖子列表（分页合并）。

    返回每项：{"note_id": ..., "title": ..., "type": ...}
    """
    notes: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        try:
            resp = client.xiaohongshu_web.get_user_notes_v2(
                user_id=user_id,
                cursor=cursor,
            )
        except Exception as e:
            print(f"[tikhub] get_user_notes_v2 异常：{e}", file=sys.stderr)
            break
        data = resp if isinstance(resp, dict) else {}
        items = data.get("data", {}).get("notes", []) or data.get("notes", [])
        if not items:
            break
        for item in items:
            notes.append({
                "note_id": item.get("noteId") or item.get("note_id", ""),
                "title": item.get("displayTitle") or item.get("title", ""),
                "type": item.get("type", ""),
            })
        cursor = data.get("data", {}).get("cursor") or data.get("cursor", "")
        if not cursor:
            break
        time.sleep(0.5)
    return notes


def fetch_note_stats(client, note_id: str) -> Optional[dict]:
    """调用 get_note_info_v4 拉取帖子基础指标。"""
    try:
        resp = client.xiaohongshu_web.get_note_info_v4(note_id=note_id)
    except Exception as e:
        print(f"[tikhub] get_note_info_v4({note_id}) 异常：{e}", file=sys.stderr)
        return None
    data = resp if isinstance(resp, dict) else {}
    note = (
        data.get("data", {}).get("items", [{}])[0].get("noteCard")
        or data.get("noteCard")
        or data.get("data", {})
    )
    interact = note.get("interactInfo", {})
    return {
        "liked_count": int(interact.get("likedCount", 0) or 0),
        "collected_count": int(interact.get("collectedCount", 0) or 0),
        "comment_count": int(interact.get("commentCount", 0) or 0),
        "share_count": int(interact.get("shareCount", 0) or 0),
        "view_count": int(interact.get("viewCount", 0) or 0),
    }


def _parse_comment(raw: dict, sub_comments: list[dict] | None = None) -> dict:
    return {
        "comment_id": raw.get("id", ""),
        "author": raw.get("userInfo", {}).get("nickname", "") or raw.get("nickname", ""),
        "content": raw.get("content", ""),
        "like_count": int(raw.get("likeCount", 0) or 0),
        "create_time": raw.get("createTime", "") or raw.get("create_time", ""),
        "sub_comments": sub_comments or [],
    }


def fetch_note_comments(client, note_id: str, max_pages: int = 5) -> list[dict]:
    """拉取帖子评论树（含子回复）。"""
    comments: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        try:
            resp = client.xiaohongshu_web.get_note_comments(
                note_id=note_id,
                cursor=cursor,
            )
        except Exception as e:
            print(f"[tikhub] get_note_comments({note_id}) 异常：{e}", file=sys.stderr)
            break
        data = resp if isinstance(resp, dict) else {}
        raw_list = (
            data.get("data", {}).get("comments", [])
            or data.get("comments", [])
        )
        if not raw_list:
            break
        for raw in raw_list:
            comment_id = raw.get("id", "")
            # 拉子回复
            sub_list: list[dict] = []
            sub_cursor = ""
            if raw.get("subCommentCount", 0):
                for _ in range(3):
                    try:
                        sub_resp = client.xiaohongshu_web.get_note_comment_replies(
                            note_id=note_id,
                            comment_id=comment_id,
                            cursor=sub_cursor,
                        )
                    except Exception:
                        break
                    sub_data = sub_resp if isinstance(sub_resp, dict) else {}
                    sub_raw = (
                        sub_data.get("data", {}).get("comments", [])
                        or sub_data.get("comments", [])
                    )
                    sub_list.extend(_parse_comment(s) for s in sub_raw)
                    sub_cursor = sub_data.get("data", {}).get("cursor") or sub_data.get("cursor", "")
                    if not sub_cursor:
                        break
                    time.sleep(0.3)
            comments.append(_parse_comment(raw, sub_list))
        cursor = data.get("data", {}).get("cursor") or data.get("cursor", "")
        if not cursor:
            break
        time.sleep(0.5)
    return comments


# ──────────────────────────────────────────────
# 主逻辑
# ──────────────────────────────────────────────

def backfill_missing_ids(
    username: str,
    client,
    user_id: str,
    platform: str = PLATFORM,
) -> None:
    """用 TikHub 用户主页帖子列表补全缺失的 post_id。"""
    pending = get_entries_pending_id(username, platform)
    if not pending:
        print(f"[backfill] 无缺失 post_id 的条目")
        return
    print(f"[backfill] 缺失 post_id 条目数：{len(pending)}")

    all_notes = fetch_user_notes(client, user_id)
    if not all_notes:
        print("[backfill] 未能拉取用户主页帖子列表", file=sys.stderr)
        return

    # title → note_id 映射
    note_map: dict[str, str] = {n["title"]: n["note_id"] for n in all_notes if n["title"]}

    for entry in pending:
        trace_id = entry["trace_id"]
        title = entry.get("title", "")
        if not title:
            continue
        # 精确匹配
        note_id = note_map.get(title, "")
        if not note_id:
            # 模糊匹配（前10字）
            for t, nid in note_map.items():
                if title[:10] and (title[:10] in t or t[:10] in title):
                    note_id = nid
                    break
        if not note_id:
            print(f"[backfill] 未找到「{title[:20]}」对应帖子")
            continue
        ok = update_platform_post_id(username, trace_id, platform, note_id)
        print(f"[backfill] {'✓' if ok else '✗'} trace={trace_id} → note_id={note_id}")


def refresh_stats(
    username: str,
    client,
    platform: str = PLATFORM,
    delay: float = 1.0,
) -> None:
    """刷新所有 active 帖子的指标（基础 stats + 评论树）。"""
    active = get_entries_for_refresh(username, platform)
    if not active:
        print(f"[refresh] 无待刷新帖子")
        return
    print(f"[refresh] 待刷新帖子数：{len(active)}")

    for item in active:
        trace_id = item["trace_id"]
        note_id = item["post_id"]
        title_short = item.get("title", "")[:20]

        # 基础指标
        stats = fetch_note_stats(client, note_id)
        if stats is None:
            print(f"[refresh] 跳过 {note_id}（拉取基础指标失败）")
            continue

        # 评论树
        comments = fetch_note_comments(client, note_id)
        stats["comments"] = comments

        ok = update_platform_stats(username, trace_id, platform, stats)
        if ok:
            print(
                f"[refresh] ✓ {title_short} | 赞={stats['liked_count']} "
                f"收={stats['collected_count']} 评={stats['comment_count']}"
            )
        else:
            print(f"[refresh] 未找到 trace_id={trace_id}")

        time.sleep(delay)


def backfill_trace(username: str, trace_id: str | None = None) -> None:
    """刷新完成后，把最新指标同步写回对应 trace 文件。"""
    try:
        from utils.backfill_social_feedback_v2 import backfill_by_trace_id, backfill_all
        if trace_id:
            backfill_by_trace_id(username, trace_id)
        else:
            backfill_all(username)
    except Exception as e:
        print(f"[backfill_trace] 异常（不影响主流程）：{e}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="TikHub 小红书指标刷新")
    parser.add_argument("--username", required=True, help="workspace 用户名")
    parser.add_argument("--platform", default=PLATFORM)
    parser.add_argument("--backfill-only", action="store_true", help="仅补全 post_id，不刷新指标")
    parser.add_argument("--refresh-only", action="store_true", help="仅刷新指标，不补全 post_id")
    parser.add_argument("--delay", type=float, default=1.0, help="请求间隔秒数")
    args = parser.parse_args()

    # 读取 xhs_config.json 获取 user_id
    config_path = _PROJECT_ROOT / "workspace" / args.username / "xhs_config.json"
    if not config_path.exists():
        print(f"[error] 未找到 {config_path}，请先运行 set-account", file=sys.stderr)
        sys.exit(1)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    user_id = config.get("xhs_user_id", "")
    if not user_id:
        print("[error] xhs_config.json 中 xhs_user_id 未配置", file=sys.stderr)
        sys.exit(1)

    client = _get_client()

    if not args.refresh_only:
        backfill_missing_ids(args.username, client, user_id, args.platform)

    if not args.backfill_only:
        refresh_stats(args.username, client, args.platform, args.delay)
        backfill_trace(args.username)


if __name__ == "__main__":
    main()
