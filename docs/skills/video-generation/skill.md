---
name: video-generation
description: 当 agent 需要根据用户指令生成视频时使用这个 skill。通过 VideoAssistant 的 Planner-Actor 多轮迭代架构完成视频生成、编辑、续写等任务，支持从热点话题直接生成，结果可直接传入发布 skill。
---

# Skill: video-generation

调用 `VideoAssistant` 将用户指令转化为视频。VideoAssistant 内部运行 Planner-Actor 多轮迭代架构，支持纯生成、视频编辑、剧本转视频等多种场景。

## 前置依赖

| 依赖 | 说明 |
|------|------|
| `ARK_API_KEY` | Doubao/Ark LLM API（必填） |
| `TOS_ACCESS_KEY` / `TOS_SECRET_KEY` | ByteDance TOS 文件存储（必填） |
| `video_assistant.py` | 主入口，位于项目根目录 |

---

## 调用方式

所有相对路径均以 `reference/` 目录为根（即 `video_assistant.py` 所在目录），无论从哪个 CWD 调用。生成结果、context、trace 均写入 `reference/workspace/`。

```python
from video_assistant import VideoAssistant

assistant = VideoAssistant(
    output_dir="workspace/output/<task_name>",   # 相对路径，写到 reference/workspace/output/
    time_length=15,                               # 每段视频最大时长（秒）
    total_duration=None,                          # 视频总时长（秒），可选
    allow_interactive=False,                      # 非交互模式（pipeline 中固定 False）
    planner_only=False,                           # 仅运行 Planner（调试用）
    context_id=None,                              # 恢复已有会话，传 ctx_xxx ID
    username="<username>",                        # 用户名，context/trace 按此隔离
    context_subdir=None,                          # 可选子目录，实现一 task 一 context
)

result = assistant.run(
    user_input="<用户指令>",                      # 主指令（必填）
    video_path="",                                # 输入视频路径（编辑场景用）
    image_paths=[],                               # 参考图路径列表
    ref_videos=[],                                # 参考视频路径列表（热点媒体抓取结果）
)
```

`result` 字典结构：

```python
{
    "success": True,
    "output_path": "/abs/path/to/output.mp4",   # 生成视频绝对路径
    "result_video": "/abs/path/to/output.mp4",  # 同上（别名字段）
    "trace_id": "ep_20260422_abc123",
    "trace_path": "/abs/path/workspace/<username>/trace/ep_xxx.json",
    "xhs_title": "适合小红书的标题",
    "xhs_tags": ["tag1", "tag2"],
    "context_id": "ctx_xxx",
    "total_rounds": 1,
    "duration_sec": 42.5,
    "error": None,                               # 失败时有值
}
```

---

## 从热点话题生成视频

当上游来自 `fetch-hot-topics` skill 时，按如下规则构造参数：
- `user_input` 包含话题标题、风格、概念等文字信息
- `ref_videos` 直接传入 `media.ref_videos` 列表，Planner 可通过多模态理解工具分析这些视频

```python
import json
from video_assistant import VideoAssistant

# 从 hot_topics_state.json 取到的 topic 对象
topic = {
    "title": "为什么年轻人越来越不爱买车了",
    "platform": "知乎",
    "url": "https://www.zhihu.com/...",
    "video_style": "question",         # showcase / question / narrative
    "preferred_ratio": "9:16",         # 9:16 / 16:9
    "video_concept": "城市停车场空旷，年轻人骑车或步行经过",
    "media": {
        "ref_videos": [
            "/abs/path/workspace/<username>/hot_topics_media/a1b2c3d4/ref_douyin.mp4",
            "/abs/path/workspace/<username>/hot_topics_media/a1b2c3d4/ref_tiktok.mp4",
            "/abs/path/workspace/<username>/hot_topics_media/a1b2c3d4/ref_youtube.mp4",
        ],
        "fetched_at": "2026-04-23T10:05:00"
    }
}

username    = "<username>"
title       = topic["title"]
platform    = topic.get("platform", "")
url         = topic.get("url", "")
video_style = topic.get("video_style") or "showcase"
ratio       = topic.get("preferred_ratio") or "9:16"
concept     = topic.get("video_concept") or ""
ref_videos  = (topic.get("media") or {}).get("ref_videos") or []

lines = [f"根据热点话题「{title}」制作一个{video_style}风格的短视频。"]
if concept:
    lines.append(f"视觉概念：{concept}")
lines.append(f"画面比例：{ratio}")
lines.append(f"来源平台：{platform}，参考链接：{url}")
if ref_videos:
    lines.append(f"已提供 {len(ref_videos)} 条本地参考视频，可通过视频理解工具分析风格。")
user_input = "\n".join(lines)

assistant = VideoAssistant(
    output_dir=f"workspace/output/hot_topics_{title[:10]}",
    username=username,
    allow_interactive=False,
)
result = assistant.run(
    user_input=user_input,
    ref_videos=ref_videos,   # 参考视频路径列表，写入 Runtime Inputs 供 Planner 按需调用
)

# 保存 result 供后续发布流程读取
with open("/tmp/va_result.json", "w") as f:
    json.dump(result, f, ensure_ascii=False)

print(json.dumps(result, ensure_ascii=False))
```

**Planner 使用 `ref_videos` 的方式：**

Runtime Inputs 中会出现 `- ref_videos: ["/abs/path/ref_douyin.mp4", ...]`。Planner 可通过以下工具利用这些视频：

| 工具 | 用途 |
|------|------|
| `video_understanding_tool` | 理解参考视频的画面风格、节奏、构图 |
| `mmut_extract_frames_tool` | 提取关键帧，用作后续生成的参考图 |

不需要每次都分析全部参考视频——仅在 user_input 或 video_concept 不够具体时才调用。

---

## 其他常见场景

### 纯生成（无参考视频）

```python
assistant = VideoAssistant(
    output_dir="workspace/output/test",
    time_length=5,
    username="<username>",
    allow_interactive=False,
)
result = assistant.run(user_input="生成一段5秒的测试视频，展示自然风景")
```

### 视频编辑

```python
assistant = VideoAssistant(
    output_dir="workspace/output/edited",
    username="<username>",
    allow_interactive=False,
)
result = assistant.run(
    user_input="将视频风格改为赛博朋克",
    video_path="/abs/path/input.mp4",
    image_paths=["/abs/path/ref.png"],
)
```

### 恢复已有会话

```python
assistant = VideoAssistant(
    output_dir="workspace/output/resumed",
    username="<username>",
    context_id="ctx_20260422_abc123",  # 从上次 result["context_id"] 获取
)
result = assistant.run()
```

### 长视频生成

```python
assistant = VideoAssistant(
    output_dir="workspace/output/long_video",
    time_length=15,
    total_duration=120,   # 目标总时长 120 秒
    username="<username>",
    allow_interactive=False,
)
result = assistant.run(user_input="根据以下剧本生成视频：...")
```

---

## 输出文件位置

| 文件 | 路径 |
|------|------|
| 生成视频 | `result["output_path"]` 或 `result["result_video"]` |
| Context 记录 | `workspace/<username>/context/ctx_<timestamp>_<hash>.json` |
| Trace 记录 | `workspace/<username>/trace/ep_<timestamp>_<hash>.json` |

---

## Reference 目录结构

`reference/` 包含运行 VideoAssistant 所需的全部代码，可直接迁移到新机器：

```
reference/
├── video_assistant.py          # 主入口
├── planner.py                  # Planner Agent 定义
├── actor.py                    # Actor Agent 定义
├── prompts.py                  # Planner/Actor 系统 prompt
├── requirements.txt            # Python 依赖
├── .env.example                # 环境变量模板（复制为 .env 后填写）
│
├── tools/                      # 所有工具实现
│   ├── plannerTools.py         # Planner 工具聚合入口
│   ├── actorTools.py           # Actor 工具聚合入口
│   ├── constants.py
│   ├── generationTools/        # 视频生成（Seedance API）
│   ├── mmUnderstandingTools/   # 多模态视频/图像理解
│   ├── physicsEditTools/       # 物理编辑
│   ├── logicSplitTools/        # 视频逻辑分镜
│   ├── novelPipeline/          # 小说转剧本流水线
│   ├── imageGen/               # 图片生成
│   ├── ioTools/                # 文件读写
│   ├── submitPlanTools/        # 多阶段计划提交/存取
│   ├── skillTools/             # 动态 skill 加载
│   ├── userTools/              # 用户反馈
│   ├── searchTools/            # 搜索工具
│   └── storyAssetTools/        # 故事资产管理
│
├── skills/                     # 运行时 skill markdown 文件
│   ├── skill_loader.py         # 根据用户输入动态加载 skill
│   ├── META_SKILL.md           # 默认 meta-skill（任务编排逻辑）
│   ├── long-video-create/
│   ├── novel-to-screenplay/
│   ├── plot-extension/
│   ├── script-lens/
│   ├── search-and-extract/
│   ├── summary/
│   ├── vfx-edit/
│   ├── video-generate/
│   └── visual-prompt-opt/
│
├── utils/                      # 运行时工具函数
│   ├── trace_recorder.py       # TraceRecorder，工具调用埋点
│   ├── context_recorder.py     # RunContext，多轮迭代记忆
│   ├── tools_implement.py      # 视频/图像理解辅助函数
│   ├── video_utils.py          # ffprobe 视频元数据
│   ├── story_gen_tools.py      # 剧本实体提取、图片生成
│   ├── auto_video_splite.py    # 视频自动分割
│   └── seedance_edit.py        # Seedance 编辑 API 封装
│
└── workspace_templates/        # 用户个性化文件模板
    ├── memory.md               # 用户偏好记忆模板
    └── content_strategy.md     # 账号内容策略模板
```

### 迁移到新机器

```bash
# 1. 将 reference/ 内容复制到新项目根目录
cp -r reference/* /path/to/new-project/

# 2. 配置环境变量
cp .env.example .env
# 填写 ARK_API_KEY / TOS_ACCESS_KEY / TOS_SECRET_KEY

# 3. 安装依赖
pip install -r requirements.txt
sudo apt install -y ffmpeg

# 4. （可选）配置用户个性化文件
mkdir -p workspace/<username>/memory
cp workspace_templates/memory.md workspace/<username>/memory/memory.md
cp workspace_templates/content_strategy.md workspace/<username>/content_strategy.md

# 5. 验证运行
python video_assistant.py
```
