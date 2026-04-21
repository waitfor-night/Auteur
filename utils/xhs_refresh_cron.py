#!/usr/bin/env python3
"""
定时刷新小红书帖子指标的 cron 脚本。
用法：python utils/xhs_refresh_cron.py --username zhaili
"""
import argparse
import json
import os
import subprocess
import sys
import requests
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MCP_URL = os.environ.get("XHS_MCP_URL", "http://localhost:18060/mcp")
PYTHON = sys.executable


def mcp_init_session() -> str:
    resp = requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "xhs-cron", "version": "1.0"}
        }
    }, timeout=10)
    resp.raise_for_status()
    session_id = resp.headers.get("Mcp-Session-Id", "")
    if not session_id:
        raise RuntimeError(f"未拿到 Mcp-Session-Id，响应：{resp.text}")
    return session_id


def mcp_user_profile(session_id: str, user_id: str, xsec_token: str, timeout: int = 30) -> dict:
    """拉取用户主页，返回包含 feeds 列表的 dict。"""
    resp = requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {
            "name": "user_profile",
            "arguments": {"user_id": user_id, "xsec_token": xsec_token}
        }
    }, headers={"Mcp-Session-Id": session_id}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"user_profile 出错：{data['error']}")
    content = data.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except Exception:
                return {}
    return {}


def mcp_list_feeds(session_id: str, timeout: int = 30) -> list:
    """拉取首页 feeds，用于获取任意帖子的 xsec_token 来访问用户主页。"""
    resp = requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "list_feeds", "arguments": {}}
    }, headers={"Mcp-Session-Id": session_id}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            try:
                result = json.loads(item["text"])
                return result if isinstance(result, list) else result.get("feeds", [])
            except Exception:
                return []
    return []


def backfill_missing_post_ids(username: str, session_id: str, xhs_user_id: str) -> None:
    """通过 user_profile 拉取自己发布的帖子列表，补全 publish_log 中缺少 post_id 的条目。"""
    result = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "utils/xhs_publish.py"),
         "find-missing", "--username", username],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )
    if result.returncode != 0:
        print(f"[backfill] find-missing 失败：{result.stderr}", file=sys.stderr)
        return
    try:
        missing = json.loads(result.stdout.strip())
    except Exception:
        return
    if not missing:
        return
    print(f"[backfill] 缺失 post_id 的帖子数：{len(missing)}")

    # 先拿一个 xsec_token 用于访问 user_profile
    try:
        home_feeds = mcp_list_feeds(session_id)
        any_xsec = next((f.get("xsecToken", "") for f in home_feeds if f.get("xsecToken")), "")
        if not any_xsec:
            raise RuntimeError("list_feeds 未返回 xsecToken")
    except Exception as e:
        print(f"[backfill] 获取 xsec_token 失败：{e}", file=sys.stderr)
        return

    # 拉取用户主页帖子列表
    try:
        profile = mcp_user_profile(session_id, xhs_user_id, any_xsec)
        user_feeds = profile.get("feeds", [])
    except Exception as e:
        print(f"[backfill] user_profile 失败：{e}", file=sys.stderr)
        return

    # 建立标题 → (post_id, xsec_token) 映射
    feed_map = {}
    for f in user_feeds:
        card = f.get("noteCard", {})
        title = card.get("displayTitle", "")
        if title:
            feed_map[title] = (f.get("id", ""), f.get("xsecToken", ""))

    for entry in missing:
        trace_id = entry["trace_id"]
        title = entry.get("title", "")
        if not title:
            continue
        # 精确匹配
        match = feed_map.get(title)
        if not match:
            # 模糊匹配：取标题前10字
            for ft, val in feed_map.items():
                if title[:10] in ft or ft[:10] in title:
                    match = val
                    break
        if not match or not match[0]:
            print(f"[backfill] 未在主页找到「{title[:20]}」")
            continue
        post_id, xsec_token = match
        upd = subprocess.run(
            [PYTHON, str(PROJECT_ROOT / "utils/xhs_publish.py"),
             "update-ids", "--username", username,
             "--trace-id", trace_id,
             "--post-id", post_id,
             "--xsec-token", xsec_token],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT)
        )
        print(upd.stdout.strip() or f"[backfill] trace_id={trace_id} → post_id={post_id}")


def mcp_get_feed_detail(session_id: str, post_id: str, xsec_token: str) -> dict:
    resp = requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "get_feed_detail",
            "arguments": {"feed_id": post_id, "xsec_token": xsec_token}
        }
    }, headers={"Mcp-Session-Id": session_id}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"get_feed_detail 出错：{data['error']}")
    content = data.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            return json.loads(item["text"])
    raise RuntimeError(f"get_feed_detail 无有效返回：{data}")


def run_pending_refresh(username: str) -> list:
    result = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "utils/xhs_publish.py"),
         "pending-refresh", "--username", username],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )
    if result.returncode != 0:
        raise RuntimeError(f"pending-refresh 失败：{result.stderr}")
    return json.loads(result.stdout.strip())


def run_refresh_all(username: str, details_json_str: str):
    result = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "utils/xhs_publish.py"),
         "refresh-all", "--username", username,
         "--details-json-str", details_json_str],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"[WARN] refresh-all stderr: {result.stderr}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    args = parser.parse_args()

    session_id = mcp_init_session()

    # 先补全缺失的 post_id（从 xhs_config.json 读取 user_id）
    try:
        xhs_config_path = PROJECT_ROOT / "workspace" / args.username / "xhs_config.json"
        xhs_user_id = json.loads(xhs_config_path.read_text(encoding="utf-8")).get("xhs_user_id", "")
        if xhs_user_id:
            backfill_missing_post_ids(args.username, session_id, xhs_user_id)
        else:
            print("[backfill] xhs_user_id 未配置，跳过补全", file=sys.stderr)
    except Exception as e:
        print(f"[backfill] 异常（不影响刷新）：{e}", file=sys.stderr)

    posts = run_pending_refresh(args.username)
    if not posts:
        print("[xhs-cron] 无待刷新帖子，退出。")
        return

    print(f"[xhs-cron] 待刷新帖子数：{len(posts)}")

    details = []
    for p in posts:
        post_id, xsec_token = p["post_id"], p["xsec_token"]
        if not post_id or not xsec_token:
            print(f"[xhs-cron] 跳过（post_id 或 xsec_token 为空，请用 update-ids 补充）: trace 相关记录")
            continue
        try:
            detail = mcp_get_feed_detail(session_id, post_id, xsec_token)
            details.append({"post_id": post_id, "detail": detail})
            print(f"[xhs-cron] 获取 {post_id} 成功")
        except Exception as e:
            print(f"[xhs-cron] 获取 {post_id} 失败：{e}", file=sys.stderr)

    if details:
        run_refresh_all(args.username, json.dumps(details, ensure_ascii=False))
        # 刷新完成后，把最新指标同步写回对应 trace 文件
        try:
            from utils.backfill_social_feedback import backfill_by_post_ids
            refreshed_post_ids = [d["post_id"] for d in details]
            backfill_by_post_ids(args.username, refreshed_post_ids)
        except Exception as e:
            print(f"[xhs-cron] backfill trace 异常（不影响主流程）：{e}", file=sys.stderr)
    else:
        print("[xhs-cron] 所有帖子获取失败，跳过写入。")


if __name__ == "__main__":
    main()
