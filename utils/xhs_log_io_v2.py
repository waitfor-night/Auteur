"""
xhs_log_io_v2.py — 多平台发布日志 v2 格式。

文件路径：workspace/<username>/publish_log_v2.json（JSON 数组，格式化缩进）

每条记录字段：
  trace_id        生成该视频的 trace episode id（ep_<timestamp>_<hash>）
  trace_path      trace 文件完整路径
  result_video    视频文件路径
  publish_time    首次发布时间（YYYY-MM-DD HH:MM:SS）
  platforms       dict，key = 平台名，value = 平台详情：
    account         发布账号标识
    post_id         帖子 id（发布后或补全后写入）
    xsec_token      小红书 xsec_token（仅小红书需要）
    title           帖子标题
    tags            标签列表
    refresh_status  active / deleted / paused
    last_updated    stats 最后更新时间
    stats           最新指标快照：
      liked_count / collected_count / comment_count / share_count / view_count
      comments        评论树列表
    stats_history   历史快照列表（仅在指标发生变化时追加）：
      每项 = stats 快照 + observed_at 字段
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _log_path(username: str) -> Path:
    p = _PROJECT_ROOT / "workspace" / username / "publish_log_v2.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_all(username: str) -> list[dict]:
    p = _log_path(username)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else []


def _write_all(username: str, entries: list[dict]) -> None:
    _log_path(username).write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _empty_stats() -> dict:
    return {
        "liked_count": 0,
        "collected_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "view_count": 0,
        "comments": [],
    }


def _has_stats_changed(old: dict, new: dict) -> bool:
    for key in ("liked_count", "collected_count", "comment_count", "share_count", "view_count"):
        if old.get(key, 0) != new.get(key, 0):
            return True
    old_ids = {c.get("comment_id") for c in old.get("comments", [])}
    new_ids = {c.get("comment_id") for c in new.get("comments", [])}
    return old_ids != new_ids


def _platform_entry(
    account: str,
    title: str,
    tags: list[str],
    post_id: str = "",
    xsec_token: str = "",
) -> dict:
    return {
        "account": account,
        "post_id": post_id,
        "xsec_token": xsec_token,
        "title": title,
        "tags": tags,
        "refresh_status": "active",
        "last_updated": None,
        "stats": _empty_stats(),
        "stats_history": [],
    }


# ──────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────

def write_publish_entry(
    username: str,
    trace_id: str,
    trace_path: str,
    result_video: str,
    platforms: dict,
    publish_time: Optional[str] = None,
) -> dict:
    """写入一条新发布记录。

    platforms 格式示例：
    {
      "xiaohongshu": {
        "account": "斋黎",
        "title": "...",
        "tags": [...],
        "post_id": "",        # 可选，后续补全
        "xsec_token": "",
      },
      "douyin": {
        "account": "zhiali-douyin",
        "title": "...",
        "tags": [...],
        "post_id": "",
      },
    }
    """
    entry: dict = {
        "trace_id": trace_id,
        "trace_path": trace_path,
        "result_video": result_video,
        "publish_time": publish_time or _now_str(),
        "published_platforms": list(platforms.keys()),
        "platforms": {},
    }
    for platform, info in platforms.items():
        entry["platforms"][platform] = _platform_entry(
            account=info.get("account", ""),
            title=info.get("title", ""),
            tags=info.get("tags", []),
            post_id=info.get("post_id", ""),
            xsec_token=info.get("xsec_token", ""),
        )
    entries = _read_all(username)
    entries.append(entry)
    _write_all(username, entries)
    return entry


def update_platform_post_id(
    username: str,
    trace_id: str,
    platform: str,
    post_id: str,
    xsec_token: str = "",
) -> bool:
    """补全某条记录某平台的 post_id（发布后未立即返回时使用）。"""
    entries = _read_all(username)
    updated = False
    for entry in entries:
        if entry.get("trace_id") != trace_id:
            continue
        plat = entry.get("platforms", {}).get(platform)
        if plat is None:
            break
        plat["post_id"] = post_id
        if xsec_token:
            plat["xsec_token"] = xsec_token
        updated = True
        break
    if updated:
        _write_all(username, entries)
    return updated


def update_platform_stats(
    username: str,
    trace_id: str,
    platform: str,
    new_stats: dict,
) -> bool:
    """刷新某条记录某平台的指标快照。

    new_stats 格式（与 stats 字段一致）：
    {
      "liked_count": int,
      "collected_count": int,
      "comment_count": int,
      "share_count": int,
      "view_count": int,
      "comments": [...],   # 已解析的评论树
    }

    只有数据发生变化时才写入 stats 和 stats_history。
    返回 True 表示找到记录（不论是否有变化）。
    """
    entries = _read_all(username)
    found = False
    changed = False
    now = _now_str()
    for entry in entries:
        if entry.get("trace_id") != trace_id:
            continue
        plat = entry.get("platforms", {}).get(platform)
        if plat is None:
            break
        found = True
        old_stats = plat.get("stats", _empty_stats())
        if not _has_stats_changed(old_stats, new_stats):
            break
        plat["stats"] = new_stats
        plat["last_updated"] = now
        if "stats_history" not in plat:
            plat["stats_history"] = []
        plat["stats_history"].append({"observed_at": now, **new_stats})
        changed = True
        break
    if changed:
        _write_all(username, entries)
    return found


def set_platform_refresh_status(
    username: str,
    trace_id: str,
    platform: str,
    status: str,
) -> bool:
    """设置平台刷新状态：active / deleted / paused。"""
    entries = _read_all(username)
    updated = False
    for entry in entries:
        if entry.get("trace_id") != trace_id:
            continue
        plat = entry.get("platforms", {}).get(platform)
        if plat is None:
            break
        plat["refresh_status"] = status
        updated = True
        break
    if updated:
        _write_all(username, entries)
    return updated


def get_entries_for_refresh(username: str, platform: str) -> list[dict]:
    """返回指定平台所有 refresh_status=active 且 post_id 非空的条目。

    每项格式：{"trace_id": ..., "post_id": ..., "xsec_token": ...}
    """
    result = []
    for entry in _read_all(username):
        plat = entry.get("platforms", {}).get(platform)
        if not plat:
            continue
        if plat.get("refresh_status") != "active":
            continue
        post_id = plat.get("post_id", "")
        if not post_id:
            continue
        result.append({
            "trace_id": entry["trace_id"],
            "post_id": post_id,
            "xsec_token": plat.get("xsec_token", ""),
            "title": plat.get("title", ""),
        })
    return result


def get_entries_pending_id(username: str, platform: str) -> list[dict]:
    """返回指定平台 post_id 为空的 active 条目（需要补全 id）。

    每项格式：{"trace_id": ..., "title": ..., "publish_time": ...}
    """
    result = []
    for entry in _read_all(username):
        plat = entry.get("platforms", {}).get(platform)
        if not plat:
            continue
        if plat.get("refresh_status") != "active":
            continue
        if plat.get("post_id"):
            continue
        result.append({
            "trace_id": entry["trace_id"],
            "title": plat.get("title", ""),
            "publish_time": entry.get("publish_time", ""),
        })
    return result


def read_publish_log(username: str) -> list[dict]:
    return _read_all(username)


def get_entry_by_trace(username: str, trace_id: str) -> Optional[dict]:
    for entry in _read_all(username):
        if entry.get("trace_id") == trace_id:
            return entry
    return None
