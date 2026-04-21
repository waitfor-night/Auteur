#!/usr/bin/env python3
"""
backfill_social_feedback_v2.py — 将 publish_log_v2.json 的多平台指标回写到 trace 文件。

写入格式（trace 顶层字段 social-media-feedback）：
{
  "publish_platform": ["xiaohongshu", "douyin"],
  "xiaohongshu": {
    "title": "...", "tags": [...], "post_id": "...",
    "publish_time": "...",
    "stats": {"liked": 120, "collected": 45, "comments": 8, "plays": 3200},
    "stats_history": [
      {"ts": "2026-04-19T10:00", "liked": 10, "plays": 300}
    ]
  },
  "douyin": { ... }
}

用法：
  python utils/backfill_social_feedback_v2.py --username <username>
  python utils/backfill_social_feedback_v2.py --username <username> --trace-id ep_xxx
  python utils/backfill_social_feedback_v2.py --username <username> --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_log_v2(username: str) -> list[dict]:
    p = _PROJECT_ROOT / "workspace" / username / "publish_log_v2.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _load_trace(trace_path: str) -> tuple[Path, dict]:
    p = Path(trace_path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / trace_path
    if not p.exists():
        raise FileNotFoundError(f"trace not found: {p}")
    return p, json.loads(p.read_text(encoding="utf-8"))


def _build_platform_feedback(plat_data: dict) -> dict:
    """把 publish_log_v2 中单平台数据转换为 trace 写入格式。"""
    stats = plat_data.get("stats", {})
    # 统一字段名：publish_log_v2 用 liked_count / view_count，trace 用 liked / plays
    compact_stats = {
        "liked": stats.get("liked_count", 0),
        "collected": stats.get("collected_count", 0),
        "comments": stats.get("comment_count", 0),
        "plays": stats.get("view_count", 0),
        "share": stats.get("share_count", 0),
    }
    compact_history = []
    for snap in plat_data.get("stats_history", []):
        compact_history.append({
            "ts": snap.get("observed_at", ""),
            "liked": snap.get("liked_count", 0),
            "collected": snap.get("collected_count", 0),
            "comments": snap.get("comment_count", 0),
            "plays": snap.get("view_count", 0),
            "share": snap.get("share_count", 0),
        })
    return {
        "title": plat_data.get("title", ""),
        "tags": plat_data.get("tags", []),
        "post_id": plat_data.get("post_id", ""),
        "account": plat_data.get("account", ""),
        "publish_time": "",  # filled by caller
        "refresh_status": plat_data.get("refresh_status", "active"),
        "last_updated": plat_data.get("last_updated"),
        "stats": compact_stats,
        "stats_history": compact_history,
    }


def _build_feedback(entry: dict) -> dict:
    platforms = entry.get("platforms", {})
    feedback: dict = {
        "publish_platform": list(platforms.keys()),
    }
    for platform, plat_data in platforms.items():
        pf = _build_platform_feedback(plat_data)
        pf["publish_time"] = entry.get("publish_time", "")
        feedback[platform] = pf
    return feedback


def backfill_by_trace_id(username: str, trace_id: str, dry_run: bool = False) -> bool:
    """回写单条记录，返回是否成功。"""
    for entry in _load_log_v2(username):
        if entry.get("trace_id") == trace_id:
            return _write_feedback(entry, dry_run=dry_run)
    print(f"[SKIP] trace_id={trace_id} 在 publish_log_v2 中未找到")
    return False


def _write_feedback(entry: dict, dry_run: bool = False) -> bool:
    trace_path = entry.get("trace_path", "")
    trace_id = entry.get("trace_id", "")
    if not trace_path:
        print(f"[SKIP] {trace_id} — trace_path 为空")
        return False
    try:
        p, trace = _load_trace(trace_path)
    except FileNotFoundError as e:
        print(f"[SKIP] {trace_id} — {e}")
        return False

    feedback = _build_feedback(entry)
    action = "UPDATE" if "social-media-feedback" in trace else "ADD"
    trace["social-media-feedback"] = feedback

    platforms = list(entry.get("platforms", {}).keys())
    print(f"[{action}] {trace_id} | 平台：{platforms}")
    if not dry_run:
        p.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"       → {p}")
    return True


def backfill_all(username: str, dry_run: bool = False) -> None:
    entries = _load_log_v2(username)
    if not entries:
        print(f"[backfill_v2] publish_log_v2.json 为空或不存在")
        return
    print(f"[backfill_v2] 共处理 {len(entries)} 条记录")
    for entry in entries:
        _write_feedback(entry, dry_run=dry_run)
    if dry_run:
        print("\n[dry-run] 未写入任何文件")
    else:
        print("\n完成")


def main() -> None:
    parser = argparse.ArgumentParser(description="v2 多平台指标回写 trace")
    parser.add_argument("--username", required=True)
    parser.add_argument("--trace-id", default="", help="仅回写指定 trace_id，不填则处理全部")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.trace_id:
        backfill_by_trace_id(args.username, args.trace_id, dry_run=args.dry_run)
    else:
        backfill_all(args.username, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
