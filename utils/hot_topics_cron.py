#!/usr/bin/env python3
"""
定时刷新热点话题的 cron 脚本，每两天由系统 crontab 触发一次。

用法：
    python utils/hot_topics_cron.py --username <username>
    python utils/hot_topics_cron.py --username <username> --top 10
    python utils/hot_topics_cron.py --username <username> --force   # 强制刷新，忽略时间检查

状态文件：workspace/<username>/hot_topics_state.json
日志文件：workspace/<username>/hot_topics_cron.log

status 取值：
  unused      — 尚未用于生成视频
  in_progress — 正在生成（防止并发重复消费）
  done        — 已生成并发布完毕
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.fetch_topics import fetch_rss, parse_items, truncate_items

FETCH_INTERVAL_DAYS = 2
DEFAULT_TOP = 10
SCORE_THRESHOLD = 5      # 低于此分的话题直接丢弃
SCORE_MODEL = "doubao-seed-1-8-251228"  # 用轻量模型打分，省 token


def _state_path(username: str) -> Path:
    return PROJECT_ROOT / "workspace" / username / "hot_topics_state.json"


def _log_path(username: str) -> Path:
    return PROJECT_ROOT / "workspace" / username / "hot_topics_cron.log"


def setup_logger(username: str) -> logging.Logger:
    log_path = _log_path(username)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("hot-topics-cron")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 写入日志文件
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # 同时输出到终端
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


def load_state(username: str) -> dict:
    p = _state_path(username)
    if not p.exists():
        return {"last_fetch_time": None, "topics": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(username: str, state: dict) -> None:
    p = _state_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def should_fetch(state: dict, force: bool) -> bool:
    if force:
        return True
    last = state.get("last_fetch_time")
    if not last:
        return True
    last_dt = datetime.fromisoformat(last)
    return datetime.now() - last_dt >= timedelta(days=FETCH_INTERVAL_DAYS)


def fetch_topics(top: int) -> list[dict]:
    from utils.fetch_topics import DEFAULT_CODE
    xml_text = fetch_rss(DEFAULT_CODE)
    platforms = parse_items(xml_text)
    platforms = truncate_items(platforms, top)

    topics = []
    for platform, items in platforms.items():
        for item in items:
            topics.append({
                "platform": platform,
                "rank": item["rank"],
                "title": item["title"],
                "url": item["url"],
                "status": "unused",
                "use_count": 0,  # 生命周期内累计使用次数
            })
    return topics


def score_and_filter_topics(topics: list[dict], logger: logging.Logger) -> list[dict]:
    """
    用 LLM 给每条话题打分（1-10），评估是否适合制作小红书短视频。
    低于 SCORE_THRESHOLD 的话题直接丢弃，剩余按分数从高到低排列。

    打分标准：
    - 高分（7-10）：生活方式、娱乐趣味、美食旅行、美妆穿搭、萌宠、科技好物、健身、居家 / 治愈系、
                   情感故事、人文趣事、文化潮流等——视觉表达性强、受众广
    - 低分（1-4）：纯时政、军事冲突、刑事案件、股市行情、政府政策公告、体育比分、
                   自然灾害（无生活角度）——难以做成吸引人的视频内容
    """
    if not topics:
        return topics

    # 读取 API key（优先环境变量，其次 .env 文件）
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("ARK_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not api_key:
        logger.warning("未找到 ARK_API_KEY，跳过话题打分，保留全部话题")
        return topics

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai 包未安装，跳过话题打分")
        return topics

    client = OpenAI(
        api_key=api_key,
        base_url="https://ark.cn-beijing.volces.com/api/v3",
    )

    # 构建批量打分 prompt
    topic_list_text = "\n".join(
        f"{i+1}. [{t['platform']}] {t['title']}"
        for i, t in enumerate(topics)
    )
    prompt = f"""你是小红书内容运营专家。请评估以下热点话题是否适合制作成小红书短视频，并给每条话题打分（1-10分）。

评分标准：
- 8-10分：视觉表达强、受众广的生活类话题（美食/旅行/美妆/萌宠/科技好物/娱乐趣事/情感故事/文化潮流）
- 5-7分：有一定可操作性但受众相对有限的话题
- 1-4分：不适合小红书的话题（时政/军事/刑事案件/股市行情/政府公告/体育比分等）

话题列表：
{topic_list_text}

请以 JSON 数组格式返回，每项包含 index（1起）和 score（1-10整数），不要有其他内容：
[{{"index": 1, "score": 8}}, {{"index": 2, "score": 3}}, ...]"""

    logger.info(f"开始 LLM 打分，共 {len(topics)} 条话题...")
    try:
        resp = client.chat.completions.create(
            model=SCORE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2048,
        )
        raw = resp.choices[0].message.content.strip()
        # 提取 JSON 数组（兼容 markdown 代码块）
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        # 截断修复：若 JSON 不完整，保留已解析部分
        try:
            scores_list: list[dict] = json.loads(raw)
        except json.JSONDecodeError:
            # 找到最后一个完整对象（以 }` 结尾），截断后补全数组
            last_brace = raw.rfind("}")
            if last_brace != -1:
                raw = raw[: last_brace + 1] + "]"
                scores_list = json.loads(raw)
            else:
                raise
        score_map = {item["index"]: item["score"] for item in scores_list}
    except Exception as e:
        logger.warning(f"LLM 打分失败（{e}），跳过打分，保留全部话题")
        return topics

    # 写入分数
    for i, t in enumerate(topics):
        t["xhs_score"] = score_map.get(i + 1, 5)

    before = len(topics)
    filtered = [t for t in topics if t.get("xhs_score", 5) >= SCORE_THRESHOLD]
    filtered.sort(key=lambda t: t.get("xhs_score", 5), reverse=True)
    after = len(filtered)

    logger.info(f"打分完成 | 保留 {after}/{before} 条 | 过滤掉 {before - after} 条（分数 < {SCORE_THRESHOLD}）")
    if filtered:
        logger.info("Top 话题：" + " | ".join(
            f"[{t['xhs_score']}分]{t['title'][:15]}" for t in filtered[:5]
        ))

    return filtered


def merge_topics(old_topics: list[dict], new_topics: list[dict]) -> list[dict]:
    """
    合并新旧话题：
    - 保留旧列表中 unused / in_progress 的记录（未处理完的不丢失）
    - done 的旧话题直接丢弃（已完成，无需保留）
    - 新话题若标题与已有话题重复则跳过，避免重复制作
    """
    existing_titles = {t["title"] for t in old_topics}
    preserved = [t for t in old_topics if t["status"] in ("unused", "in_progress")]
    fresh = [t for t in new_topics if t["title"] not in existing_titles]
    return preserved + fresh


def main() -> None:
    parser = argparse.ArgumentParser(description="定时刷新热点话题")
    parser.add_argument("--username", required=True)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP,
                        help=f"每平台保留前 N 条，默认 {DEFAULT_TOP}")
    parser.add_argument("--force", action="store_true",
                        help="忽略时间检查，强制刷新")
    parser.add_argument("--no-score", action="store_true",
                        help="跳过 LLM 打分过滤（快速测试用）")
    args = parser.parse_args()

    log = setup_logger(args.username)
    state = load_state(args.username)

    if not should_fetch(state, args.force):
        last = state["last_fetch_time"]
        unused = sum(1 for t in state["topics"] if t["status"] == "unused")
        log.info(f"距上次拉取（{last}）未满 {FETCH_INTERVAL_DAYS} 天，跳过。当前 unused 话题数：{unused}")
        return

    log.info(f"开始拉取热点（每平台前 {args.top} 条）...")
    try:
        new_topics = fetch_topics(args.top)
    except Exception as e:
        log.error(f"拉取失败：{e}")
        sys.exit(1)

    # LLM 打分过滤（默认开启，--no-score 跳过）
    if not args.no_score:
        new_topics = score_and_filter_topics(new_topics, log)
    else:
        log.info("已跳过 LLM 打分（--no-score）")

    # 每次覆盖：直接用新话题替换，不保留旧记录
    merged = new_topics

    # 记录本次拉取详情
    log.info(f"本次拉取原始数据：{json.dumps(new_topics, ensure_ascii=False)}")
    log.info(f"本次拉取 {len(merged)} 条话题：")
    for t in merged:
        log.info(f"  [{t['platform']}] #{t['rank']} {t['title']}")

    new_added = len(merged)
    unused = len(merged)

    now = datetime.now().isoformat(timespec="seconds")
    state["last_fetch_time"] = now
    state["topics"] = merged
    save_state(args.username, state)

    log.info(f"拉取完成 | 新增话题：{new_added} 条 | 总话题：{len(merged)} 条 | unused：{unused} 条")
    log.info(f"状态文件：{_state_path(args.username)}")
    log.info(f"日志文件：{_log_path(args.username)}")


if __name__ == "__main__":
    main()
