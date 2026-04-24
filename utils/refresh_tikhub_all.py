#!/usr/bin/env python3
"""
refresh_tikhub_all.py — 通过 TikHub 刷新所有平台（XHS / 抖音 / Bilibili / 快手）帖子指标。

全自动流程，只需两个前提：
  1. sau login <platform>  — 登录获取 cookie（用于发布）
  2. TIKHUB_API_TOKEN       — TikHub API Token（用于数据采集）

无需手动配置任何用户 ID。首次运行时自动通过标题搜索发现各平台 user_id，
并缓存到 workspace/<username>/platform_config.json，后续直接用主页列表拉取。

每个平台的执行流程：
  1. 读取 platform_config.json 中缓存的 user_id
     → 若不存在：从 publish_log_v2 取已发布帖子标题，TikHub 搜索发现 user_id，写入缓存
  2. 用 user_id 拉取用户主页帖子列表，补全缺失的 post_id（标题匹配）
  3. 用 post_id 拉取指标（赞/评/播/收藏）+ 评论树
  4. 写入 publish_log_v2.json（仅数据变化时追加 stats_history）
  5. 全部平台完成后：回写 trace 文件（social-media-feedback 字段）

用法：
  python utils/refresh_tikhub_all.py --username <username>
  python utils/refresh_tikhub_all.py --username <username> --platform douyin bilibili
  python utils/refresh_tikhub_all.py --username <username> --backfill-only
  python utils/refresh_tikhub_all.py --username <username> --refresh-only

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

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.xhs_log_io_v2 import (
    get_entries_for_refresh,
    get_entries_pending_id,
    read_publish_log,
    update_platform_post_id,
    update_platform_stats,
)

# 自动加载项目根目录 .env
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_TIKHUB_TOKEN = os.environ.get("TIKHUB_API_TOKEN", "")
_DELAY = 1.0


def _find_sau_cookie_dir() -> Path:
    """自动查找 sau cookie 目录：优先读环境变量，其次从 sau_cli 包位置推断。"""
    # 1. 环境变量显式指定
    env_dir = os.environ.get("SAU_COOKIE_DIR", "")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir)
    # 2. 从 sau_cli 包安装位置推断
    try:
        import sau_cli
        pkg_dir = Path(sau_cli.__file__).resolve().parent
        # pip install -e . 时 __file__ 在项目根；pip install 时在 site-packages 内
        for candidate in [pkg_dir / "cookies", pkg_dir.parent / "cookies"]:
            if candidate.exists():
                return candidate
    except ImportError:
        pass
    # 3. 兜底：项目内 submodule 目录
    return _PROJECT_ROOT / "social-auto-upload" / "cookies"


def _load_sau_cookie(platform: str, account: str) -> str:
    """从 sau cookies 目录读取 Playwright cookie 并转成 header 字符串。"""
    cookie_dir = _find_sau_cookie_dir()
    f = cookie_dir / f"{platform}_{account}.json"
    if not f.exists():
        return ""
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if platform == "bilibili":
            cookies = data.get("cookie_info", {}).get("cookies", [])
        else:
            cookies = data.get("cookies", [])
        return "; ".join(f'{c["name"]}={c["value"]}' for c in cookies if c.get("name") and c.get("value"))
    except Exception:
        return ""


# ──────────────────────────────────────────────
# 客户端 & 配置
# ──────────────────────────────────────────────

def _get_client():
    try:
        from tikhub import TikHub
    except ImportError:
        print("[tikhub] 未安装 tikhub，请运行：pip install tikhub", file=sys.stderr)
        sys.exit(1)
    if not _TIKHUB_TOKEN:
        print("[tikhub] 缺少 TIKHUB_API_TOKEN 环境变量", file=sys.stderr)
        sys.exit(1)
    return TikHub(api_key=_TIKHUB_TOKEN)


def _config_path(username: str) -> Path:
    return _PROJECT_ROOT / "workspace" / username / "platform_config.json"


def _load_config(username: str) -> dict:
    p = _config_path(username)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_config(username: str, config: dict) -> None:
    p = _config_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_any_title(username: str, platform: str) -> str:
    """从 publish_log_v2 取该平台任意一条已发布帖子的标题，用于搜索发现 user_id。"""
    for entry in read_publish_log(username):
        plat = entry.get("platforms", {}).get(platform, {})
        title = plat.get("title", "")
        if title:
            return title
    return ""


# ──────────────────────────────────────────────
# 自动发现 user_id（首次运行时通过搜索获取）
# ──────────────────────────────────────────────

def _discover_douyin_user_id(client, title: str) -> str:
    try:
        resp = client.douyin_web.fetch_video_search_result(keyword=title, count=5)
        data = resp if isinstance(resp, dict) else {}
        items = data.get("data", {}).get("data", []) or data.get("data", [])
        for item in items:
            info = item.get("aweme_info") or item
            author = info.get("author", {})
            sec_uid = author.get("sec_uid", "")
            if sec_uid:
                nickname = author.get("nickname", "")
                print(f"[douyin][discover] 发现用户：{nickname}，sec_user_id={sec_uid}")
                return sec_uid
    except Exception as e:
        print(f"[douyin][discover] 搜索异常：{e}", file=sys.stderr)
    return ""


def _discover_bilibili_uid(client, title: str) -> str:
    try:
        resp = client.bilibili_web.fetch_general_search(
            keyword=title, order="pubdate", page=1, page_size=10)
        data = resp if isinstance(resp, dict) else {}
        results = (data.get("data", {}).get("result", [])
                   or data.get("result", []))
        for item in results:
            uid = str(item.get("mid", ""))
            if uid and uid != "0":
                author = item.get("author", "")
                print(f"[bilibili][discover] 发现用户：{author}，uid={uid}")
                return uid
    except Exception as e:
        print(f"[bilibili][discover] 搜索异常：{e}", file=sys.stderr)
    return ""


def _discover_kuaishou_user_id(client, title: str) -> str:
    try:
        resp = client.kuaishou_app.search_video_v2(keyword=title)
        data = resp if isinstance(resp, dict) else {}
        feeds = (data.get("data", {}).get("feeds", [])
                 or data.get("feeds", []))
        for feed in feeds:
            photo = feed.get("photo", {}) or {}
            author = feed.get("author", {}) or {}
            user_id = (photo.get("userId", "")
                       or author.get("id", "")
                       or author.get("userId", ""))
            if user_id:
                name = author.get("name", "")
                print(f"[kuaishou][discover] 发现用户：{name}，user_id={user_id}")
                return user_id
    except Exception as e:
        print(f"[kuaishou][discover] 搜索异常：{e}", file=sys.stderr)
    return ""


# config_key → (discover_fn, kwarg_name_in_config)
_DISCOVER_FN = {
    "douyin":   (_discover_douyin_user_id,  "sec_user_id"),
    "bilibili": (_discover_bilibili_uid,    "uid"),
    "kuaishou": (_discover_kuaishou_user_id, "user_id"),
    # TikTok / YouTube 直接从 platform_config.json 读取，无需自动发现
}


def _ensure_user_id(username: str, client, platform: str, config: dict) -> str:
    """返回 user_id；若缓存中没有则自动搜索发现并写入缓存。XHS 从 xhs_config.json 读取。"""
    if platform == "xiaohongshu":
        uid = config.get("xiaohongshu", {}).get("user_id", "")
        if not uid:
            xhs_cfg = _PROJECT_ROOT / "workspace" / username / "xhs_config.json"
            if xhs_cfg.exists():
                uid = json.loads(xhs_cfg.read_text(encoding="utf-8")).get("xhs_user_id", "")
        return uid

    if platform == "tiktok":
        return config.get("tiktok", {}).get("sec_uid", "")

    if platform == "youtube":
        return config.get("youtube", {}).get("channel_id", "")

    discover_fn, cfg_key = _DISCOVER_FN[platform]
    uid = config.get(platform, {}).get(cfg_key, "")
    if uid:
        return uid

    print(f"[{platform}] 未缓存 user_id，尝试通过搜索自动发现...")
    title = _get_any_title(username, platform)
    if not title:
        print(f"[{platform}] publish_log_v2 中无已发布帖子标题，无法搜索", file=sys.stderr)
        return ""

    uid = discover_fn(client, title)
    if uid:
        config.setdefault(platform, {})[cfg_key] = uid
        _save_config(username, config)
        print(f"[{platform}] user_id 已自动发现并缓存：{uid}")
    else:
        print(f"[{platform}] 自动发现失败，跳过该平台", file=sys.stderr)
    return uid


# ──────────────────────────────────────────────
# 小红书
# ──────────────────────────────────────────────

def _xhs_fetch_user_posts(client, user_id: str, username: str = "") -> list[dict]:
    notes, cursor = [], ""
    for _ in range(10):
        try:
            resp = client.xiaohongshu_web.get_user_notes_v2(user_id=user_id, lastCursor=cursor or None)
        except Exception as e:
            print(f"[xhs] get_user_notes_v2 异常：{e}", file=sys.stderr); break
        data = resp if isinstance(resp, dict) else {}
        items = data.get("data", {}).get("notes", []) or data.get("notes", [])
        if not items:
            break
        for item in items:
            notes.append({
                "post_id": item.get("noteId") or item.get("note_id", ""),
                "title": item.get("displayTitle") or item.get("title", ""),
            })
        cursor = data.get("data", {}).get("cursor") or data.get("cursor", "")
        if not cursor:
            break
        time.sleep(0.5)
    return notes


def _xhs_fetch_stats(client, post_id: str) -> Optional[dict]:
    try:
        resp = client.xiaohongshu_web.get_note_info_v4(note_id=post_id)
    except Exception as e:
        print(f"[xhs] get_note_info_v4 异常：{e}", file=sys.stderr); return None
    data = resp if isinstance(resp, dict) else {}
    # data.data 是列表，每项有 note_list
    items = data.get("data", {}).get("data", [])
    note_list = items[0].get("note_list", []) if items else []
    note = note_list[0] if note_list else {}
    if not note:
        return None
    return {
        "liked_count":     int(note.get("liked_count", 0) or 0),
        "collected_count": int(note.get("collected_count", 0) or 0),
        "comment_count":   int(note.get("comments_count", 0) or 0),
        "share_count":     int(note.get("shared_count", 0) or 0),
        "view_count":      int(note.get("view_count", 0) or 0),
    }


def _xhs_fetch_comments(client, post_id: str) -> list[dict]:
    comments, cursor = [], ""
    for _ in range(5):
        try:
            resp = client.xiaohongshu_web.get_note_comments(note_id=post_id, lastCursor=cursor or None)
        except Exception as e:
            print(f"[xhs] get_note_comments 异常：{e}", file=sys.stderr); break
        data = resp if isinstance(resp, dict) else {}
        inner = data.get("data", {}).get("data", data.get("data", {}))
        raw_list = inner.get("comments", []) if isinstance(inner, dict) else []
        if not raw_list:
            break
        for raw in raw_list:
            cid = raw.get("id", "")
            sub_list = []
            if raw.get("subCommentCount", 0):
                sub_cursor = ""
                for _ in range(3):
                    try:
                        sr = client.xiaohongshu_web.get_note_comment_replies(
                            note_id=post_id, comment_id=cid, cursor=sub_cursor)
                    except Exception:
                        break
                    sd = sr if isinstance(sr, dict) else {}
                    sd_inner = sd.get("data", {}).get("data", sd.get("data", {}))
                    for s in (sd_inner.get("comments", []) if isinstance(sd_inner, dict) else []):
                        sub_list.append({"comment_id": s.get("id", ""), "content": s.get("content", ""),
                                         "author": s.get("userInfo", {}).get("nickname", ""),
                                         "like_count": int(s.get("likeCount", 0) or 0), "sub_comments": []})
                    sub_cursor = sd_inner.get("cursor", "") if isinstance(sd_inner, dict) else ""
                    if not sub_cursor:
                        break
                    time.sleep(0.3)
            comments.append({"comment_id": cid, "content": raw.get("content", ""),
                              "author": raw.get("userInfo", {}).get("nickname", ""),
                              "like_count": int(raw.get("likeCount", 0) or 0), "sub_comments": sub_list})
        cursor = inner.get("cursor", "") if isinstance(inner, dict) else ""
        if not cursor:
            break
        time.sleep(0.5)
    return comments


# ──────────────────────────────────────────────
# 抖音
# ──────────────────────────────────────────────

def _douyin_fetch_user_posts(client, sec_user_id: str, username: str = "") -> list[dict]:
    posts, max_cursor = [], None
    cookie = _load_sau_cookie("douyin", f"{username}-douyin") if username else ""
    for _ in range(10):
        try:
            resp = client.douyin_web.fetch_user_post_videos(
                sec_user_id=sec_user_id, max_cursor=max_cursor, count=20,
                cookie=cookie or None)
        except Exception as e:
            print(f"[douyin] fetch_user_post_videos 异常：{e}", file=sys.stderr); break
        data = resp if isinstance(resp, dict) else {}
        items = data.get("data", {}).get("aweme_list", []) or data.get("aweme_list", [])
        if not items:
            break
        for item in items:
            title = item.get("desc", "") or item.get("share_info", {}).get("share_title", "")
            posts.append({"post_id": item.get("aweme_id", ""), "title": title})
        has_more = data.get("data", {}).get("has_more") or data.get("has_more", 0)
        if not has_more:
            break
        max_cursor = str(data.get("data", {}).get("max_cursor") or data.get("max_cursor", ""))
        if not max_cursor or max_cursor == "0":
            break
        time.sleep(0.5)
    return posts


def _douyin_fetch_stats(client, aweme_id: str) -> Optional[dict]:
    try:
        resp = client.douyin_web.fetch_one_video(aweme_id=aweme_id)
    except Exception as e:
        print(f"[douyin] fetch_one_video 异常：{e}", file=sys.stderr); return None
    data = resp if isinstance(resp, dict) else {}
    item = (data.get("data", {}).get("aweme_detail") or data.get("aweme_detail") or data.get("data", {}))
    s = item.get("statistics", {})
    return {
        "liked_count":     int(s.get("digg_count", 0) or 0),
        "collected_count": int(s.get("collect_count", 0) or 0),
        "comment_count":   int(s.get("comment_count", 0) or 0),
        "share_count":     int(s.get("share_count", 0) or 0),
        "view_count":      int(s.get("play_count", 0) or 0),
    }


def _douyin_fetch_comments(client, aweme_id: str) -> list[dict]:
    comments, cursor = [], 0
    for _ in range(5):
        try:
            resp = client.douyin_web.fetch_video_comments(aweme_id=aweme_id, cursor=cursor, count=20)
        except Exception as e:
            print(f"[douyin] fetch_video_comments 异常：{e}", file=sys.stderr); break
        data = resp if isinstance(resp, dict) else {}
        raw_list = data.get("data", {}).get("comments", []) or data.get("comments", [])
        if not raw_list:
            break
        for raw in raw_list:
            cid = raw.get("cid", "")
            sub_list = []
            if raw.get("reply_comment_total", 0):
                sub_cursor = 0
                for _ in range(3):
                    try:
                        sr = client.douyin_web.fetch_video_comment_replies(
                            item_id=aweme_id, comment_id=cid, cursor=sub_cursor, count=10)
                    except Exception:
                        break
                    sd = sr if isinstance(sr, dict) else {}
                    for s in sd.get("data", {}).get("comments", []) or sd.get("comments", []):
                        sub_list.append({"comment_id": s.get("cid", ""), "content": s.get("text", ""),
                                         "author": s.get("user", {}).get("nickname", ""),
                                         "like_count": int(s.get("digg_count", 0) or 0), "sub_comments": []})
                    if not (sd.get("data", {}).get("has_more") or sd.get("has_more")):
                        break
                    sub_cursor = sd.get("data", {}).get("cursor") or sd.get("cursor", 0)
                    time.sleep(0.3)
            comments.append({"comment_id": cid, "content": raw.get("text", ""),
                              "author": raw.get("user", {}).get("nickname", ""),
                              "like_count": int(raw.get("digg_count", 0) or 0), "sub_comments": sub_list})
        if not (data.get("data", {}).get("has_more") or data.get("has_more")):
            break
        cursor = data.get("data", {}).get("cursor") or data.get("cursor", 0)
        time.sleep(0.5)
    return comments


# ──────────────────────────────────────────────
# Bilibili
# ──────────────────────────────────────────────

def _bilibili_fetch_user_posts(client, uid: str, username: str = "") -> list[dict]:
    posts, pn = [], 1
    for _ in range(5):
        try:
            resp = client.bilibili_web.fetch_user_post_videos(uid=uid, pn=pn)
        except Exception as e:
            print(f"[bilibili] fetch_user_post_videos 异常：{e}", file=sys.stderr); break
        data = resp if isinstance(resp, dict) else {}
        items = (data.get("data", {}).get("data", {}).get("list", {}).get("vlist", [])
                 or data.get("data", {}).get("list", {}).get("vlist", [])
                 or data.get("list", {}).get("vlist", []))
        if not items:
            break
        for item in items:
            posts.append({
                "post_id": item.get("bvid", ""),
                "aid": str(item.get("aid", "")),
                "title": item.get("title", ""),
            })
        total = data.get("data", {}).get("page", {}).get("count", 0)
        if total <= pn * 30:
            break
        pn += 1
        time.sleep(0.5)
    return posts


def _bilibili_fetch_stats(client, bvid: str, aid: str = "") -> Optional[dict]:
    try:
        resp = client.bilibili_web.fetch_one_video(bv_id=bvid)
    except Exception as e:
        print(f"[bilibili] fetch_one_video 异常：{e}", file=sys.stderr); return None
    data = resp if isinstance(resp, dict) else {}
    outer = data.get("data", {})
    s = outer.get("data", {}).get("stat") or outer.get("stat") or {}
    return {
        "liked_count":     int(s.get("like", 0) or 0),
        "collected_count": int(s.get("favorite", 0) or 0),
        "comment_count":   int(s.get("reply", 0) or 0),
        "share_count":     int(s.get("share", 0) or 0),
        "view_count":      int(s.get("view", 0) or 0),
    }


def _bilibili_fetch_comments(client, bvid: str) -> list[dict]:
    comments = []
    try:
        resp = client.bilibili_web.fetch_video_comments(bv_id=bvid, pn=1)
        data = resp if isinstance(resp, dict) else {}
        for raw in (data.get("data", {}).get("replies", []) or data.get("replies", []))[:20]:
            sub_list = []
            for sub in (raw.get("replies") or [])[:5]:
                sub_list.append({"comment_id": str(sub.get("rpid", "")),
                                  "content": sub.get("content", {}).get("message", ""),
                                  "author": sub.get("member", {}).get("uname", ""),
                                  "like_count": int(sub.get("like", 0) or 0), "sub_comments": []})
            comments.append({"comment_id": str(raw.get("rpid", "")),
                              "content": raw.get("content", {}).get("message", ""),
                              "author": raw.get("member", {}).get("uname", ""),
                              "like_count": int(raw.get("like", 0) or 0), "sub_comments": sub_list})
    except Exception as e:
        print(f"[bilibili] fetch_video_comments 异常：{e}", file=sys.stderr)
    return comments


# ──────────────────────────────────────────────
# 快手
# ──────────────────────────────────────────────

def _kuaishou_fetch_user_posts(client, user_id: str, username: str = "") -> list[dict]:
    posts = []
    try:
        resp = client.kuaishou_app.fetch_user_hot_post(user_id=user_id)
    except Exception as e:
        print(f"[kuaishou] fetch_user_post 异常：{e}", file=sys.stderr)
        return posts
    data = resp if isinstance(resp, dict) else {}
    items = data.get("data", {}).get("feeds", []) or data.get("feeds", [])
    for item in items:
        photo_id = str(item.get("photo_id", "") or "")
        posts.append({
            "post_id": photo_id,
            "title": item.get("caption", ""),
            "_raw": item,
        })
    return posts


def _kuaishou_stats_from_item(item: dict) -> dict:
    raw = item.get("_raw", item)
    return {
        "liked_count":     int(raw.get("like_count", 0) or raw.get("likeCount", 0) or 0),
        "collected_count": int(raw.get("collect_count", 0) or 0),
        "comment_count":   int(raw.get("comment_count", 0) or raw.get("commentCount", 0) or 0),
        "share_count":     int(raw.get("share_count", 0) or raw.get("forwardCount", 0) or 0),
        "view_count":      int(raw.get("view_count", 0) or raw.get("viewCount", 0) or 0),
    }


def _kuaishou_fetch_stats_direct(client, photo_id: str) -> dict | None:
    try:
        resp = client.kuaishou_web.fetch_one_video_v2(photo_id=photo_id)
        photo = (resp if isinstance(resp, dict) else {}).get("data", {}).get("photo", {})
        if not photo:
            return None
        return {
            "liked_count":     int(photo.get("likeCount", 0) or 0),
            "collected_count": 0,
            "comment_count":   int(photo.get("commentCount", 0) or 0),
            "share_count":     int(photo.get("forwardCount", 0) or 0),
            "view_count":      int(photo.get("viewCount", 0) or 0),
        }
    except Exception as e:
        print(f"[kuaishou] fetch_one_video_v2 异常：{e}", file=sys.stderr)
        return None


def _kuaishou_fetch_comments(client, photo_id: str) -> list[dict]:
    comments, pcursor = [], ""
    for _ in range(3):
        try:
            resp = client.kuaishou_web.fetch_one_video_comment(
                photo_id=photo_id, pcursor=pcursor or None)
        except Exception as e:
            print(f"[kuaishou] fetch_one_video_comment 异常：{e}", file=sys.stderr); break
        data = resp if isinstance(resp, dict) else {}
        raw_list = (data.get("data", {}).get("visionVideoDetail", {})
                    .get("commentList", {}).get("rootComments", [])
                    or data.get("rootComments", []))
        if not raw_list:
            break
        for raw in raw_list:
            cid = raw.get("commentId", "")
            sub_list = []
            if raw.get("subCommentCount", 0):
                try:
                    sr = client.kuaishou_web.fetch_one_video_sub_comment(
                        photo_id=photo_id, root_comment_id=cid)
                    sd = sr if isinstance(sr, dict) else {}
                    for s in (sd.get("data", {}).get("subComments", []) or [])[:5]:
                        sub_list.append({"comment_id": s.get("commentId", ""),
                                         "content": s.get("content", ""),
                                         "author": s.get("authorName", ""),
                                         "like_count": int(s.get("likedCount", 0) or 0), "sub_comments": []})
                except Exception:
                    pass
            comments.append({"comment_id": cid, "content": raw.get("content", ""),
                              "author": raw.get("authorName", ""),
                              "like_count": int(raw.get("likedCount", 0) or 0), "sub_comments": sub_list})
        pcursor = (data.get("data", {}).get("visionVideoDetail", {})
                   .get("commentList", {}).get("pcursor") or data.get("pcursor", ""))
        if not pcursor or pcursor == "no_more":
            break
        time.sleep(0.3)
    return comments


# ──────────────────────────────────────────────
# TikTok
# ──────────────────────────────────────────────

def _tiktok_fetch_user_posts(client, sec_uid: str, username: str = "") -> list[dict]:
    posts = []
    try:
        resp = client.tiktok_web.fetch_user_post(secUid=sec_uid, count=20)
        data = resp if isinstance(resp, dict) else {}
        items = data.get("data", {}).get("itemList", []) or data.get("itemList", [])
        for item in items:
            posts.append({
                "post_id": item.get("id", ""),
                "title": item.get("desc", ""),
            })
    except Exception as e:
        print(f"[tiktok] fetch_user_post 异常：{e}", file=sys.stderr)
    return posts


def _tiktok_fetch_stats(client, post_id: str) -> Optional[dict]:
    try:
        resp = client.tiktok_web.fetch_post_detail(itemId=post_id)
        data = resp if isinstance(resp, dict) else {}
        item = data.get("data", {}).get("itemInfo", {}).get("itemStruct", {}) or data.get("itemStruct", {})
        s = item.get("stats", {})
        return {
            "liked_count":     int(s.get("diggCount", 0) or 0),
            "collected_count": int(s.get("collectCount", 0) or 0),
            "comment_count":   int(s.get("commentCount", 0) or 0),
            "share_count":     int(s.get("shareCount", 0) or 0),
            "view_count":      int(s.get("playCount", 0) or 0),
        }
    except Exception as e:
        print(f"[tiktok] fetch_post_detail 异常：{e}", file=sys.stderr)
        return None


def _tiktok_fetch_comments(client, post_id: str) -> list[dict]:
    comments = []
    try:
        resp = client.tiktok_web.fetch_post_comment(itemId=post_id, count=20)
        data = resp if isinstance(resp, dict) else {}
        for raw in data.get("data", {}).get("comments", []) or data.get("comments", []):
            comments.append({
                "comment_id": raw.get("id", ""),
                "content": raw.get("text", ""),
                "author": raw.get("user", {}).get("uniqueId", ""),
                "like_count": int(raw.get("diggCount", 0) or 0),
                "sub_comments": [],
            })
    except Exception as e:
        print(f"[tiktok] fetch_post_comment 异常：{e}", file=sys.stderr)
    return comments


# ──────────────────────────────────────────────
# YouTube
# ──────────────────────────────────────────────

def _youtube_fetch_user_posts(client, channel_id: str, username: str = "") -> list[dict]:
    posts = []
    try:
        resp = client.youtube_web.get_channel_videos(channel_id=channel_id)
        data = resp if isinstance(resp, dict) else {}
        items = data.get("data", {}).get("videos", []) or data.get("videos", [])
        for item in items:
            posts.append({
                "post_id": item.get("id", ""),
                "title": item.get("title", ""),
            })
    except Exception as e:
        print(f"[youtube] get_channel_videos 异常：{e}", file=sys.stderr)
    return posts


def _youtube_fetch_stats(client, video_id: str) -> Optional[dict]:
    try:
        resp = client.youtube_web.get_video_info(video_id=video_id)
        data = resp if isinstance(resp, dict) else {}
        item = data.get("data", {})
        return {
            "liked_count":     int(item.get("likeCount", 0) or 0),
            "collected_count": 0,
            "comment_count":   int(item.get("commentCount", 0) or 0),
            "share_count":     0,
            "view_count":      int(item.get("viewCount", 0) or 0),
        }
    except Exception as e:
        print(f"[youtube] get_video_info 异常：{e}", file=sys.stderr)
        return None


def _youtube_fetch_comments(_client, _video_id: str) -> list[dict]:
    # TikHub YouTube comments API 需要额外 token，暂返回空列表
    return []


# ──────────────────────────────────────────────
# 平台统一接口表
# ──────────────────────────────────────────────

_PLATFORM_HANDLERS: dict[str, dict] = {
    "xiaohongshu": {
        "fetch_user_posts": _xhs_fetch_user_posts,
        "fetch_stats":      _xhs_fetch_stats,
        "fetch_comments":   _xhs_fetch_comments,
        "kuaishou_list_stats": False,
    },
    "douyin": {
        "fetch_user_posts": _douyin_fetch_user_posts,
        "fetch_stats":      _douyin_fetch_stats,
        "fetch_comments":   _douyin_fetch_comments,
        "kuaishou_list_stats": False,
    },
    "bilibili": {
        "fetch_user_posts": _bilibili_fetch_user_posts,
        "fetch_stats":      _bilibili_fetch_stats,
        "fetch_comments":   _bilibili_fetch_comments,
        "kuaishou_list_stats": False,
    },
    "kuaishou": {
        "fetch_user_posts": _kuaishou_fetch_user_posts,
        "fetch_stats":      _kuaishou_fetch_stats_direct,
        "fetch_comments":   _kuaishou_fetch_comments,
        "kuaishou_list_stats": False,
    },
    "tiktok": {
        "fetch_user_posts": _tiktok_fetch_user_posts,
        "fetch_stats":      _tiktok_fetch_stats,
        "fetch_comments":   _tiktok_fetch_comments,
        "kuaishou_list_stats": False,
    },
    "youtube": {
        "fetch_user_posts": _youtube_fetch_user_posts,
        "fetch_stats":      _youtube_fetch_stats,
        "fetch_comments":   _youtube_fetch_comments,
        "kuaishou_list_stats": False,
    },
}


# ──────────────────────────────────────────────
# 核心逻辑
# ──────────────────────────────────────────────

def _title_match(target: str, candidate: str) -> bool:
    if target == candidate:
        return True
    t10 = target[:10]
    return bool(t10) and (t10 in candidate or candidate[:10] in target)


def backfill_missing_ids(username: str, client, platform: str, user_id: str) -> None:
    pending = get_entries_pending_id(username, platform)
    if not pending:
        return
    print(f"[{platform}][backfill] 缺失 post_id：{len(pending)} 条")

    handler = _PLATFORM_HANDLERS[platform]
    all_posts = handler["fetch_user_posts"](client, user_id, username)
    if not all_posts:
        print(f"[{platform}][backfill] 未能拉取用户帖子列表", file=sys.stderr)
        return

    post_map = {p["title"]: p for p in all_posts if p.get("title")}

    for entry in pending:
        title = entry.get("title", "")
        if not title:
            continue
        matched = post_map.get(title)
        if not matched:
            for t, p in post_map.items():
                if _title_match(title, t):
                    matched = p
                    break
        if not matched:
            print(f"[{platform}][backfill] 未找到「{title[:20]}」")
            continue
        post_id = matched["post_id"]
        ok = update_platform_post_id(username, entry["trace_id"], platform, post_id)
        print(f"[{platform}][backfill] {'✓' if ok else '✗'} {title[:20]} → {post_id}")


def refresh_platform_stats(username: str, client, platform: str, user_id: str, delay: float = _DELAY) -> None:
    active = get_entries_for_refresh(username, platform)
    if not active:
        print(f"[{platform}][refresh] 无待刷新帖子")
        return
    print(f"[{platform}][refresh] 待刷新：{len(active)} 条")

    handler = _PLATFORM_HANDLERS[platform]

    # 快手：提前拉用户主页列表，直接从列表项读指标
    ks_posts_map: dict[str, dict] = {}
    if handler["kuaishou_list_stats"]:
        raw_list = handler["fetch_user_posts"](client, user_id, username)
        ks_posts_map = {p["post_id"]: p for p in raw_list if p.get("post_id")}

    for item in active:
        trace_id = item["trace_id"]
        post_id = item["post_id"]
        title_short = item.get("title", "")[:20]

        if handler["kuaishou_list_stats"]:
            raw_item = ks_posts_map.get(post_id)
            if raw_item is None:
                print(f"[{platform}][refresh] 跳过 {post_id}（未在主页列表找到）")
                continue
            stats = _kuaishou_stats_from_item(raw_item)
        else:
            stats = handler["fetch_stats"](client, post_id)
            if stats is None:
                print(f"[{platform}][refresh] 跳过 {post_id}（拉取指标失败）")
                continue

        comments = handler["fetch_comments"](client, post_id)
        stats["comments"] = comments

        ok = update_platform_stats(username, trace_id, platform, stats)
        if ok:
            print(f"[{platform}][refresh] ✓ {title_short} | "
                  f"赞={stats['liked_count']} 评={stats['comment_count']} 播={stats['view_count']}")
        else:
            print(f"[{platform}][refresh] ✗ 未找到 trace_id={trace_id}")
        time.sleep(delay)


def run(
    username: str,
    platforms: list[str] | None = None,
    backfill_only: bool = False,
    refresh_only: bool = False,
    delay: float = _DELAY,
) -> None:
    config = _load_config(username)
    client = _get_client()
    target_platforms = platforms or list(_PLATFORM_HANDLERS.keys())

    for platform in target_platforms:
        if platform not in _PLATFORM_HANDLERS:
            print(f"[warn] 未知平台：{platform}，跳过")
            continue

        print(f"\n{'='*40}\n平台：{platform}\n{'='*40}")

        # 自动发现 user_id（首次时通过搜索）
        user_id = _ensure_user_id(username, client, platform, config)
        if not user_id:
            continue

        if not refresh_only:
            backfill_missing_ids(username, client, platform, user_id)
        if not backfill_only:
            refresh_platform_stats(username, client, platform, user_id, delay)

    # 全部平台完成后回写 trace
    if not backfill_only:
        try:
            from utils.backfill_social_feedback_v2 import backfill_all
            backfill_all(username)
        except Exception as e:
            print(f"[backfill_trace] 异常（不影响主流程）：{e}", file=sys.stderr)


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="TikHub 多平台指标刷新（全自动）")
    parser.add_argument("--username", required=True)
    parser.add_argument("--platform", nargs="*",
                        help="指定平台（不填则全部）：xiaohongshu douyin bilibili kuaishou tiktok youtube")
    parser.add_argument("--backfill-only", action="store_true", help="仅补全 post_id")
    parser.add_argument("--refresh-only",  action="store_true", help="仅刷新指标")
    parser.add_argument("--delay", type=float, default=_DELAY)
    args = parser.parse_args()

    run(
        username=args.username,
        platforms=args.platform or None,
        backfill_only=args.backfill_only,
        refresh_only=args.refresh_only,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
