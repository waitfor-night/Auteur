#!/usr/bin/env python3
"""
topic_media.py — 为热点话题下载参考视频。

存储规则：
  workspace/<username>/hot_topics_media/<md5(title)[:8]>/ref_video.mp4

路径全部为 ASCII（hash 命名），无中文。
每条话题只存一条参考视频。
"""
from __future__ import annotations

import hashlib
import re
import ssl
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ── 路径计算 ──────────────────────────────────────────────────

def topic_media_dir(media_root: Path, title: str) -> Path:
    """返回该话题媒体目录（hash 命名，无中文）。"""
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
    return media_root / h


def topic_video_path(media_root: Path, title: str) -> Path:
    return topic_media_dir(media_root, title) / "ref_video.mp4"


# ── 下载工具 ──────────────────────────────────────────────────

def _download(url: str, dest: Path, referer: str = "https://www.douyin.com/",
              retries: int = 3) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
    }
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90, context=_SSL_CTX) as resp, \
                    open(dest, "wb") as f:
                while chunk := resp.read(1024 * 256):
                    f.write(chunk)
            size_mb = dest.stat().st_size / 1024 / 1024
            print(f"  [media] ✓ {dest.name}  {size_mb:.1f} MB")
            return True
        except Exception as e:
            print(f"  [media] attempt {attempt}/{retries} 失败：{e}")
            if dest.exists():
                dest.unlink()
            if attempt < retries:
                time.sleep(2 * attempt)
    return False


# ── 抖音 ──────────────────────────────────────────────────────

def _candidate_keywords(title: str) -> list[str]:
    """从话题标题生成多个候选搜索词，跳过问句引导词，依次收窄。"""
    stripped = re.sub(r'^(为什么|如何|怎么|哪些|什么是|怎样|是否|有没有|请问)', '', title)
    parts = [p.strip() for p in re.split(r'[，,。？！、\s；;]', stripped) if p.strip()]
    parts.sort(key=len, reverse=True)
    candidates: list[str] = []
    for p in parts[:2]:
        for length in (8, 6, 4):
            kw = p[:length]
            if kw and kw not in candidates:
                candidates.append(kw)
    fallback = title[:8]
    if fallback not in candidates:
        candidates.append(fallback)
    return candidates


def _douyin_aweme_to_url(client, aweme_id: str) -> Optional[str]:
    """返回抖音视频高清下载 URL，失败返回 None。"""
    try:
        resp = client.douyin_web.fetch_video_high_quality_play_url(aweme_id=aweme_id)
        inner = (resp if isinstance(resp, dict) else {}).get("data", {})
        return inner.get("original_video_url") if isinstance(inner, dict) else None
    except Exception as e:
        print(f"  [media] 抖音高清接口失败：{e}")
        return None


def _douyin_search_aweme_id(client, title: str) -> Optional[str]:
    """关键词搜索抖音，返回第一条视频 aweme_id，失败返回 None。"""
    for kw in _candidate_keywords(title):
        print(f"  [media] 抖音搜索：{kw!r}")
        try:
            resp = client.douyin_web.fetch_video_search_result_v2(keyword=kw)
            data = resp if isinstance(resp, dict) else {}
            if data.get("code") != 200:
                time.sleep(0.5)
                continue
            for entry in data.get("data", {}).get("business_data", []):
                aid = entry.get("data", {}).get("aweme_info", {}).get("aweme_id", "")
                if aid:
                    return aid
        except Exception as e:
            print(f"  [media] 搜索异常：{e}")
            time.sleep(0.5)
    return None


# ── B站 ───────────────────────────────────────────────────────

def _bilibili_video_url(client, bvid: str) -> Optional[str]:
    """返回 B站视频最高质量 mp4 URL，失败返回 None。"""
    try:
        info = client.bilibili_web.fetch_one_video(bv_id=bvid)
        outer = (info if isinstance(info, dict) else {}).get("data", {})
        video_data = outer.get("data", outer) if isinstance(outer, dict) else {}
        cid = str(video_data.get("cid", "") or "")
        if not cid:
            pages = video_data.get("pages", [])
            cid = str(pages[0].get("cid", "")) if pages else ""
        if not cid:
            return None

        play = client.bilibili_web.fetch_video_playurl(bv_id=bvid, cid=cid)
        inner = (play if isinstance(play, dict) else {}).get("data", {})
        inner = inner.get("data", inner) if isinstance(inner, dict) else {}

        # DASH 格式
        videos = (inner.get("dash") or {}).get("video", [])
        if videos:
            best = videos[0]
            return (best.get("baseUrl") or best.get("base_url")
                    or ((best.get("backupUrl") or best.get("backup_url") or [None])[0]))
        # 传统 durl
        durl = inner.get("durl", [])
        return durl[0].get("url") if durl else None
    except Exception as e:
        print(f"  [media] B站接口失败：{e}")
        return None


# ── 公开接口 ──────────────────────────────────────────────────

def fetch_topic_video(client, topic: dict, media_root: Path) -> Optional[str]:
    """
    为单条话题下载参考视频，返回本地绝对路径字符串；失败返回 None。

    已有有效文件时直接返回路径，跳过重复下载。
    """
    title = topic["title"]
    url = topic.get("url", "")
    dest = topic_video_path(media_root, title)

    # 已下载且文件有效（> 10 KB）
    if dest.exists() and dest.stat().st_size > 10_240:
        print(f"  [media] 已存在，跳过：{dest}")
        return str(dest)

    print(f"  [media] 话题：{title[:40]}")

    # B站直链
    bvid = (re.search(r"(BV[A-Za-z0-9]+)", url) or [None, None])[1]
    if bvid:
        video_url = _bilibili_video_url(client, bvid)
        if video_url and _download(video_url, dest, referer="https://www.bilibili.com/"):
            return str(dest)
        return None

    # 抖音直链
    aweme_id = (re.search(r"douyin\.com/video/(\d+)", url) or [None, None])[1]
    if not aweme_id:
        aweme_id = _douyin_search_aweme_id(client, title)
    if aweme_id:
        video_url = _douyin_aweme_to_url(client, aweme_id)
        if video_url and _download(video_url, dest):
            return str(dest)

    return None


def build_media_field(ref_video_path: Optional[str]) -> Optional[dict]:
    """构造写入 state 的 media 字段；无路径时返回 None。"""
    if not ref_video_path:
        return None
    return {
        "ref_video": ref_video_path,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
