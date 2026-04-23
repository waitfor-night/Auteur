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

from utils.fetch_topics import fetch_all_hot, truncate_items

_ENV_FILE = PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

FETCH_INTERVAL_DAYS = 2
DEFAULT_TOP = 10
SCORE_THRESHOLD = 5      # 低于此分的话题直接丢弃
MAX_DAILY_TOPICS = 3     # 每次 cron 最终写入队列的话题上限
SCORE_MODEL = "doubao-seed-2-0-pro-260215"


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
    platforms = fetch_all_hot(top=top)
    topics = []
    for platform, items in platforms.items():
        for item in items:
            topics.append({
                "platform": platform,
                "rank": item["rank"],
                "title": item["title"],
                "url": item["url"],
                "status": "unused",
                "use_count": 0,
            })
    return topics


def _get_ark_client():
    """读取 ARK_API_KEY，返回 OpenAI-compatible client；失败返回 None。"""
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
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key, base_url="https://ark.cn-beijing.volces.com/api/v3")
    except ImportError:
        return None


def _parse_json_array(raw: str) -> list[dict]:
    """从 LLM 输出中提取 JSON 数组，兼容 markdown 代码块和截断。"""
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        last_brace = raw.rfind("}")
        if last_brace != -1:
            return json.loads(raw[: last_brace + 1] + "]")
        raise


def score_and_filter_topics(topics: list[dict], logger: logging.Logger) -> list[dict]:
    """
    第一轮：内容安全 + 传播价值打分（1-10），过滤政治/引战内容，保留有视频制作价值的话题。

    打分标准（面向抖音/B站/快手/小红书四平台综合）：
    - 高分（7-10）：视觉表达强、受众广、正向传播——生活方式/娱乐趣事/美食旅行/萌宠/
                   科技好物/情感故事/文化潮流/有争议但适合提问式视频的社会现象
    - 中分（5-6）：有制作空间但受众有限，或需要特殊处理
    - 低分（1-4，直接丢弃）：
        * 政治敏感：时政/军事冲突/政府政策/外交事件
        * 引战内容：地域黑/性别对立/族群矛盾/平台互撕
        * 无视觉价值：股市行情/体育比分/法庭判决/灾害伤亡数字
    """
    if not topics:
        return topics

    client = _get_ark_client()
    if not client:
        logger.warning("未找到 ARK_API_KEY 或 openai 未安装，跳过打分，保留全部话题")
        return topics

    topic_list_text = "\n".join(
        f"{i+1}. [{t['platform']}] {t['title']}"
        for i, t in enumerate(topics)
    )
    prompt = f"""你是短视频内容审核专家，负责为抖音、B站、快手、小红书四个平台筛选热点话题。

请给以下每条话题打分（1-10分），综合评估其**内容安全性**和**视频制作价值**。

【打分规则】
高分（7-10）——适合制作、传播价值高：
  · 生活方式、美食旅行、美妆穿搭、萌宠、科技好物、居家治愈
  · 娱乐趣事、情感故事、人文趣事、文化潮流
  · 有争议但无对立性的社会现象（适合做提问式/观点讨论视频）

中分（5-6）——可以制作但有限制：
  · 受众较窄，或需要规避部分细节
  · 争议较大但仍在中性范围内

低分（1-4）——直接过滤，不制作：
  · 政治敏感：时政/执政/军事冲突/外交事件/政府政策公告
  · 引战内容：地域攻击/性别对立/族群矛盾/平台阵营互撕
  · 无视觉价值：股市数字/体育比分/法庭判决/灾害死亡统计

话题列表：
{topic_list_text}

请以 JSON 数组返回，每项包含 index（1起）和 score（整数），不要有其他内容：
[{{"index": 1, "score": 8}}, {{"index": 2, "score": 3}}, ...]"""

    logger.info(f"开始 LLM 打分，共 {len(topics)} 条话题...")
    try:
        resp = client.chat.completions.create(
            model=SCORE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2048,
        )
        scores_list = _parse_json_array(resp.choices[0].message.content.strip())
        score_map = {item["index"]: item["score"] for item in scores_list}
    except Exception as e:
        logger.warning(f"LLM 打分失败（{e}），跳过打分，保留全部话题")
        return topics

    for i, t in enumerate(topics):
        t["content_score"] = score_map.get(i + 1, 5)

    before = len(topics)
    filtered = [t for t in topics if t.get("content_score", 5) >= SCORE_THRESHOLD]
    filtered.sort(key=lambda t: t.get("content_score", 5), reverse=True)
    after = len(filtered)

    logger.info(f"打分完成 | 保留 {after}/{before} 条 | 过滤 {before - after} 条（分 < {SCORE_THRESHOLD}）")
    if filtered:
        logger.info("Top 话题：" + " | ".join(
            f"[{t['content_score']}分]{t['title'][:15]}" for t in filtered[:5]
        ))
    return filtered


def seedance_filter_topics(topics: list[dict], logger: logging.Logger) -> list[dict]:
    """
    第二轮：Seedance 生成可行性评估。
    对每条话题判断：
      - seedance_viable: 能否生成有效视频（排除真人肖像/纯文字/负面内容）
      - video_style: showcase（展示型）| question（提问讨论型）| narrative（叙事型）
      - preferred_ratio: "9:16"（竖屏，适合抖/快/XHS）| "16:9"（横屏，适合 B站）
      - video_concept: 一句话视觉概念，供 Planner 参考
    不可行的话题直接丢弃。
    """
    if not topics:
        return topics

    client = _get_ark_client()
    if not client:
        logger.warning("跳过 Seedance 可行性过滤")
        for t in topics:
            t.setdefault("seedance_viable", True)
            t.setdefault("video_style", "showcase")
            t.setdefault("preferred_ratio", "9:16")
            t.setdefault("video_concept", t["title"])
        return topics

    topic_list_text = "\n".join(
        f"{i+1}. [{t['platform']}] {t['title']}"
        for i, t in enumerate(topics)
    )
    prompt = f"""你是 AI 视频生成专家，负责评估话题能否用文生视频模型（Seedance）制作成短视频。

对以下每条话题，输出：
  viable: true/false
    · false 的情形：依赖真实名人面孔/需要实时新闻画面/纯数据图表/色情暴力/负面灾难
    · true：场景可以被 AI 视觉化表达
  style:
    · "showcase"  展示型——美景/好物/生活场景，直接呈现画面
    · "question"  提问型——有争议或反差的社会现象，用悬念/疑问句引出讨论
    · "narrative" 叙事型——有情节/情感弧线，通过故事推进
  ratio:
    · "9:16"  适合竖屏（生活/情感/萌宠等移动端场景）
    · "16:9"  适合横屏（自然风景/知识科普/叙事类）
  concept: 一句话视觉描述（15字以内，供 AI 生成 prompt 参考，不含人名）

话题列表：
{topic_list_text}

以 JSON 数组返回，不要有其他内容：
[{{"index":1,"viable":true,"style":"showcase","ratio":"9:16","concept":"色彩斑斓的自然景观与城市街道交替出现"}}, ...]"""

    logger.info(f"开始 Seedance 可行性评估，共 {len(topics)} 条...")
    try:
        resp = client.chat.completions.create(
            model=SCORE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2048,
        )
        result_list = _parse_json_array(resp.choices[0].message.content.strip())
        result_map = {item["index"]: item for item in result_list}
    except Exception as e:
        logger.warning(f"Seedance 评估失败（{e}），跳过，保留全部话题")
        for t in topics:
            t.setdefault("seedance_viable", True)
            t.setdefault("video_style", "showcase")
            t.setdefault("preferred_ratio", "9:16")
            t.setdefault("video_concept", t["title"])
        return topics

    viable = []
    dropped = []
    for i, t in enumerate(topics):
        r = result_map.get(i + 1, {})
        t["seedance_viable"] = bool(r.get("viable", True))
        t["video_style"] = r.get("style", "showcase")
        t["preferred_ratio"] = r.get("ratio", "9:16")
        t["video_concept"] = r.get("concept", t["title"])
        if t["seedance_viable"]:
            viable.append(t)
        else:
            dropped.append(t["title"][:20])

    logger.info(f"Seedance 评估完成 | 可行 {len(viable)} 条 | 不可行 {len(dropped)} 条")
    if dropped:
        logger.info("不可行话题：" + " | ".join(dropped))
    if viable:
        logger.info("可行话题：" + " | ".join(
            f"[{t['video_style']}/{t['preferred_ratio']}]{t['title'][:12]}" for t in viable
        ))
    return viable


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


def _download_media(topics: list[dict], username: str, log: logging.Logger) -> None:
    """为话题列表下载参考视频，就地写入 media 字段。"""
    tikhub_token = os.environ.get("TIKHUB_API_TOKEN", "")
    if not tikhub_token:
        log.warning("未找到 TIKHUB_API_TOKEN，跳过媒体下载")
        return
    try:
        from tikhub import TikHub
        from utils.topic_media import fetch_topic_videos, build_media_field
        client = TikHub(api_key=tikhub_token)
        media_root = PROJECT_ROOT / "workspace" / username / "hot_topics_media"
        for t in topics:
            paths = fetch_topic_videos(client, t, media_root)
            t["media"] = build_media_field(paths)
            if t["media"]:
                log.info(f"  [media] ✓ {t['title'][:25]} → {len(paths)} 条视频")
                for p in paths:
                    log.info(f"    {p}")
            else:
                log.warning(f"  [media] ✗ {t['title'][:25]} 全部来源下载失败，media=null")
    except Exception as e:
        log.warning(f"媒体下载异常（不影响话题写入）：{e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="定时刷新热点话题")
    parser.add_argument("--username", required=True)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP,
                        help=f"每平台保留前 N 条，默认 {DEFAULT_TOP}")
    parser.add_argument("--max-topics", type=int, default=MAX_DAILY_TOPICS,
                        help=f"最终写入队列的话题上限，默认 {MAX_DAILY_TOPICS}")
    parser.add_argument("--force", action="store_true",
                        help="忽略时间检查，强制刷新")
    parser.add_argument("--no-score", action="store_true",
                        help="跳过全部 LLM 过滤（快速测试用）")
    parser.add_argument("--no-media", action="store_true",
                        help="跳过参考视频下载")
    parser.add_argument("--media-only", action="store_true",
                        help="仅为 state 中现有话题补充媒体下载，不重新拉取或过滤")
    args = parser.parse_args()

    log = setup_logger(args.username)
    state = load_state(args.username)

    # --media-only：跳过拉取和过滤，直接给现有话题补下载
    if args.media_only:
        new_topics = state.get("topics", [])
        if not new_topics:
            log.error("state 中无话题，请先运行正常模式拉取")
            sys.exit(1)
        log.info(f"[media-only] 为 {len(new_topics)} 条现有话题补充媒体下载...")
        _download_media(new_topics, args.username, log)
        state["topics"] = new_topics
        save_state(args.username, state)
        log.info("[media-only] 完成")
        return

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

    if not args.no_score:
        # 第一轮：内容安全 + 传播价值打分，过滤政治/引战内容
        new_topics = score_and_filter_topics(new_topics, log)
        # 第二轮：Seedance 生成可行性 + 风格/比例/概念标注
        new_topics = seedance_filter_topics(new_topics, log)
    else:
        log.info("已跳过 LLM 过滤（--no-score）")

    # 按 content_score 取 top N，控制每日入队数量
    if len(new_topics) > args.max_topics:
        new_topics = new_topics[: args.max_topics]
        log.info(f"按上限截断，保留 {args.max_topics} 条")

    # 下载参考视频，写入 media 字段
    if not args.no_media:
        _download_media(new_topics, args.username, log)
    else:
        log.info("已跳过媒体下载（--no-media）")

    log.info(f"本次入队 {len(new_topics)} 条话题：")
    for t in new_topics:
        style = t.get("video_style", "-")
        ratio = t.get("preferred_ratio", "-")
        score = t.get("content_score", "-")
        has_media = "✓" if t.get("media") else "✗"
        log.info(f"  [{t['platform']}] #{t['rank']} [{score}分/{style}/{ratio}] [media={has_media}] {t['title']}")
        if t.get("video_concept"):
            log.info(f"    concept: {t['video_concept']}")

    now = datetime.now().isoformat(timespec="seconds")
    state["last_fetch_time"] = now
    state["topics"] = new_topics
    save_state(args.username, state)

    log.info(f"拉取完成 | 写入 {len(new_topics)} 条 | 状态文件：{_state_path(args.username)}")


if __name__ == "__main__":
    main()
