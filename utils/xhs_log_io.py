"""
publish_log.py — 记录 CC 发布到小红书后的帖子信息，并支持后续刷新指标。

文件格式：workspace/<username>/publish_log.json（JSON 数组，格式化缩进）

每条记录字段：
  username        生成视频的用户名（workspace 目录标识）
  trace_id        生成该视频的 trace episode id（ep_<timestamp>_<hash>）
  trace_path      trace 文件完整路径（workspace/<username>/trace/ep_*.json）
  result_video    视频文件名
  xhs_account     发布的小红书账号昵称
  xhs_user_id     发布的小红书 user_id
  post_id         小红书帖子 id（由 MCP publish_with_video 返回）
  xsec_token      帖子的 xsec_token（用于后续查询详情）
  title           帖子标题
  tags            话题标签列表
  publish_time    发布时间（YYYY-MM-DD HH:MM:SS）
  status          帖子状态：published / deleted（可选，默认 published）
  stats           帖子最新指标快照（每次 refresh 覆盖）：
    liked_count       点赞数
    collected_count   收藏数
    comment_count     评论总数
    share_count       分享数
    comments          评论树列表，每项：
      comment_id        评论唯一 id
      author            评论者昵称
      content           评论内容
      like_count        评论点赞数
      create_time       评论时间（YYYY-MM-DD HH:MM:SS）
      sub_comments      子回复列表（同结构递归）
  stats_history   历史指标序列（每次 refresh 追加，不覆盖）：
    每项格式与 stats 相同，额外包含：
      observed_at       本次观测时间（YYYY-MM-DD HH:MM:SS）
  last_updated    stats 最后更新时间（YYYY-MM-DD HH:MM:SS）
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _log_path(username: str) -> Path:
    p = _PROJECT_ROOT / "workspace" / username / "publish_log.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_all(username: str) -> list[dict]:
    p = _log_path(username)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return json.loads(text)


def _write_all(username: str, entries: list[dict]) -> None:
    _log_path(username).write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _ts_to_str(ts) -> str:
    """毫秒时间戳 → 'YYYY-MM-DD HH:MM:SS'，失败返回原值字符串。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_comment(raw: dict) -> dict:
    """将 get_feed_detail 返回的评论对象解析为树结构节点。"""
    return {
        "comment_id": raw.get("id", ""),
        "author": raw.get("userInfo", {}).get("nickname", ""),
        "content": raw.get("content", ""),
        "like_count": int(raw.get("likeCount", 0) or 0),
        "create_time": _ts_to_str(raw.get("createTime", "")),
        "sub_comments": [_parse_comment(c) for c in (raw.get("subComments") or [])],
    }


# ──────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────

def write_publish_entry(
    username: str,
    trace_id: str,
    trace_path: str,
    result_video: str,
    xhs_account: str,
    xhs_user_id: str,
    post_id: str,
    xsec_token: str,
    title: str,
    tags: list[str],
    publish_time: Optional[str] = None,
) -> dict:
    """发布成功后写入一条新记录。"""
    entry = {
        "username": username,
        "trace_id": trace_id,
        "trace_path": trace_path,
        "result_video": result_video,
        "xhs_account": xhs_account,
        "xhs_user_id": xhs_user_id,
        "post_id": post_id,
        "xsec_token": xsec_token,
        "title": title,
        "tags": tags,
        "publish_time": publish_time or _now_str(),
        "stats": {
            "liked_count": 0,
            "collected_count": 0,
            "comment_count": 0,
            "share_count": 0,
            "comments": [],
        },
        "last_updated": None,
    }
    entries = _read_all(username)
    entries.append(entry)
    _write_all(username, entries)
    return entry


def update_post_stats(
    username: str,
    post_id: str,
    liked_count: int = 0,
    collected_count: int = 0,
    comment_count: int = 0,
    share_count: int = 0,
    comments: Optional[list[dict]] = None,
) -> bool:
    """全量刷新帖子指标与评论树。

    每次调用会：
    1. 将新指标写入 stats（最新快照）
    2. 同时追加一条带时间戳的快照到 stats_history（历史序列）

    comments 可直接传入 get_feed_detail 返回的 comments.list（原始格式），
    内部自动解析为树结构；也可传入已解析的树结构列表。
    """
    entries = _read_all(username)
    found = False
    changed = False
    now = _now_str()
    for entry in entries:
        if entry.get("post_id") != post_id:
            continue
        found = True
        parsed: list[dict] = []
        for c in (comments or []):
            parsed.append(_parse_comment(c) if "userInfo" in c else c)
        snapshot = {
            "liked_count": liked_count,
            "collected_count": collected_count,
            "comment_count": comment_count,
            "share_count": share_count,
            "comments": parsed,
        }
        # 与当前 stats 对比，数值无变化则跳过写入
        prev = entry.get("stats", {})
        metrics_changed = (
            prev.get("liked_count") != liked_count
            or prev.get("collected_count") != collected_count
            or prev.get("comment_count") != comment_count
            or prev.get("share_count") != share_count
        )
        if not metrics_changed:
            break  # 找到帖子但数据没变，不写入
        entry["stats"] = snapshot
        entry["last_updated"] = now
        # 只在有变化时追加历史快照
        if "stats_history" not in entry:
            entry["stats_history"] = []
        entry["stats_history"].append({"observed_at": now, **snapshot})
        changed = True
        break
    if changed:
        _write_all(username, entries)
    return found


def update_post_ids(username: str, trace_id: str, post_id: str, xsec_token: str) -> bool:
    """补充或更新某条记录的 post_id 和 xsec_token（发布时未能立即获取时使用）。"""
    entries = _read_all(username)
    updated = False
    for entry in entries:
        if entry.get("trace_id") == trace_id:
            entry["post_id"] = post_id
            entry["xsec_token"] = xsec_token
            updated = True
    if updated:
        _write_all(username, entries)
    return updated


def mark_deleted(username: str, post_id: str) -> bool:
    """将帖子标记为已删除。"""
    entries = _read_all(username)
    updated = False
    for entry in entries:
        if entry.get("post_id") == post_id:
            entry["status"] = "deleted"
            updated = True
    if updated:
        _write_all(username, entries)
    return updated


def read_publish_log(username: str) -> list[dict]:
    """读取全部发布记录。"""
    return _read_all(username)


def get_entry_by_trace(username: str, trace_id: str) -> Optional[dict]:
    """按 trace_id 查找记录。"""
    for entry in _read_all(username):
        if entry.get("trace_id") == trace_id:
            return entry
    return None


def get_entry_by_timestamp(username: str, timestamp: str) -> Optional[dict]:
    """按时间戳前缀匹配 publish_log 记录。

    ctx 文件名格式：ctx_<timestamp>_<hash>.json
    trace_id 格式： ep_<timestamp>_<hash>
    两者共享相同的 timestamp，用于跨文件关联。
    """
    for entry in _read_all(username):
        tid = entry.get("trace_id", "")
        # ep_1775802093_xxx → 取中间的 timestamp 部分
        parts = tid.split("_")
        if len(parts) >= 2 and parts[1] == timestamp:
            return entry
    return None
