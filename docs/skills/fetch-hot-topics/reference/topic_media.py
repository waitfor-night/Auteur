#!/usr/bin/env python3
"""
topic_media.py — 为热点话题下载参考视频（多来源）。

存储规则：
  workspace/<username>/hot_topics_media/<md5(title)[:8]>/ref_<source>.mp4
      source: bilibili | douyin | tiktok | youtube

每条话题可有多条参考视频，state 中以 media.ref_videos 列表存储。
已存在且有效（>10 KB）的文件跳过重复下载。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ── 路径计算 ──────────────────────────────────────────────────

def topic_media_dir(media_root: Path, title: str) -> Path:
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
    return media_root / h


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


def _cached(dest: Path) -> bool:
    return dest.exists() and dest.stat().st_size > 10_240


# ── 关键词提取（LLM，带 fallback） ───────────────────────────

def _fallback_keywords(title: str) -> tuple[str, str]:
    """纯字符串截断 fallback，返回 (中文关键词, 英文关键词)。"""
    stripped = re.sub(r'^(为什么|如何|怎么|哪些|什么是|怎样|是否|有没有|请问)', '', title)
    parts = [p.strip() for p in re.split(r'[，,。？！、\s；;]', stripped) if p.strip()]
    parts.sort(key=len, reverse=True)
    cn = (parts[0][:8] if parts else title[:8])
    return cn, cn  # 无翻译能力时英文与中文相同


def extract_search_keywords(title: str) -> tuple[str, str]:
    """
    用 LLM 从话题标题提取搜索关键词，返回 (国内关键词_中文, 海外关键词_英文)。
    失败时 fallback 到字符串截断。
    """
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        return _fallback_keywords(title)
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )
        resp = client.chat.completions.create(
            model="doubao-seed-2-0-pro-260215",
            messages=[{"role": "user", "content": (
                f"从以下话题标题提取视频搜索关键词：\n\n话题：{title}\n\n"
                "要求：\n"
                "1. cn：中文关键词，用于抖音搜索，抓住核心视觉概念，5-8字，去除问句引导词\n"
                "2. en：英文关键词，用于 TikTok/YouTube 搜索，翻译核心概念，3-6个词\n\n"
                '仅输出 JSON：{"cn": "...", "en": "..."}'
            )}],
            temperature=0.1,
            max_tokens=80,
        )
        content = resp.choices[0].message.content.strip()
        if "```" in content:
            content = content.split("```")[1].lstrip("json").strip()
        result = json.loads(content)
        cn = (result.get("cn") or "").strip() or _fallback_keywords(title)[0]
        en = (result.get("en") or "").strip() or cn
        return cn, en
    except Exception as e:
        print(f"  [media] 关键词提取失败（{e}），使用截断 fallback")
        return _fallback_keywords(title)


# ── 抖音 ──────────────────────────────────────────────────────

def _douyin_aweme_to_url(client, aweme_id: str) -> Optional[str]:
    try:
        resp = client.douyin_web.fetch_video_high_quality_play_url(aweme_id=aweme_id)
        inner = (resp if isinstance(resp, dict) else {}).get("data", {})
        return inner.get("original_video_url") if isinstance(inner, dict) else None
    except Exception as e:
        print(f"  [media] 抖音高清接口失败：{e}")
        return None


def _douyin_search_aweme_id(client, kw: str) -> Optional[str]:
    print(f"  [media] 抖音搜索：{kw!r}")
    try:
        resp = client.douyin_web.fetch_video_search_result_v2(keyword=kw)
        data = resp if isinstance(resp, dict) else {}
        if data.get("code") != 200:
            return None
        for entry in data.get("data", {}).get("business_data", []):
            aid = entry.get("data", {}).get("aweme_info", {}).get("aweme_id", "")
            if aid:
                return aid
    except Exception as e:
        print(f"  [media] 抖音搜索异常：{e}")
    return None


# ── B站 ───────────────────────────────────────────────────────

def _bilibili_video_url(client, bvid: str) -> Optional[str]:
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

        videos = (inner.get("dash") or {}).get("video", [])
        if videos:
            best = videos[0]
            return (best.get("baseUrl") or best.get("base_url")
                    or ((best.get("backupUrl") or best.get("backup_url") or [None])[0]))
        durl = inner.get("durl", [])
        return durl[0].get("url") if durl else None
    except Exception as e:
        print(f"  [media] B站接口失败：{e}")
        return None


# ── TikTok ────────────────────────────────────────────────────

def _tiktok_search_aweme_id(client, kw: str) -> Optional[str]:
    print(f"  [media] TikTok搜索：{kw!r}")
    try:
        resp = client.tiktok_app_v3.fetch_video_search_result(keyword=kw)
        data = (resp if isinstance(resp, dict) else {}).get("data", {})
        for item in data.get("search_item_list", []):
            aid = item.get("aweme_info", {}).get("aweme_id", "")
            if aid:
                return aid
    except Exception as e:
        print(f"  [media] TikTok搜索异常：{e}")
    return None


def _tiktok_aweme_to_url(client, aweme_id: str) -> Optional[str]:
    # 优先直接从搜索结果里的 play_addr 取，避免再调一次接口
    # 此函数作为 fallback，通过详情接口补充
    try:
        resp = client.tiktok_web.fetch_post_detail(aweme_id=aweme_id)
        data = (resp if isinstance(resp, dict) else {}).get("data", {})
        detail = data.get("aweme_detail", data)
        video = detail.get("video", {})
        for field in ("play_addr", "download_addr"):
            urls = video.get(field, {}).get("url_list", [])
            if urls:
                return urls[0]
        return None
    except Exception as e:
        print(f"  [media] TikTok详情接口失败：{e}")
        return None


def _tiktok_search_and_get_url(client, kw: str) -> Optional[str]:
    """搜索 TikTok，直接从搜索结果取播放 URL。"""
    print(f"  [media] TikTok搜索：{kw!r}")
    try:
        resp = client.tiktok_app_v3.fetch_video_search_result(keyword=kw)
        data = (resp if isinstance(resp, dict) else {}).get("data", {})
        for item in data.get("search_item_list", []):
            aweme = item.get("aweme_info", {})
            video = aweme.get("video", {})
            for field in ("play_addr", "download_addr"):
                urls = (video.get(field) or {}).get("url_list", [])
                if urls:
                    return urls[0]
    except Exception as e:
        print(f"  [media] TikTok搜索异常：{e}")
    return None


# ── YouTube ───────────────────────────────────────────────────

def _youtube_search_video_id(client, kw: str) -> Optional[str]:
    print(f"  [media] YouTube搜索：{kw!r}")
    try:
        resp = client.youtube_web.search_video(search_query=kw)
        data = (resp if isinstance(resp, dict) else {}).get("data", {})
        for item in data.get("videos", []):
            vid = item.get("video_id", "")
            if vid:
                return vid
    except Exception as e:
        print(f"  [media] YouTube搜索异常：{e}")
    return None


def _youtube_download(client, video_id: str, dest: Path) -> bool:
    # 先尝试 TikHub 直链
    for method_name in ("get_video_info_v2", "get_video_info"):
        try:
            method = getattr(client.youtube_web, method_name)
            resp = method(video_id=video_id)
            data = (resp if isinstance(resp, dict) else {}).get("data", {})
            url = data.get("url") or data.get("streaming_url")
            if not url:
                for fmt in data.get("formats", []):
                    if fmt.get("ext") == "mp4" and fmt.get("url"):
                        url = fmt["url"]
                        break
            if url:
                return _download(url, dest, referer="https://www.youtube.com/")
        except Exception as e:
            print(f"  [media] YouTube {method_name} 失败：{e}")

    # fallback: yt-dlp
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        print("  [media] YouTube: yt-dlp 未安装，跳过")
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [ytdlp,
             "-f", "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]",
             "--merge-output-format", "mp4",
             "-o", str(dest),
             "--no-playlist", "--quiet",
             f"https://www.youtube.com/watch?v={video_id}"],
            timeout=120, capture_output=True,
        )
        return r.returncode == 0 and _cached(dest)
    except Exception as e:
        print(f"  [media] yt-dlp 失败：{e}")
        return False


# ── 公开接口 ──────────────────────────────────────────────────

def fetch_topic_videos(client, topic: dict, media_root: Path) -> list[str]:
    """
    为单条话题从国内外多来源下载参考视频，返回本地绝对路径列表。

    来源顺序（前两者按 URL 类型走直链，后两者按关键词搜索）：
      B站直链 → 抖音直链/搜索 → TikTok搜索 → YouTube搜索
    各来源独立文件，互不影响；已存在有效文件直接复用。
    """
    title = topic["title"]
    url = topic.get("url", "")
    base_dir = topic_media_dir(media_root, title)
    base_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [media] 话题：{title[:40]}")

    # 一次 LLM 调用提取国内/海外关键词
    cn_kw, en_kw = extract_search_keywords(title)
    print(f"  [media] 关键词：抖音={cn_kw!r}  TikTok/YouTube={en_kw!r}")

    paths: list[str] = []

    # ── B站直链 ──
    bvid = (re.search(r"(BV[A-Za-z0-9]+)", url) or [None, None])[1]
    if bvid:
        dest = base_dir / "ref_bilibili.mp4"
        if _cached(dest):
            paths.append(str(dest))
        else:
            video_url = _bilibili_video_url(client, bvid)
            if video_url and _download(video_url, dest, referer="https://www.bilibili.com/"):
                paths.append(str(dest))

    # ── 抖音 ──
    aweme_id = (re.search(r"douyin\.com/video/(\d+)", url) or [None, None])[1]
    if not aweme_id:
        aweme_id = _douyin_search_aweme_id(client, cn_kw)
    if aweme_id:
        dest = base_dir / "ref_douyin.mp4"
        if _cached(dest):
            paths.append(str(dest))
        else:
            video_url = _douyin_aweme_to_url(client, aweme_id)
            if video_url and _download(video_url, dest):
                paths.append(str(dest))

    # ── TikTok + YouTube 并行搜索（使用英文关键词）──
    def _fetch_tiktok() -> Optional[str]:
        dest = base_dir / "ref_tiktok.mp4"
        if _cached(dest):
            return str(dest)
        tt_url = _tiktok_search_and_get_url(client, en_kw)
        if tt_url and _download(tt_url, dest, referer="https://www.tiktok.com/"):
            return str(dest)
        return None

    def _fetch_youtube() -> Optional[str]:
        dest = base_dir / "ref_youtube.mp4"
        if _cached(dest):
            return str(dest)
        yt_id = _youtube_search_video_id(client, en_kw)
        if not yt_id:
            return None
        if _youtube_download(client, yt_id, dest):
            return str(dest)
        return None

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(_fetch_tiktok): "tiktok", ex.submit(_fetch_youtube): "youtube"}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                p = fut.result()
                if p:
                    paths.append(p)
            except Exception as e:
                print(f"  [media] {src} 异常：{e}")

    return paths


def build_media_field(ref_video_paths: list[str]) -> Optional[dict]:
    if not ref_video_paths:
        return None
    return {
        "ref_videos": ref_video_paths,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
