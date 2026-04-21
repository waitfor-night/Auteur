---
name: summary
description: 仅生成摘要/字幕，不合并为成片。Use when user only wants summary or captions, not a merged video.
---

# SUMMARY

## 何时用（When to use）

- **触发条件**：用户**只要摘要或字幕**，不需要把片段合并成一条新成片。
- **典型说法**：摘要、字幕、summary、caption、subtitle、生成文字说明 等。
- **与其它任务区分**：不做找镜头（SEARCH_AND_EXTRACT）、换人/风格（VFX_EDIT）、续写延长（PLOT_EXTENSION）。

## 应调用的工具及调用顺序

**重要**：本任务必须有 video_path。若 video_path 为空，**必须先调用 getUserMessageTool** 询问用户。

### 工作流 A：全片摘要

1. **video_understanding_tool** — 全片理解，获取视频整体描述。
2. **手写 Plan** — timeline 单条，action_trigger=KEEP，execution_prompt=摘要内容，requires_merge=false。
3. 可选 **submit_video_execution_plan**。

### 工作流 B：按镜头字幕/摘要

1. **video_understanding_tool** — 全片理解，获取语义分镜列表（`semantic_scene_list`）。
2. **image_understanding_tool** — 若有 image_paths 则调用。
3. **get_video_metadata_tool** — 获取视频时长、fps 等元数据。
4. **video_scene_logic_split_tool** — 技术分镜，得到 `technical_scene_list`。
5. **分镜列表融合**（Agent 自行完成）— 融合语义分镜和技术分镜，输出 `final_scene_list`（格式：`[[start_sec, end_sec], ...]`）。
6. **detect_and_resegment_shots_tool** — 使用 `final_scene_list`，校验并按需切分。
7. **video_split_tool** — 按场景列表切分，得到 segment_list。
8. **手写 Plan** — 为每个 segment 填写 timeline item，action_trigger=KEEP，execution_prompt=该段字幕/摘要，requires_merge=false。
9. 可选 **submit_video_execution_plan** — 提交最终 Plan。

---

## action_trigger by task_type

- requires_merge = **false**；所有片段 action_trigger = **KEEP**；**无 GENERATE**
- **time_range 必填**：格式 `{"start": "mm:ss", "end": "mm:ss"}`
- **Actor 无需执行视频编辑**，仅输出文字结果

## Example

```json
{
  "task_metadata": { "task_type": "SUMMARY", "global_prompt": "为视频每个镜头生成字幕", "requires_merge": false },
  "timeline": [
    { "segment_id": 1, "time_range": {"start": "00:00", "end": "00:08"}, "segment_path": "/tmp/v/seg_1.mp4", "action_trigger": "KEEP", "execution_prompt": "清晨的阳光洒进卧室，闹钟响起，主人公睁开眼睛开始新的一天。", "reference_resources": [] },
    { "segment_id": 2, "time_range": {"start": "00:08", "end": "00:15"}, "segment_path": "/tmp/v/seg_2.mp4", "action_trigger": "KEEP", "execution_prompt": "主人公走进厨房，打开冰箱取出牛奶，开始准备早餐。", "reference_resources": [] }
  ]
}
```
