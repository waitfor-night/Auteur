#!/usr/bin/env python3
"""
publish_record_v2.py — sau 发布完成后，将各平台结果写入 publish_log_v2.json。

用法（发布完所有平台后调用一次）：
  python utils/publish_record_v2.py record \
      --username <username> \
      --trace-id ep_xxx \
      --trace-path workspace/<username>/trace/ep_xxx.json \
      --result-video /path/to/video.mp4 \
      --title "标题" \
      --tags tag1 tag2 tag3 \
      --platforms douyin:zhiali-douyin bilibili:zhiali-bilibili kuaishou:zhiali-kuaishou xiaohongshu:zhiali-xiaohongshu

  # 补全单个平台的 post_id（TikHub backfill 后可跳过，由 xhs_refresh_tikhub.py 自动处理）
  python utils/publish_record_v2.py update-id \
      --username <username> \
      --trace-id ep_xxx \
      --platform xiaohongshu \
      --post-id abc123 \
      [--xsec-token xxx]

  # 查看所有记录
  python utils/publish_record_v2.py list --username <username>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.xhs_log_io_v2 import (
    write_publish_entry,
    update_platform_post_id,
    read_publish_log,
    get_entry_by_trace,
)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────
# 子命令实现
# ──────────────────────────────────────────────

def cmd_record(args: argparse.Namespace) -> None:
    """记录一次多平台发布。"""
    # 解析 --platforms douyin:zhiali-douyin bilibili:zhiali-bilibili ...
    platforms: dict = {}
    for spec in args.platforms:
        if ":" in spec:
            platform, account = spec.split(":", 1)
        else:
            platform, account = spec, spec
        platforms[platform] = {
            "account": account,
            "title": args.title,
            "tags": args.tags,
        }

    if not platforms:
        print("[error] 至少需要指定一个平台（--platforms douyin:账号 ...）", file=sys.stderr)
        sys.exit(1)

    # 检查是否已存在
    existing = get_entry_by_trace(args.username, args.trace_id)
    if existing:
        print(f"[warn] trace_id={args.trace_id} 已存在，跳过写入")
        print(json.dumps(existing, ensure_ascii=False, indent=2))
        return

    entry = write_publish_entry(
        username=args.username,
        trace_id=args.trace_id,
        trace_path=args.trace_path,
        result_video=args.result_video,
        platforms=platforms,
        publish_time=_now_str(),
    )
    print(f"[record] 已写入 trace_id={args.trace_id}，平台：{list(platforms.keys())}")
    print(json.dumps(entry, ensure_ascii=False, indent=2))


def cmd_update_id(args: argparse.Namespace) -> None:
    """补全某平台的 post_id。"""
    ok = update_platform_post_id(
        username=args.username,
        trace_id=args.trace_id,
        platform=args.platform,
        post_id=args.post_id,
        xsec_token=getattr(args, "xsec_token", "") or "",
    )
    if ok:
        print(f"[update-id] ✓ {args.platform} post_id={args.post_id}")
    else:
        print(f"[update-id] ✗ 未找到 trace_id={args.trace_id}", file=sys.stderr)
        sys.exit(1)


def cmd_retry_publish(args: argparse.Namespace) -> None:
    """检测缺失平台并重试发布，成功才写入日志。"""
    import subprocess

    entries = read_publish_log(args.username)
    if not entries:
        print("（无记录）")
        return

    # 从日志中已有记录动态推断各平台账号名，避免硬编码
    def _infer_account(platform: str) -> str:
        for e in entries:
            plat = e.get("platforms", {}).get(platform, {})
            if plat.get("account"):
                return plat["account"]
        return platform  # 兜底

    TARGET_PLATFORMS: dict[str, dict] = {
        p: {"account": _infer_account(p), "cmd": p}
        for p in ("douyin", "bilibili", "kuaishou")
    }

    for entry in entries:
        existing_platforms = set(entry.get("platforms", {}).keys())
        missing = [p for p in TARGET_PLATFORMS if p not in existing_platforms]
        if not missing:
            continue

        video = entry.get("result_video", "")
        title = (list(entry["platforms"].values())[0].get("title", "") if entry.get("platforms") else "")
        tags_list = (list(entry["platforms"].values())[0].get("tags", []) if entry.get("platforms") else [])
        tags_str = ",".join(tags_list)
        trace_id = entry.get("trace_id", "")

        if not video or not title:
            print(f"[retry] {trace_id}: 缺少 result_video 或 title，跳过")
            continue

        print(f"[retry] {trace_id}: 缺失平台 {missing}，开始重试...")

        for platform in missing:
            cfg = TARGET_PLATFORMS[platform]
            account = cfg["account"]
            cmd_name = cfg["cmd"]

            if platform == "bilibili":
                cmd = [
                    "sau", cmd_name, "upload-video",
                    "--account", account,
                    "--file", video,
                    "--title", title[:80],
                    "--desc", title[:80],
                    "--tags", tags_str,
                    "--tid", "25",
                ]
            else:
                cmd = [
                    "sau", cmd_name, "upload-video",
                    "--account", account,
                    "--file", video,
                    "--title", title[:30],
                    "--tags", tags_str,
                ]

            print(f"  → {platform}: {' '.join(cmd[:5])} ...")
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
                success = result.returncode == 0
            except subprocess.TimeoutExpired:
                success = False
                print(f"  ✗ {platform}: 超时")

            if success:
                print(f"  ✓ {platform}: 发布成功")
                from utils.xhs_log_io_v2 import _read_all, _write_all
                log = _read_all(args.username)
                for e in log:
                    if e.get("trace_id") == trace_id:
                        if platform not in e.get("published_platforms", []):
                            e.setdefault("published_platforms", []).append(platform)
                        e.setdefault("platforms", {})[platform] = {
                            "account": account,
                            "post_id": "",
                            "xsec_token": "",
                            "title": title,
                            "tags": tags_list,
                            "refresh_status": "active",
                            "last_updated": None,
                            "stats": {"liked_count": 0, "collected_count": 0,
                                      "comment_count": 0, "share_count": 0,
                                      "view_count": 0, "comments": []},
                            "stats_history": [],
                        }
                        break
                _write_all(args.username, log)
            else:
                print(f"  ✗ {platform}: 发布失败，不写入日志")


def cmd_list(args: argparse.Namespace) -> None:
    entries = read_publish_log(args.username)
    if not entries:
        print("（无记录）")
        return
    for e in entries:
        platforms_info = ", ".join(
            f"{p}({v.get('post_id') or '无id'})"
            for p, v in e.get("platforms", {}).items()
        )
        print(f"[{e.get('publish_time','')}] {e.get('trace_id','')} | {platforms_info}")


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="多平台发布日志 v2")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # record
    p_record = sub.add_parser("record", help="记录一次发布")
    p_record.add_argument("--username", required=True)
    p_record.add_argument("--trace-id", required=True)
    p_record.add_argument("--trace-path", required=True)
    p_record.add_argument("--result-video", required=True)
    p_record.add_argument("--title", required=True)
    p_record.add_argument("--tags", nargs="*", default=[])
    p_record.add_argument(
        "--platforms", nargs="+", required=True,
        help="格式：platform:account，如 douyin:zhiali-douyin bilibili:zhiali-bilibili",
    )

    # update-id
    p_uid = sub.add_parser("update-id", help="补全 post_id")
    p_uid.add_argument("--username", required=True)
    p_uid.add_argument("--trace-id", required=True)
    p_uid.add_argument("--platform", required=True)
    p_uid.add_argument("--post-id", required=True)
    p_uid.add_argument("--xsec-token", default="")

    # list
    p_list = sub.add_parser("list", help="列出所有记录")
    p_list.add_argument("--username", required=True)

    # retry-publish
    p_retry = sub.add_parser("retry-publish", help="检测缺失平台并重试发布，成功才写入日志")
    p_retry.add_argument("--username", required=True)

    args = parser.parse_args()
    {"record": cmd_record, "update-id": cmd_update_id, "list": cmd_list,
     "retry-publish": cmd_retry_publish}[args.cmd](args)


if __name__ == "__main__":
    main()
