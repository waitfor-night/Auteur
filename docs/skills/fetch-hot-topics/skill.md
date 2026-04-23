---
name: fetch-hot-topics
description: 当 agent 需要抓取当前热点话题时使用这个 skill。通过 TikHub SDK 并行拉取抖音/知乎/B站/TikTok 热榜，经两轮 LLM 过滤和 Seedance 可行性标注后，从抖音/TikTok/YouTube 下载多条参考视频，写入 hot_topics_state.json 供视频生成流程消费。
---

# Skill: fetch-hot-topics

通过 TikHub SDK 并行抓取 4 个平台热点话题，经两轮 LLM 过滤，并从国内外平台下载参考视频，写入 `workspace/<username>/hot_topics_state.json`。

## 前置依赖

| 依赖 | 说明 |
|------|------|
| `ARK_API_KEY` | Doubao/Ark LLM API，用于两轮过滤 + 关键词提取翻译 |
| `TIKHUB_API_TOKEN` | TikHub SDK，用于热榜拉取 + 媒体搜索下载 |
| `yt-dlp` | YouTube 视频下载（`pip install yt-dlp`） |
| `utils/hot_topics_cron.py` | 完整流水线入口 |
| `utils/topic_media.py` | 多平台媒体下载模块 |
| `utils/fetch_topics.py` | TikHub 热榜拉取（抖音/知乎/B站/TikTok） |

---

## 完整流程

```
TikHub 并行拉取（4 路）
  ├─ 抖音热搜（douyin_web.fetch_hot_search_result）
  ├─ 知乎热榜（zhihu_web.fetch_hot_list）
  ├─ B站热搜（bilibili_web.fetch_hot_search）
  └─ TikTok趋势（tiktok_web.fetch_trending_searchwords）
    ↓ 每平台取 top N 条（默认 10）
第一轮 LLM 过滤（doubao-seed-2-0-pro-260215）
  内容安全 + 传播价值打分 1-10，低于 5 丢弃
  过滤：政治敏感 / 引战对立 / 无视觉价值
    ↓
第二轮 LLM 过滤（doubao-seed-2-0-pro-260215）
  Seedance 生成可行性评估
  标注：video_style / preferred_ratio / video_concept
    ↓ 按 content_score 截断至 max_topics 条（默认 3）
媒体下载（每条话题并行抓取国内外来源）
  ├─ B站直链（URL 含 BVxxx 时）
  ├─ 抖音搜索（LLM 提取中文关键词 → TikHub douyin_web）
  ├─ TikTok 搜索（LLM 翻译英文关键词 → TikHub tiktok_app_v3）
  └─ YouTube 搜索（同英文关键词 → TikHub youtube_web + yt-dlp fallback）
    ↓
写入 hot_topics_state.json
```

**关键词提取逻辑**：每条话题调用一次 LLM，同时生成国内关键词（中文，供抖音）和海外关键词（英文，供 TikTok/YouTube）。ARK API 不可用时 fallback 到字符串截断。

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
# 每平台拉取前 5 条，最终入队不超过 3 条（快速测试）
python3 utils/hot_topics_cron.py --username <username> --top 5 --max-topics 3

# 跳过 LLM 过滤（快速验证数据源）
python3 utils/hot_topics_cron.py --username <username> --no-score

# 跳过媒体下载
python3 utils/hot_topics_cron.py --username <username> --no-media

# 仅为已有话题补充媒体下载（不重新拉取/过滤）
python3 utils/hot_topics_cron.py --username <username> --media-only
```

### 单独调试热榜拉取

```bash
# 全平台并行，每平台前 10 条
python3 utils/fetch_topics.py --top 10

# 仅某个平台
python3 utils/fetch_topics.py --douyin --top 10
python3 utils/fetch_topics.py --zhihu --top 5
python3 utils/fetch_topics.py --bilibili
python3 utils/fetch_topics.py --tiktok

# JSON 输出
python3 utils/fetch_topics.py --json --top 5
```

---

## 输出：hot_topics_state.json 结构

```json
{
  "last_fetch_time": "2026-04-23T14:00:00",
  "topics": [
    {
      "platform": "抖音热搜",
      "rank": 1,
      "title": "旅行熟人局到底有多废嘴",
      "url": "https://www.douyin.com/video/xxx",
      "status": "unused",
      "use_count": 0,
      "content_score": 9,
      "seedance_viable": true,
      "video_style": "showcase",
      "preferred_ratio": "9:16",
      "video_concept": "好友旅行嬉笑聊天",
      "media": {
        "ref_videos": [
          "/abs/path/workspace/<username>/hot_topics_media/1b45e718/ref_tiktok.mp4",
          "/abs/path/workspace/<username>/hot_topics_media/1b45e718/ref_youtube.mp4"
        ],
        "fetched_at": "2026-04-23T16:50:00"
      }
    }
  ]
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `platform` | `抖音热搜` / `知乎热榜` / `B站热搜` / `TikTok趋势` |
| `status` | `unused` / `in_progress` / `done` |
| `use_count` | 累计被使用次数（轮完后重置为 unused 并 +1） |
| `content_score` | 第一轮打分（1-10），低于 5 已被过滤 |
| `seedance_viable` | 第二轮：Seedance 能否生成有效视频 |
| `video_style` | `showcase` / `question` / `narrative` |
| `preferred_ratio` | `9:16`（竖屏）/ `16:9`（横屏） |
| `video_concept` | LLM 生成的一句话视觉概念，≤15字 |
| `media.ref_videos` | 本地参考视频绝对路径列表，全部失败时 `media` 为 `null` |

媒体文件路径：`workspace/<username>/hot_topics_media/<md5(title)[:8]>/ref_<source>.mp4`
`source` 取值：`bilibili` / `douyin` / `tiktok` / `youtube`

---

## 媒体下载详细逻辑

每条话题的媒体抓取由 `utils/topic_media.py` 的 `fetch_topic_videos()` 完成：

1. **关键词提取**（LLM，一次调用）：生成 `cn_kw`（中文）和 `en_kw`（英文翻译）
2. **B站**：URL 含 `BVxxx` 时走直链（TikHub `bilibili_web`），无需搜索
3. **抖音**：URL 含抖音 video_id 时走直链，否则用 `cn_kw` 搜索（TikHub `douyin_web`）
4. **TikTok + YouTube**：用 `en_kw` 并行搜索
   - TikTok：`tiktok_app_v3.fetch_video_search_result`，搜索结果直接含播放 URL
   - YouTube：`youtube_web.search_video`（参数名 `search_query`），TikHub 直链 → fallback yt-dlp
5. 已存在且 >10KB 的文件直接复用，跳过重复下载

---

## 常见错误与处理

| 错误 | 原因 | 处理 |
|------|------|------|
| `SSL: UNEXPECTED_EOF` / `DECRYPTION_FAILED` | CDN/TLS 抖动 | 重跑，内置 3 次重试，通常自愈 |
| TikHub `HTTP 400` on douyin search | 关键词触发 API 过滤 | LLM 已生成精炼关键词，重跑一次 |
| YouTube `unexpected keyword argument 'keyword'` | 参数名应为 `search_query` | 已修复，勿回退 |
| YouTube TikHub 直链 `HTTP 400` | TikHub 限制 | 已自动 fallback 到 yt-dlp |
| yt-dlp 超时（120s） | YouTube 网络慢 | 正常现象，TikTok 视频通常已成功 |
| TikHub `HTTP 402 / 429` | 配额/限流 | 等 60 秒再跑；确认 `TIKHUB_API_TOKEN` 有效 |
| LLM 打分 `TPM limit exceeded` | API 配额耗尽 | 等配额恢复；临时加 `--no-score` 跳过打分 |

**原则：任何错误先重试 2-3 次，再考虑排查原因。**

---

## 参考代码

- `utils/fetch_topics.py` — TikHub 热榜拉取（抖音/知乎/B站/TikTok）
- `utils/hot_topics_cron.py` — 主流水线脚本
- `utils/topic_media.py` — 多平台媒体下载（`fetch_topic_videos` / `extract_search_keywords`）
