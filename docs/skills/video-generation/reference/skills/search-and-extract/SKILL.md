---
name: search-and-extract
description: 用户要「找到/筛选/提取」某些镜头再合并。Use when edit_prompt is find/filter/extract shots (e.g. "find all shots of X", "找出…的镜头合并").
---

# SEARCH_AND_EXTRACT

## 何时用（When to use）

- **触发条件**：用户指令是「找/筛选/提取」某类镜头再合并成片。
- **典型说法**：找、筛选、提取、find all、找出、merge into one、合并成一个、所有…的镜头、把…的片段剪出来 等。
- **与其它任务区分**：不涉及换人/换脸/风格（VFX_EDIT）；不涉及续写/延长/生成整片（PLOT_EXTENSION）。

## 应调用的工具及调用顺序

**重要**：本任务必须有 video_path。若 video_path 为空，**必须先调用 getUserMessageTool** 询问用户。

1. **video_understanding_tool** — 全片理解并获取语义分镜列表（`semantic_scene_list`），需将用户搜索意图嵌入 prompt，每个场景标注 `is_match: true/false`。
2. **image_understanding_tool** — 若有 image_paths 则调用。
3. **Multi_model_understanding_tool** — 判断 edit_prompt 是否模糊；若 is_ambiguous=true，调用 **getUserMessageTool** 获取 clarified_edit_prompt。
4. **get_video_metadata_tool** — 获取时长、fps。
5. **video_scene_logic_split_tool** — 技术分镜，得到 `technical_scene_list`。
6. **分镜列表融合**（Agent 自行完成）— 融合 `semantic_scene_list` 和 `technical_scene_list`，输出 `final_scene_list`（格式：`[[start_sec, end_sec], ...]`）。
7. **detect_and_resegment_shots_tool** — 使用 `final_scene_list`，校验并按需切分。
8. **video_split_tool** — 按场景列表切分，得到 segment_list。
9. **analyze_segment_relevance_tool** — 传入 segment_list 和用户搜索指令；每个片段得到 editing_required（true=命中）。
10. **手写 Plan**：
    - 命中（editing_required=true）→ action_trigger=**KEEP**
    - 未命中 → action_trigger=**DISCARD**
    - execution_prompt 填命中/未命中原因，**无 GENERATE**
    - **time_range 必填**：格式 `{"start": "mm:ss", "end": "mm:ss"}`
11. 可选 **submit_video_execution_plan**。

## action_trigger by task_type

- Pass the user's search instruction as edit_prompt to analyze_segment_relevance_tool.
- editing_required=true (segment matches search) → action_trigger=**KEEP**; editing_required=false (does not match) → action_trigger=**DISCARD**.
- execution_prompt = event or short match/miss reason. No GENERATE.
- **time_range 必须填写**：每个 timeline item 必须包含 time_range（从 segment_list 的 start_time/end_time 获取），格式为 `{"start": "mm:ss", "end": "mm:ss"}`。

## Example

User instruction: "Find all shots of cats eating in the video and merge into one video."

```json
{
  "task_metadata": { "task_type": "SEARCH_AND_EXTRACT", "global_prompt": "Find all shots of cats eating.", "requires_merge": true },
  "timeline": [
    { "segment_id": 1, "time_range": {"start": "00:00", "end": "00:05"}, "segment_path": "/tmp/v/seg_1.mp4", "action_trigger": "DISCARD", "execution_prompt": "Empty room, no subject.", "reference_resources": [] },
    { "segment_id": 2, "time_range": {"start": "00:05", "end": "00:12"}, "segment_path": "/tmp/v/seg_2.mp4", "action_trigger": "KEEP", "execution_prompt": "Found a cat eating dry food from a bowl.", "reference_resources": [] }
  ]
}
```
