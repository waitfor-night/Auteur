# Skill: fetch-hot-topics

抓取多平台热点话题，经过两轮 LLM 过滤，并为每条话题下载本地参考视频，最终写入 `workspace/<username>/hot_topics_state.json` 供后续生成流程消费。

## 前置依赖

| 依赖 | 说明 |
|------|------|
| `ARK_API_KEY` | Doubao/Ark LLM API，用于两轮过滤 |
| `TIKHUB_API_TOKEN` | TikHub SDK，用于媒体下载（可选，缺失则跳过媒体） |
| `utils/hot_topics_cron.py` | 完整抓取/过滤/下载入口脚本 |
| `utils/topic_media.py` | 媒体下载模块 |
| `utils/fetch_topics.py` | 底层 RSS 拉取，被 cron 脚本调用 |

---

## 完整流程

```
RSS 拉取（摸摸鱼 + 抖音热搜）
    ↓
第一轮 LLM 过滤：内容安全 + 传播价值（1-10 分，低于 5 丢弃）
    ↓
第二轮 LLM 过滤：Seedance 生成可行性 → 标注 video_style / preferred_ratio / video_concept
    ↓
按分数截断至 max_topics 条
    ↓
媒体下载：B站直链 / 抖音搜索 → 本地 mp4
    ↓
写入 hot_topics_state.json
```

---

## 命令

### 标准运行（推荐）

```bash
python3 utils/hot_topics_cron.py --username <username>
```

2 天内有缓存时自动跳过，无需外部调度。

### 强制刷新

```bash
python3 utils/hot_topics_cron.py --username <username> --force
```

### 调整参数

```bash
# 每平台拉取前 10 条，最终入队不超过 5 条
python3 utils/hot_topics_cron.py --username <username> --top 10 --max-topics 5

# 跳过 LLM 过滤（快速测试）
python3 utils/hot_topics_cron.py --username <username> --no-score

# 跳过媒体下载
python3 utils/hot_topics_cron.py --username <username> --no-media

# 仅补充媒体下载（不重新拉取话题）
python3 utils/hot_topics_cron.py --username <username> --media-only
```

---

## 输出：hot_topics_state.json 结构

```json
{
  "last_fetch_time": "2026-04-22T10:00:00",
  "topics": [
    {
      "platform": "知乎",
      "rank": 1,
      "title": "为什么年轻人越来越不爱买车了",
      "url": "https://www.zhihu.com/...",
      "status": "unused",
      "use_count": 0,
      "content_score": 8,
      "seedance_viable": true,
      "video_style": "question",
      "preferred_ratio": "9:16",
      "video_concept": "城市停车场空旷，年轻人骑车或步行经过",
      "media": {
        "ref_video": "/abs/path/workspace/<username>/hot_topics_media/a1b2c3d4/ref_video.mp4",
        "fetched_at": "2026-04-22T10:05:00"
      }
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `status` | `unused` / `in_progress` / `done` |
| `content_score` | 第一轮打分（1-10），低于 5 已被过滤 |
| `video_style` | `showcase` / `question` / `narrative` |
| `preferred_ratio` | `9:16`（竖屏）/ `16:9`（横屏） |
| `video_concept` | 供 Planner 参考的一句话视觉概念（≤15字） |
| `media.ref_video` | 本地参考视频**绝对路径**，不存在或下载失败时为 `null` |

媒体文件路径规则：`workspace/<username>/hot_topics_media/<md5(title)[:8]>/ref_video.mp4`（纯 ASCII，无中文）。

---

## 常见错误与重试

| 错误 | 原因 | 处理 |
|------|------|------|
| `SSL: UNEXPECTED_EOF_WHILE_READING` | CDN 断连 | 重跑命令，通常 1-2 次成功 |
| `SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC` | TLS 记录损坏 | 重跑，最多 3 次 |
| TikHub `HTTP 400` on `fetch_video_search_result_v2` | 关键词触发 API 过滤 | 代码内置多候选词自动重试，无需修改代码 |
| RSS `SSL EOF` from momoyu | 上游 SSL 抖动 | 重跑 `--force`；话题列表已有时加 `--no-media` 跳过 RSS |
| TikHub `HTTP 402 / 429` | 配额或限流 | 等 60 秒再跑；确认 `TIKHUB_API_TOKEN` 已设置 |

**原则：任何错误先重试 2-3 次，再考虑排查原因，不要直接修改代码。**

---

## 参考代码

- [`reference/hot_topics_cron.py`](reference/hot_topics_cron.py) — 主脚本
- [`reference/topic_media.py`](reference/topic_media.py) — 媒体下载模块
