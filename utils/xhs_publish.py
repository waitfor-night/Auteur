"""
xhs_publish.py — 小红书发布全流程 CLI 脚本

三个子命令对应发布流程的三个阶段：

  prepare   从 trace 文件提取 trace_id 和视频路径，准备发布素材
  record    发布完成后，将 MCP 返回的 post_id 写入 publish_log
  refresh   查询小红书帖子最新指标并更新 publish_log

典型用法（顺序执行）：

  1. 生成视频后，查看待发布素材：
     python scripts/xhs_publish.py prepare --username zhaili

  2. 用 CC（Claude Code）调用 MCP publish_with_video 发布视频，
     拿到 post_id 和 xsec_token 后，记录到 publish_log：
     python scripts/xhs_publish.py record \\
         --username zhaili \\
         --trace-id ep_1775805609_c4c93ab6 \\
         --post-id 69d8ab5200000000230220ce \\
         --xsec-token "ABOJAmUV..." \\
         --xhs-account 斋黎 \\
         --xhs-user-id 6415565300000000120118f4 \\
         --title "川大望江校区 | 这才是真实的百年名校" \\
         --tags 四川大学 川大望江 成都校园打卡

  3. 定期刷新帖子指标（需将 get_feed_detail 的 JSON 响应保存为文件）：
     python scripts/xhs_publish.py refresh \\
         --username zhaili \\
         --post-id 69d8ab5200000000230220ce \\
         --detail-json /tmp/feed_detail.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.xhs_log_io import (
    write_publish_entry,
    update_post_stats,
    update_post_ids,
    read_publish_log,
    mark_deleted,
)


# ──────────────────────────────────────────────────────────────
# XHS 账号配置（workspace/<username>/xhs_config.json）
# ──────────────────────────────────────────────────────────────

def _xhs_config_path(username: str) -> Path:
    return _PROJECT_ROOT / "workspace" / username / "xhs_config.json"


def load_xhs_config(username: str) -> dict:
    """读取该 username 绑定的小红书账号配置。

    配置文件：workspace/<username>/xhs_config.json
    格式：{"xhs_account": "昵称", "xhs_user_id": "..."}

    若文件不存在或字段缺失，返回空字符串字段并打印提示。
    """
    p = _xhs_config_path(username)
    if not p.exists():
        print(
            f"[xhs_config] 未找到 {p}\n"
            f"  请先运行：python3 utils/xhs_publish.py set-account "
            f"--username {username} --xhs-account <昵称> --xhs-user-id <user_id>",
            file=sys.stderr,
        )
        return {"xhs_account": "", "xhs_user_id": ""}
    return json.loads(p.read_text(encoding="utf-8"))


def save_xhs_config(username: str, xhs_account: str, xhs_user_id: str) -> None:
    p = _xhs_config_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"xhs_account": xhs_account, "xhs_user_id": xhs_user_id},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ──────────────────────────────────────────────────────────────
# Trace 解析
# ──────────────────────────────────────────────────────────────

def _find_trace_files(username: str) -> list[Path]:
    """返回该 username 下所有 trace 文件，按时间戳降序（最新在前）。

    递归扫描子目录，兼容 sandbox 实验路径：
      workspace/<username>/trace/ep_*.json          ← 直接运行
      workspace/<username>/trace/task_0/phase1/ep_*.json  ← sandbox 实验
    """
    trace_dir = _PROJECT_ROOT / "workspace" / username / "trace"
    if not trace_dir.exists():
        return []
    files = sorted(trace_dir.rglob("ep_*.json"), reverse=True)
    return files


def _extract_video_from_trace(trace_path: Path) -> str | None:
    """从 trace JSON 中找到 merge_video_tool 的输出路径（绝对路径）。"""
    data = json.loads(trace_path.read_text(encoding="utf-8"))
    for item in data.get("history", []):
        for call in item.get("tool_calls", []):
            if call.get("tool_name") == "merge_video_tool" and call.get("status") == "success":
                output = call.get("outputs") or call.get("output") or ""
                if output:
                    return str(output)
    return None


def _extract_video_filename_from_trace(trace_path: Path) -> str | None:
    """从 trace JSON 中找到 merge_video_tool 的 save_path（文件名）。"""
    data = json.loads(trace_path.read_text(encoding="utf-8"))
    for item in data.get("history", []):
        for call in item.get("tool_calls", []):
            if call.get("tool_name") == "merge_video_tool" and call.get("status") == "success":
                save_path = (
                    call.get("inputs", {})
                    .get("kwargs", {})
                    .get("save_path", "")
                )
                if save_path:
                    return save_path
    return None


def _already_recorded(username: str, trace_id: str) -> bool:
    """检查该 trace_id 是否已有 publish_log 记录。"""
    for entry in read_publish_log(username):
        if entry.get("trace_id") == trace_id:
            return True
    return False




# ──────────────────────────────────────────────────────────────
# 子命令：prepare
# ──────────────────────────────────────────────────────────────

def cmd_prepare(args: argparse.Namespace) -> None:
    """列出未发布的 trace，输出 trace_id 和视频路径，供 CC 调用 MCP 发布。"""
    trace_files = _find_trace_files(args.username)
    if not trace_files:
        print(f"[prepare] 未找到 workspace/{args.username}/trace/ 下的 trace 文件。")
        return

    print(f"[prepare] username={args.username}，共 {len(trace_files)} 条 trace\n")
    unpublished = []
    for tf in trace_files:
        trace_id = tf.stem
        video_abs = _extract_video_from_trace(tf)
        video_name = _extract_video_filename_from_trace(tf)
        recorded = _already_recorded(args.username, trace_id)
        status = "已发布" if recorded else "未发布"
        rel = tf.relative_to(_PROJECT_ROOT / "workspace" / args.username / "trace")
        subdir = str(rel.parent) if rel.parent != Path(".") else ""
        print(f"  [{status}] {trace_id}" + (f"  ({subdir})" if subdir else ""))
        if not recorded and video_abs:
            unpublished.append((trace_id, video_abs, video_name))

    if unpublished:
        print("\n" + "─" * 60)
        print(f"[prepare] 以下 {len(unpublished)} 条 trace 尚未发布：\n")
        for trace_id, video_abs, video_name in unpublished:
            print(f"  trace_id   : {trace_id}")
            print(f"  video_path : {video_abs}")
            print(f"  video_name : {video_name}")
            print()
    else:
        print("\n[prepare] 所有 trace 均已记录在 publish_log 中。")


# ──────────────────────────────────────────────────────────────
# 子命令：record
# ──────────────────────────────────────────────────────────────

def cmd_record(args: argparse.Namespace) -> None:
    """发布完成后，将 post_id 等信息写入 publish_log。

    trace_id 与 result_video 的来源：
      - trace_id   ← trace 文件名（ep_<timestamp>_<hash>）
      - result_video ← trace 文件中 merge_video_tool 的 save_path
      若 --trace-id 对应的 trace 文件存在，result_video 自动从 trace 提取；
      也可通过 --result-video 手动覆盖。
    """
    # 自动推断 trace_path，并从中提取 result_video
    trace_path_obj = Path(args.trace_path) if args.trace_path else (
        _PROJECT_ROOT / "workspace" / args.username / "trace" / f"{args.trace_id}.json"
    )
    # 存相对于项目根目录的路径，避免绝对路径与机器绑定
    if trace_path_obj.exists():
        try:
            trace_path_str = str(trace_path_obj.relative_to(_PROJECT_ROOT))
        except ValueError:
            trace_path_str = str(trace_path_obj)
    else:
        trace_path_str = ""

    result_video = args.result_video
    if not result_video and trace_path_obj.exists():
        result_video = _extract_video_filename_from_trace(trace_path_obj)
    if not result_video:
        print("[record] 错误：无法从 trace 文件提取 result_video，请用 --result-video 手动指定。")
        sys.exit(1)

    if _already_recorded(args.username, args.trace_id):
        print(f"[record] 警告：trace_id={args.trace_id} 已有记录，跳过写入。")
        print("         如需更新，请直接编辑 publish_log.json 或使用 refresh 子命令刷新指标。")
        return

    # 账号信息：优先用命令行参数，否则从 xhs_config.json 自动读取
    cfg = load_xhs_config(args.username)
    xhs_account = args.xhs_account or cfg.get("xhs_account", "")
    xhs_user_id = args.xhs_user_id or cfg.get("xhs_user_id", "")
    if not xhs_account or not xhs_user_id:
        print("[record] 错误：未找到小红书账号配置，请先运行 set-account 子命令。")
        sys.exit(1)

    entry = write_publish_entry(
        username=args.username,
        trace_id=args.trace_id,
        trace_path=trace_path_str,
        result_video=result_video,
        xhs_account=xhs_account,
        xhs_user_id=xhs_user_id,
        post_id=args.post_id,
        xsec_token=args.xsec_token,
        title=args.title,
        tags=args.tags or [],
        publish_time=args.publish_time or None,
    )
    log_path = _PROJECT_ROOT / "workspace" / args.username / "publish_log.json"
    print(f"[record] 写入成功 → {log_path}")
    print(json.dumps(entry, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────────────────────
# 子命令：refresh
# ──────────────────────────────────────────────────────────────


def _unwrap_detail(payload: dict) -> dict:
    """兼容 get_feed_detail 的多层嵌套返回格式。"""
    for obj in [payload, payload.get("data", {}), payload.get("data", {}).get("data", {})]:
        if isinstance(obj, dict) and "note" in obj and "comments" in obj:
            return obj
    raise ValueError("无法解析 get_feed_detail 返回格式。")


def _load_detail_payload(value: str) -> dict:
    """将 --detail-json 的值解析为 dict，自动区分文件路径和 JSON 字符串。

    支持两种输入：
      - 文件路径：  /tmp/feed.json
      - JSON 字符串：'{"note": {...}, "comments": {...}}'
    """
    stripped = value.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    path = Path(stripped)
    if not path.exists():
        print(f"错误：找不到文件 {path}")
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def _apply_detail(username: str, post_id: str, payload: dict, label: str = "refresh") -> bool:
    """解析 payload 并调用 update_post_stats，打印结果，返回是否成功。"""
    detail = _unwrap_detail(payload)
    note = detail.get("note", {})
    interact = note.get("interactInfo", {})
    comments_data = detail.get("comments", {})
    raw_comments = comments_data.get("list", [])

    liked_count = int(interact.get("likedCount", 0) or 0)
    collected_count = int(interact.get("collectedCount", 0) or 0)
    comment_count = int(interact.get("commentCount", 0) or 0)
    share_count = int(interact.get("sharedCount", 0) or 0)

    updated = update_post_stats(
        username=username,
        post_id=post_id,
        liked_count=liked_count,
        collected_count=collected_count,
        comment_count=comment_count,
        share_count=share_count,
        comments=raw_comments,
    )
    if updated:
        print(
            f"[{label}] OK  {post_id}  "
            f"👍{liked_count} 🔖{collected_count} 💬{comment_count} 🔁{share_count}"
        )
    else:
        print(f"[{label}] 未找到 post_id={post_id}，请先执行 record 子命令。")
    return updated


def cmd_refresh(args: argparse.Namespace) -> None:
    """刷新单条帖子指标。--detail-json 可传文件路径或 JSON 字符串，两种方式均支持。

    方式 A（文件路径）：
      python scripts/xhs_publish.py refresh \\
          --username zhaili --post-id <id> \\
          --detail-json /tmp/feed.json

    方式 B（JSON 字符串，CC 直接粘贴 MCP 返回值）：
      python scripts/xhs_publish.py refresh \\
          --username zhaili --post-id <id> \\
          --detail-json '{"note": {...}, "comments": {...}}'
    """
    payload = _load_detail_payload(args.detail_json)
    _apply_detail(args.username, args.post_id, payload)


# ──────────────────────────────────────────────────────────────
# 子命令：refresh-all
# ──────────────────────────────────────────────────────────────

def cmd_refresh_all(args: argparse.Namespace) -> None:
    """批量刷新某 username 所有未删除帖子的指标。

    两种输入方式可同时使用，脚本按以下优先级匹配每条帖子：
      1. --details-json-str：CC 直接传入合并 JSON 字符串
         格式：'[{"post_id": "xxx", "detail": {...}}, ...]'
      2. --detail-dir：目录下的 <post_id>.json 文件

    若某条帖子在两种来源中都能找到，优先使用 --details-json-str 中的数据。
    两种来源都没有的帖子会被跳过并提示。

    方式 A（合并 JSON 字符串）：
      python scripts/xhs_publish.py refresh-all \\
          --username zhaili \\
          --details-json-str '[{"post_id":"69d8ab52...","detail":{...}},...]'

    方式 B（目录）：
      python scripts/xhs_publish.py refresh-all \\
          --username zhaili \\
          --detail-dir /tmp/feeds/

    方式 A+B 混用（字符串优先，目录补充）：
      python scripts/xhs_publish.py refresh-all \\
          --username zhaili \\
          --details-json-str '[...]' \\
          --detail-dir /tmp/feeds/
    """
    if not args.details_json_str and not args.detail_dir:
        print("[refresh-all] 错误：--details-json-str 和 --detail-dir 至少提供一个。")
        sys.exit(1)

    # 从 JSON 字符串构建 post_id → payload 映射
    str_map: dict[str, dict] = {}
    if args.details_json_str:
        items = json.loads(args.details_json_str.strip())
        for item in items:
            pid = item.get("post_id", "")
            detail = item.get("detail", {})
            if pid and detail:
                str_map[pid] = detail

    entries = read_publish_log(args.username)
    active = [e for e in entries if e.get("status", "published") != "deleted"]
    if not active:
        print(f"[refresh-all] username={args.username} 无需刷新的帖子。")
        return

    success, skipped, failed = 0, 0, 0
    for e in active:
        post_id = e.get("post_id", "")

        # 优先用字符串来源
        if post_id in str_map:
            payload = str_map[post_id]
        elif args.detail_dir:
            feed_file = Path(args.detail_dir) / f"{post_id}.json"
            if not feed_file.exists():
                print(f"[refresh-all] 跳过（无数据）: {post_id}")
                skipped += 1
                continue
            payload = json.loads(feed_file.read_text(encoding="utf-8"))
        else:
            print(f"[refresh-all] 跳过（无数据）: {post_id}")
            skipped += 1
            continue

        try:
            ok = _apply_detail(args.username, post_id, payload, label="refresh-all")
            if ok:
                success += 1
            else:
                failed += 1
        except Exception as exc:
            print(f"[refresh-all] 失败: {post_id}  错误: {exc}")
            failed += 1

    print(f"\n[refresh-all] 完成：成功 {success}，跳过 {skipped}，失败 {failed}")


# ──────────────────────────────────────────────────────────────
# 子命令：find-missing
# ──────────────────────────────────────────────────────────────

def cmd_find_missing(args: argparse.Namespace) -> None:
    """输出 publish_log 中缺少 post_id 或 xsec_token 的记录（JSON 数组）。

    供 CC 自动补充：
      1. CC 调用本命令拿到缺失列表
      2. CC 用每条记录的 title 调 MCP search_feeds 搜索
      3. CC 找到匹配帖子后调 update-ids 自动写入，无需询问用户
    """
    entries = read_publish_log(args.username)
    missing = [
        {"trace_id": e["trace_id"], "title": e.get("title", ""), "publish_time": e.get("publish_time", "")}
        for e in entries
        if not e.get("post_id") or not e.get("xsec_token")
    ]
    print(json.dumps(missing, ensure_ascii=False))


# ──────────────────────────────────────────────────────────────
# 子命令：update-ids
# ──────────────────────────────────────────────────────────────

def cmd_update_ids(args: argparse.Namespace) -> None:
    """补充发布时未能立即获取的 post_id 和 xsec_token，无需人工确认直接写入。"""
    updated = update_post_ids(args.username, args.trace_id, args.post_id, args.xsec_token)
    if updated:
        print(f"[update-ids] OK  trace_id={args.trace_id}  post_id={args.post_id}")
    else:
        print(f"[update-ids] 未找到 trace_id={args.trace_id} 的记录，请先执行 record 子命令。")


# ──────────────────────────────────────────────────────────────
# 子命令：mark-deleted
# ──────────────────────────────────────────────────────────────

def cmd_mark_deleted(args: argparse.Namespace) -> None:
    """将帖子标记为已删除。"""
    updated = mark_deleted(args.username, args.post_id)
    if updated:
        print(f"[mark-deleted] post_id={args.post_id} 已标记为 deleted。")
    else:
        print(f"[mark-deleted] 未找到 post_id={args.post_id} 的记录。")


# ──────────────────────────────────────────────────────────────
# 子命令：list
# ──────────────────────────────────────────────────────────────

def _mcp_publish_video(title: str, content: str, video_abs: str, tags: list) -> None:
    """通过 MCP JSON-RPC 调用 publish_with_video，失败时抛异常。"""
    import requests as _req

    MCP_URL = os.environ.get("XHS_MCP_URL", "http://localhost:18060/mcp")

    r = _req.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "xhs-publish", "version": "1.0"}},
    }, timeout=15)
    r.raise_for_status()
    session_id = r.headers.get("Mcp-Session-Id", "")
    if not session_id:
        raise RuntimeError("MCP initialize 未返回 session_id")

    r2 = _req.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "publish_with_video",
                   "arguments": {"title": title, "content": content,
                                 "video": video_abs, "tags": tags}},
    }, headers={"Mcp-Session-Id": session_id}, timeout=900)
    r2.raise_for_status()
    data = r2.json()
    if "error" in data:
        raise RuntimeError(f"publish_with_video 返回错误：{data['error']}")


def _find_feed_id(title: str, xhs_user_id: str) -> tuple[str, str]:
    """发布后查找 feed_id / xsec_token。
    先尝试 MCP user_profile（需要 xhs_user_id），失败则降级到 HTTP user/me。
    返回 (post_id, xsec_token)，找不到返回 ("", "")。
    """
    import requests as _req
    import time as _time

    MCP_URL = os.environ.get("XHS_MCP_URL", "http://localhost:18060/mcp")
    XHS_BASE = os.environ.get("XHS_MCP_BASE_URL", "http://localhost:18060/api/v1")

    def _search_feeds(feeds: list) -> tuple[str, str]:
        for feed in feeds:
            note_title = feed.get("noteCard", {}).get("displayTitle", "")
            if title in note_title or note_title in title:
                return feed.get("id", ""), feed.get("xsecToken", "")
        return "", ""

    for attempt in range(4):
        _time.sleep(10 if attempt else 0)
        print(f"[auto-publish] 查找帖子 ({attempt+1}/4)...", file=sys.stderr)

        # ── 方式 A：MCP user_profile（有 xhs_user_id 时使用）──
        if xhs_user_id:
            try:
                r_init = _req.post(MCP_URL, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "xhs-find", "version": "1.0"}},
                }, timeout=15)
                sid = r_init.headers.get("Mcp-Session-Id", "")

                # 先拿一个 xsec_token
                r_lf = _req.post(MCP_URL, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "list_feeds", "arguments": {}},
                }, headers={"Mcp-Session-Id": sid}, timeout=60)
                home_feeds_raw = r_lf.json().get("result", {}).get("content", [])
                any_xsec = ""
                for item in home_feeds_raw:
                    if item.get("type") == "text":
                        try:
                            hf = json.loads(item["text"])
                            feeds_list = hf if isinstance(hf, list) else hf.get("feeds", [])
                            any_xsec = next((f.get("xsecToken", "") for f in feeds_list if f.get("xsecToken")), "")
                        except Exception:
                            pass

                if any_xsec:
                    r_up = _req.post(MCP_URL, json={
                        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "user_profile",
                                   "arguments": {"user_id": xhs_user_id, "xsec_token": any_xsec}},
                    }, headers={"Mcp-Session-Id": sid}, timeout=60)
                    for item in r_up.json().get("result", {}).get("content", []):
                        if item.get("type") == "text":
                            try:
                                profile = json.loads(item["text"])
                                post_id, xsec = _search_feeds(profile.get("feeds", []))
                                if post_id:
                                    return post_id, xsec
                            except Exception:
                                pass
            except Exception as e:
                print(f"[auto-publish] MCP user_profile 失败：{e}", file=sys.stderr)

        # ── 方式 B：HTTP user/me 降级 ──
        try:
            me = _req.get(f"{XHS_BASE}/user/me", timeout=180)
            me.raise_for_status()
            post_id, xsec = _search_feeds(me.json()["data"]["data"].get("feeds", []))
            if post_id:
                return post_id, xsec
        except Exception as e:
            print(f"[auto-publish] HTTP user/me 失败：{e}", file=sys.stderr)

    return "", ""


def cmd_auto_publish(args: argparse.Namespace) -> None:
    """视频生成后一体化发布：读取 VideoAssistant 结果 JSON，通过 MCP JSON-RPC 发布视频，
    自动查询 feed_id，输出含 post_id / xsec_token 的结果 JSON 供 record 子命令使用。

    典型用法（CC 执行）：
      1. 运行 VideoAssistant，拿到 result dict（含 trace_id / result_video / xhs_title / xhs_tags）
      2. 将 result 写入临时 JSON 文件，或直接以 --result-json-str 传入字符串
      3. 本命令直接完成发布，输出含 post_id / xsec_token 的 JSON
      4. 用输出的 post_id / xsec_token 执行 record 子命令写入 publish_log

    输出格式（stdout，JSON）：
    {
      "trace_id": "ep_...",
      "trace_path": "workspace/<username>/trace/ep_....json",
      "result_video": "workspace/.../merged_xxx.mp4",
      "post_id": "...",
      "xsec_token": "...",
      "title": "...",
      "tags": ["...", "..."],
      "xhs_account": "...",
      "xhs_user_id": "..."
    }
    """
    import requests

    XHS_BASE = os.environ.get("XHS_MCP_BASE_URL", "http://localhost:18060/api/v1")

    # 解析 VideoAssistant result（支持 JSON 字符串、文件路径、@文件路径）
    result_str = args.result_json_str.strip()
    if result_str.startswith("{"):
        va_result = json.loads(result_str)
    else:
        file_path = result_str.lstrip("@")  # 支持 @/path/to/file.json 语法
        path = Path(file_path)
        if not path.exists():
            print(f"错误：找不到文件 {path}", file=sys.stderr)
            sys.exit(1)
        va_result = json.loads(path.read_text(encoding="utf-8"))

    if not va_result.get("success"):
        print(f"错误：VideoAssistant 未成功，error={va_result.get('error')}", file=sys.stderr)
        sys.exit(1)

    trace_id = va_result.get("trace_id", "")
    # 推断 trace_path：优先从 result 取，否则按默认路径查找
    # 统一存相对路径，避免绝对路径与机器绑定
    trace_path = va_result.get("trace_path", "")
    if not trace_path and trace_id:
        candidate = _PROJECT_ROOT / "workspace" / args.username / "trace" / f"{trace_id}.json"
        if candidate.exists():
            trace_path = str(candidate.relative_to(_PROJECT_ROOT))
    elif trace_path:
        try:
            trace_path = str(Path(trace_path).relative_to(_PROJECT_ROOT))
        except ValueError:
            pass  # 路径不在项目根目录下时保留原值
    result_video = va_result.get("result_video", "")
    title = va_result.get("xhs_title", "") or args.title or ""
    tags = va_result.get("xhs_tags", []) or (args.tags or [])

    if not result_video:
        print("错误：result 中没有 result_video，视频可能未生成成功。", file=sys.stderr)
        sys.exit(1)

    # 解析视频绝对路径
    src = Path(result_video)
    if not src.is_absolute():
        src = _PROJECT_ROOT / src
    # result_video 可能只是文件名，在 workspace/output 下递归搜索实际文件
    if not src.exists():
        matches = list((_PROJECT_ROOT / "workspace" / "output").rglob(Path(result_video).name))
        if matches:
            src = max(matches, key=lambda p: p.stat().st_mtime)
    if not src.exists():
        print(f"错误：视频文件不存在：{src}", file=sys.stderr)
        sys.exit(1)
    video_abs = str(src.resolve())

    # ── 检查 MCP 服务登录状态 ──────────────────────────────────
    try:
        login_resp = requests.get(f"{XHS_BASE}/login/status", timeout=30)
        login_resp.raise_for_status()
        if not login_resp.json()["data"].get("is_logged_in"):
            print("错误：xiaohongshu-mcp 未登录，请先启动服务并扫码登录", file=sys.stderr)
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("错误：无法连接 xiaohongshu-mcp 服务，请确认已启动：cd xiaohongshu-mcp && nohup ./start.sh > /tmp/xhs-mcp.log 2>&1 &", file=sys.stderr)
        sys.exit(1)

    # 小红书标题限 20 字，超长自动截断
    if len(title) > 20:
        title = title[:19] + "…"
        print(f"[auto-publish] 标题超长，已截断为：{title}", file=sys.stderr)
    content = " ".join(f"#{t}" for t in tags)

    # 账号信息：优先用命令行参数，否则从 xhs_config.json 读取（监控用）
    cfg = {}
    try:
        cfg = load_xhs_config(args.username)
    except Exception:
        pass
    xhs_account = args.xhs_account or cfg.get("xhs_account", "")
    xhs_user_id = args.xhs_user_id or cfg.get("xhs_user_id", "")

    # ── MCP JSON-RPC 发布 ──────────────────────────────────────
    print(f"[auto-publish] 发布中：{title}", file=sys.stderr)
    try:
        _mcp_publish_video(title, content, video_abs, tags)
    except Exception as e:
        print(f"错误：MCP publish_with_video 失败：{e}", file=sys.stderr)
        sys.exit(1)
    print("[auto-publish] 发布完成，查找 feed_id...", file=sys.stderr)

    # ── 查找 feed_id ───────────────────────────────────────────
    post_id, xsec_token = _find_feed_id(title, xhs_user_id)
    if post_id:
        print(f"[auto-publish] feed_id={post_id}", file=sys.stderr)
    else:
        print("[auto-publish] 警告：未找到 feed_id，可后续用 find-missing + update-ids 补充。", file=sys.stderr)

    publish_params = {
        "trace_id": trace_id,
        "trace_path": trace_path,
        "result_video": result_video,
        "post_id": post_id,
        "xsec_token": xsec_token,
        "title": title,
        "tags": tags,
        "xhs_account": xhs_account,
        "xhs_user_id": xhs_user_id,
    }
    print(json.dumps(publish_params, ensure_ascii=False, indent=2))


def cmd_set_account(args: argparse.Namespace) -> None:
    """绑定该 username 对应的小红书账号，写入 workspace/<username>/xhs_config.json。"""
    save_xhs_config(args.username, args.xhs_account, args.xhs_user_id)
    p = _xhs_config_path(args.username)
    print(f"[set-account] 已保存 → {p}")
    print(f"  xhs_account : {args.xhs_account}")
    print(f"  xhs_user_id : {args.xhs_user_id}")


def cmd_pending_refresh(args: argparse.Namespace) -> None:
    """输出所有 status=published 帖子的 post_id 与 xsec_token（JSON 数组，供 CC 循环调用 MCP）。"""
    import json as _json
    entries = read_publish_log(args.username)
    pending = [
        {"post_id": e["post_id"], "xsec_token": e.get("xsec_token", "")}
        for e in entries
        if e.get("status", "published") == "published"
    ]
    print(_json.dumps(pending, ensure_ascii=False))


def cmd_list(args: argparse.Namespace) -> None:
    """列出指定 username 的所有 publish_log 记录（摘要）。"""
    entries = read_publish_log(args.username)
    if not entries:
        print(f"[list] workspace/{args.username}/publish_log.json 暂无记录。")
        return
    print(f"[list] username={args.username}，共 {len(entries)} 条记录：\n")
    for i, e in enumerate(entries, 1):
        status = e.get("status", "published")
        stats = e.get("stats", {})
        print(f"  [{i}] trace_id    : {e.get('trace_id', '-')}")
        print(f"       result_video : {e.get('result_video', '-')}")
        print(f"       post_id      : {e.get('post_id', '-')}")
        print(f"       title        : {e.get('title', '-')}")
        print(f"       status       : {status}")
        print(f"       stats        : 👍{stats.get('liked_count',0)}  🔖{stats.get('collected_count',0)}  💬{stats.get('comment_count',0)}")
        print(f"       publish_time : {e.get('publish_time', '-')}")
        print()


# ──────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhs_publish",
        description="小红书发布全流程管理：trace → publish_log → 指标刷新",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # prepare
    p_prepare = sub.add_parser("prepare", help="列出未发布的 trace 及待发布视频路径")
    p_prepare.add_argument("--username", required=True, help="workspace 用户名")

    # set-account
    p_sa = sub.add_parser("set-account", help="绑定该 username 对应的小红书账号（保存到 xhs_config.json）")
    p_sa.add_argument("--username", required=True)
    p_sa.add_argument("--xhs-account", required=True, help="小红书账号昵称")
    p_sa.add_argument("--xhs-user-id", required=True, help="小红书 user_id")

    # record
    p_record = sub.add_parser("record", help="发布后将 post_id 写入 publish_log")
    p_record.add_argument("--username", required=True)
    p_record.add_argument("--trace-id", required=True, help="ep_<timestamp>_<hash>")
    p_record.add_argument("--post-id", default="", help="小红书帖子 id（可留空，后续用 update-ids 补充）")
    p_record.add_argument("--xsec-token", default="", help="帖子 xsec_token（可留空，后续用 update-ids 补充）")
    p_record.add_argument("--xhs-account", default="", help="小红书账号昵称（留空则从 xhs_config.json 读取）")
    p_record.add_argument("--xhs-user-id", default="", help="小红书 user_id（留空则从 xhs_config.json 读取）")
    p_record.add_argument("--title", required=True, help="帖子标题")
    p_record.add_argument("--tags", nargs="*", default=[], help="话题标签（空格分隔）")
    p_record.add_argument("--trace-path", default="", help="trace 文件完整路径（留空则按默认路径自动推断）")
    p_record.add_argument("--result-video", default="", help="视频文件名（留空则自动从 trace 提取）")
    p_record.add_argument("--publish-time", default="", help="发布时间 YYYY-MM-DD HH:MM:SS（留空取当前时间）")

    # refresh
    p_refresh = sub.add_parser("refresh", help="刷新单条帖子指标（支持文件路径或 JSON 字符串）")
    p_refresh.add_argument("--username", required=True)
    p_refresh.add_argument("--post-id", required=True)
    p_refresh.add_argument(
        "--detail-json", required=True,
        help="get_feed_detail 返回值：文件路径（/tmp/feed.json）或 JSON 字符串（'{...}'）均可",
    )

    # refresh-all
    p_rall = sub.add_parser("refresh-all", help="批量刷新某用户所有未删除帖子（支持字符串或目录，可混用）")
    p_rall.add_argument("--username", required=True)
    p_rall.add_argument(
        "--details-json-str", default="",
        help="合并 JSON 字符串，格式：'[{\"post_id\":\"xxx\",\"detail\":{...}},...]'",
    )
    p_rall.add_argument(
        "--detail-dir", default="",
        help="目录路径，目录下含 <post_id>.json 文件",
    )

    # find-missing
    p_fm = sub.add_parser("find-missing", help="输出缺少 post_id/xsec_token 的记录（供 CC 自动搜索补充）")
    p_fm.add_argument("--username", required=True)

    # update-ids
    p_uid = sub.add_parser("update-ids", help="补充发布时未能立即获取的 post_id 和 xsec_token")
    p_uid.add_argument("--username", required=True)
    p_uid.add_argument("--trace-id", required=True, help="ep_<timestamp>_<hash>")
    p_uid.add_argument("--post-id", required=True, help="小红书帖子 id")
    p_uid.add_argument("--xsec-token", required=True, help="帖子 xsec_token")

    # mark-deleted
    p_del = sub.add_parser("mark-deleted", help="将帖子标记为已删除")
    p_del.add_argument("--username", required=True)
    p_del.add_argument("--post-id", required=True)

    # auto-publish
    p_ap = sub.add_parser(
        "auto-publish",
        help="读取 VideoAssistant 结果，输出 MCP 发布所需参数（JSON）",
    )
    p_ap.add_argument("--username", required=True)
    p_ap.add_argument(
        "--result-json-str", required=True,
        help="VideoAssistant.run() 返回的 dict：文件路径或 JSON 字符串均可",
    )
    p_ap.add_argument("--xhs-account", default="", help="小红书账号昵称（留空则从 xhs_config.json 读取）")
    p_ap.add_argument("--xhs-user-id", default="", help="小红书 user_id（留空则从 xhs_config.json 读取）")
    p_ap.add_argument("--title", default="", help="覆盖自动生成的标题（可选）")
    p_ap.add_argument("--tags", nargs="*", default=[], help="覆盖自动生成的标签（可选）")

    # pending-refresh
    p_pending = sub.add_parser("pending-refresh", help="输出所有 published 帖子的 post_id 与 xsec_token（JSON）")
    p_pending.add_argument("--username", required=True)

    # list
    p_list = sub.add_parser("list", help="列出 publish_log 所有记录摘要")
    p_list.add_argument("--username", required=True)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    {
        "set-account": cmd_set_account,
        "prepare": cmd_prepare,
        "record": cmd_record,
        "refresh": cmd_refresh,
        "refresh-all": cmd_refresh_all,
        "find-missing": cmd_find_missing,
        "update-ids": cmd_update_ids,
        "mark-deleted": cmd_mark_deleted,
        "auto-publish": cmd_auto_publish,
        "pending-refresh": cmd_pending_refresh,
        "list": cmd_list,
    }[args.command](args)


if __name__ == "__main__":
    main()
