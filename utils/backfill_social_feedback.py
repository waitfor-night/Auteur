"""
backfill_social_feedback.py — 将 publish_log 中的社交媒体指标写回对应 trace 文件。

对每条 publish_log 记录：
  - 在 trace 文件顶层添加 "social-media-feedback" 字段
  - 内容包含：title / tags / post_id / xhs_account / publish_time /
              stats（最新快照）/ stats_history（历史序列）

用法：
    python3 utils/backfill_social_feedback.py --username zhaili
    python3 utils/backfill_social_feedback.py --username zhaili --start-title "豪宅变深大宿舍"
    python3 utils/backfill_social_feedback.py --username zhaili --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_publish_log(username: str) -> list[dict]:
    p = _PROJECT_ROOT / "workspace" / username / "publish_log.json"
    if not p.exists():
        raise FileNotFoundError(f"publish_log not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _load_trace(trace_path: str) -> tuple[Path, dict]:
    p = Path(trace_path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / trace_path
    if not p.exists():
        raise FileNotFoundError(f"trace file not found: {p}")
    return p, json.loads(p.read_text(encoding="utf-8"))


def _build_feedback(entry: dict) -> dict:
    return {
        "title": entry.get("title", ""),
        "tags": entry.get("tags", []),
        "post_id": entry.get("post_id", ""),
        "xhs_account": entry.get("xhs_account", ""),
        "publish_time": entry.get("publish_time", ""),
        "stats": entry.get("stats", {}),
        "stats_history": entry.get("stats_history", []),
        "last_updated": entry.get("last_updated"),
    }


def backfill_by_post_ids(username: str, post_ids: list[str]) -> None:
    """刷新完成后，只更新本次刷新过的帖子对应的 trace 文件。"""
    if not post_ids:
        return
    entries = _load_publish_log(username)
    post_id_set = set(post_ids)
    for entry in entries:
        if entry.get("post_id") not in post_id_set:
            continue
        trace_path = entry.get("trace_path", "")
        if not trace_path:
            continue
        try:
            p, trace = _load_trace(trace_path)
        except FileNotFoundError:
            continue
        trace["social-media-feedback"] = _build_feedback(entry)
        p.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[backfill] {entry.get('trace_id','')} | {entry.get('title','')} -> 已更新")


def backfill(username: str, start_title: str | None = None, dry_run: bool = False) -> None:
    entries = _load_publish_log(username)

    # 找到起始索引
    start_idx = 0
    if start_title:
        for i, e in enumerate(entries):
            if start_title in e.get("title", ""):
                start_idx = i
                break
        else:
            print(f"[WARN] 未找到包含 '{start_title}' 的条目，从头开始处理所有记录")

    targets = entries[start_idx:]
    print(f"共处理 {len(targets)} 条（从第 {start_idx} 条 '{entries[start_idx].get('title', '')}' 开始）\n")

    for entry in targets:
        trace_id = entry.get("trace_id", "")
        trace_path = entry.get("trace_path", "")
        title = entry.get("title", "")

        if not trace_path:
            print(f"[SKIP] {trace_id} — trace_path 为空")
            continue

        try:
            p, trace = _load_trace(trace_path)
        except FileNotFoundError as e:
            print(f"[SKIP] {trace_id} — {e}")
            continue

        feedback = _build_feedback(entry)

        if "social-media-feedback" in trace:
            print(f"[UPDATE] {trace_id} | {title}")
        else:
            print(f"[ADD]    {trace_id} | {title}")

        trace["social-media-feedback"] = feedback

        if not dry_run:
            p.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"         -> 已写入 {p}")

    if dry_run:
        print("\n[dry-run] 未写入任何文件")
    else:
        print("\n完成")


def main() -> None:
    parser = argparse.ArgumentParser(description="将 publish_log 社交指标回填到 trace 文件")
    parser.add_argument("--username", required=True, help="workspace 用户名")
    parser.add_argument(
        "--start-title",
        default="豪宅变深大宿舍",
        help="从包含该字符串的 title 开始处理（默认：豪宅变深大宿舍）",
    )
    parser.add_argument("--all", action="store_true", help="处理全部记录，忽略 --start-title")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = parser.parse_args()

    start_title = None if args.all else args.start_title
    backfill(username=args.username, start_title=start_title, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
