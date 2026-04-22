---
name: vfx-edit
description: 用户要对部分或全部片段做风格/换人/换脸等编辑。Use when edit_prompt is replace person/face/style with reference images.
---

# VFX_EDIT

## 何时用（When to use）

- **触发条件**：用户要对视频做**按片段/按人物的编辑**：换人、换脸、换风格、参考图替换、加角色等。
- **典型说法**：把某人换成、参考图、换脸、换风格、赛博朋克风格、按图改 等；且常带 **video_path + image_paths + edit_prompt**。
- **与其它任务区分**：不是「找镜头」（SEARCH_AND_EXTRACT）；不是「续写/延长」（PLOT_EXTENSION）。

## 应调用的工具及调用顺序

**重要**：本任务必须有 video_path。若 video_path 为空，**必须先调用 getUserMessageTool** 询问用户。

本任务走 **full workflow**：

1. **video_understanding_tool** — 全片理解。
2. **image_understanding_tool** — 必须调用，得到 image_understanding_result；execution_prompt 中补充「参考图N（图中描述：***）」。
3. **Multi_model_understanding_tool** — 判断是否模糊；若 is_ambiguous=true，调用 **getUserMessageTool** 获取 clarified_user_info。
4. **get_video_metadata_tool** — 获取时长、fps。
5. **video_scene_logic_split_tool** — 逻辑分镜。
6. **detect_and_resegment_shots_tool** — 校验并按 time_length 切分。
7. **video_split_tool** — 实际切分，得到 segment_list。
8. **analyze_segment_relevance_tool** — 每段得到 editing_required、editing_prompt。
9. **手写 Plan**：
   - editing_required=true → **GENERATE**（填 execution_prompt、reference_resources）；**execution_prompt 要求写详细，补充参考图详细描述**
   - editing_required=false → **KEEP**
   - **time_range 必填**：格式 `{"start": "mm:ss", "end": "mm:ss"}`
10. 可选 **submit_video_execution_plan**。

## action_trigger by task_type

- editing_required=true → **GENERATE**；editing_required=false → **KEEP**

## Example

```json
{
  "task_metadata": { "task_type": "VFX_EDIT", "global_prompt": "Apply Cyberpunk style.", "requires_merge": true },
  "timeline": [
    { "segment_id": 1, "time_range": {"start": "00:00", "end": "00:05"}, "segment_path": "/tmp/v/seg_1.mp4", "action_trigger": "GENERATE", "execution_prompt": "Cyberpunk style, neon lights, rainy street.", "reference_resources": ["/tmp/ref_img.jpg"] },
    { "segment_id": 2, "time_range": {"start": "00:05", "end": "00:12"}, "segment_path": "/tmp/v/seg_2.mp4", "action_trigger": "KEEP", "execution_prompt": "No edit needed.", "reference_resources": [] }
  ]
}
```
