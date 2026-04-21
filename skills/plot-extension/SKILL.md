---
name: plot-extension
description: 用户要「续写/延长」结尾或某段，或「生成」且可能带参考视频/参考图。Use when edit_prompt is extend ending, 续写, 向前/向后延长, "X秒短片", or 参考视频/图生成。
---

# PLOT_EXTENSION

## 何时用（When to use）

- **触发条件**：用户要**续写、延长**结尾或某段，或**按时长生成**（如「12秒科幻短片」）；也包括**生成任务中提供参考视频或参考图**。
- **典型说法**：续写、延长、向前延长、向后延长、X秒短片、extend、生成、参考这段视频生成 等。
- **与其它任务区分**：不做按镜头查找（SEARCH_AND_EXTRACT）或按片段换人/风格（VFX_EDIT）。

## 应调用的工具及调用顺序

### 工作流 A：续写/延长原片

1. **get_video_metadata_tool** — 获取 duration_s。
2. **split_video_by_duration_tool** — 若 duration_s > time_length 则切分。
3. **手写 Plan** — 向后延长：[KEEP, ..., GENERATE]；向前延长：[GENERATE, KEEP, ...]。GENERATE 的 reference_resources 放原片段路径。
4. 可选 **submit_video_execution_plan**。

---

### 工作流 B：生成 + 参考视频/图

1. **video_understanding_tool** — 理解参考视频。
2. **image_understanding_tool** — 若有 image_paths 必须调用；execution_prompt 中补充「参考图N（图中描述：***）」。
3. **手写 Plan** — 一条或多条 GENERATE，reference_resources 放参考视频和图片路径。
4. 可选 **submit_video_execution_plan**。

---

### 工作流 C：纯生成（无参考视频）

1. **image_understanding_tool** — 若有 image_paths 必须调用。
2. **手写 Plan** — ≤15s 一条 GENERATE；>15s 按时段拆多条 GENERATE。execution_prompt **完整照抄**用户 edit_prompt，不可省略。
3. 可选 **submit_video_execution_plan**。

---

## action_trigger by task_type

- 保留原片段 → **KEEP**；生成/延长段 → **GENERATE**
- **time_range 必填**：格式 `{"start": "mm:ss", "end": "mm:ss"}`
- **reference_resources**：续写时放原片段路径；生成时放参考视频/图路径

## Example

```json
{
  "task_metadata": { "task_type": "PLOT_EXTENSION", "global_prompt": "Extend ending by 2s with flying.", "requires_merge": true },
  "timeline": [
    { "segment_id": 1, "time_range": {"start": "00:00", "end": "00:05"}, "segment_path": "/tmp/v/seg_1.mp4", "action_trigger": "KEEP", "execution_prompt": "Original footage.", "reference_resources": [] },
    { "segment_id": 2, "time_range": {"start": "00:05", "end": "00:07"}, "segment_path": "", "action_trigger": "GENERATE", "execution_prompt": "Extend 2s, character flies up.", "reference_resources": ["/tmp/v/seg_1.mp4"], "technical_params": { "extend_duration": "2.0s" } }
  ]
}
```
