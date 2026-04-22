---
name: long-video-create
description: 分镜规划与视频生成。根据分镜内容生成多段视频并合并为长视频。
---

# LONG_VIDEO_CREATE

## 何时用（When to use）

- **触发条件**：用户提供剧本/分镜文案/故事脚本，希望从零生成一部长视频（无参考视频输入）。
- **典型说法**：根据剧本生成视频、把这个故事做成视频、剧本转视频、生成剧情视频 等。
- **与其它任务区分**：PLOT_EXTENSION 是有参考视频需要续写/延长，或短时长纯生成；LONG_VIDEO_CREATE 是仅提供剧本文本，需完整的「分段生成 → 合并」流程。

## 应调用的工具及调用顺序

1. **剧本解析与分镜规划**（Agent 自行完成）— 将剧本拆分为多个分镜段落；确定每段时长（用户指定总时长 → 每段 = total_duration / 场景数；否则每段 = time_length）；逐对判断段间连续性（场景切换 → 独立；同场景 + 事件延续 → 连续）。
2. **手写 Plan** — 所有段落 action_trigger=GENERATE，time_range 必填，execution_prompt 要求写详细。
3. 可选 **submit_video_execution_plan**。
4. **batch_video_generate_tool** 或多次 **video_generate_tool** — 独立段落可并发生成；连续段落需分批执行（先生成前段 → 获取 last_frame_path → 加入后段 image_paths → 再生成后段）。必须传入 segment_ids。
5. **merge_video_tool** — 将所有生成的视频片段按顺序合并为最终长视频。

---

## action_trigger by task_type

- 所有段落 action_trigger = **GENERATE**（纯生成任务）
- **time_range 必填**：格式 `{"start": "mm:ss", "end": "mm:ss"}`
- **execution_prompt 要求写详细**：包含完整分镜描述；连续段落需写明「从上一段结尾自然接续，保持风格、光线连贯」
- **reference_resources**：填入该段涉及的角色图 + 场景图路径

## 段间连续性判断（核心）

| 情况 | 判断 | 依据 |
|-----|------|-----|
| 同一场景，台词/动作接续 | ✅ 连续 | 同场景+事件延续 |
| 离开A地 → 到达B地 | ❌ 独立 | 场景切换（即使有因果关系） |
| 同一房间内对话继续 | ✅ 连续 | 同场景+事件延续 |
| 时间跳跃（"X年后"） | ❌ 独立 | 时间断裂 |

## Example

```json
{
  "task_metadata": {
    "task_type": "LONG_VIDEO_CREATE",
    "global_prompt": "三幕剧。连续性分析：幕1→幕2场景切换(独立)，幕2→幕3同场景(连续)。",
    "requires_merge": true
  },
  "timeline": [
    {
      "segment_id": 1,
      "time_range": {"start": "00:00", "end": "00:15"},
      "segment_path": "",
      "action_trigger": "GENERATE",
      "execution_prompt": "正午，营地外，艾拉躲在灌木丛后观察敌人动向...",
      "reference_resources": ["/path/to/aila.png", "/path/to/camp.png"],
      "technical_params": {"duration": "15s"}
    },
    {
      "segment_id": 2,
      "time_range": {"start": "00:15", "end": "00:30"},
      "segment_path": "",
      "action_trigger": "GENERATE",
      "execution_prompt": "山谷入口，艾拉和古恩站在山坡上俯瞰...",
      "reference_resources": ["/path/to/aila.png", "/path/to/valley.png"],
      "technical_params": {"duration": "15s"}
    },
    {
      "segment_id": 3,
      "time_range": {"start": "00:30", "end": "00:45"},
      "segment_path": "",
      "action_trigger": "GENERATE",
      "execution_prompt": "从上一段结尾自然接续，保持山谷场景连贯。两人开始下山...",
      "reference_resources": ["/path/to/aila.png", "/path/to/valley.png"],
      "technical_params": {"duration": "15s"}
    }
  ]
}
```
