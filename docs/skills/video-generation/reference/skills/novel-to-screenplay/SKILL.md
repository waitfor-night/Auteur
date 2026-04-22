---
name: novel-to-screenplay
description: 短篇小说转剧本与分镜。根据小说正文或路径产出 CP1–CP5（角色、场景、片段、剧本、分镜），所有中间产物以路径交互。
---

# NOVEL_TO_SCREENPLAY

## 何时用（When to use）

- **触发条件**：用户提供短篇小说（几千字）或小说文件路径，希望转换为专业剧本或拍摄分镜。
- **典型说法**：把这篇小说转成剧本、根据小说做分镜、小说转分镜、短篇转剧本 等。
- **与其它任务区分**：LONG_VIDEO_CREATE 是已有剧本/分镜直接生成视频；本 skill 是从小说到剧本再到分镜的完整流水线，产出 CP1–CP5 文件后可再接 LONG_VIDEO_CREATE 或 SCRIPT_LENS。

## 本阶段 Plan 结构（无 timeline）

本阶段 **无 timeline**。在 `plan` 的 `task_metadata` 或自定义块中提供以下字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| novel_text | string | 与 novel_path 二选一 | 用户直接粘贴的小说正文（短篇）。若提供则 Actor 先调用 write_tool 落盘再执行后续。 |
| novel_path | string | 与 novel_text 二选一 | 小说文件路径（用户给路径或 Runner 已落盘时）。 |
| output_dir | string | 是 | 产出目录，所有 CP 文件写在此目录下；相对路径基于当前工作目录。 |
| title | string | 否 | 剧本标题，默认「未命名剧本」。 |
| storyboard_style | string | 否 | 分镜风格：cinematic / tv / short / animation，默认 cinematic。 |

## 产出结构说明（CP2–CP5）

- **CP2**：叙事**大段**（约 1500–3500 字、完整情节点），段数尽量少；**不要**按「15 秒」把原文切碎。段内在 CP3 再拆成多个约 15s 视频单元。
- **CP3**：**`dialogues`** 须覆盖片段内**全部对白**（逐条，`lines` 为数组）；**`video_units`** 每项约 **12–18s 成片**，内含 **`narrative_beats`**（画面 + 声画合一）。另有扁平 **`narrative_beats`** 供兼容。
- **CP5**：**每场景可有多个 panel**（每个 **video_unit** 一条），`duration=15`，`content` 含 **`【0-5s】【5-10s】【10-15s】`**，`video_unit_id` 对应 CP3。下游按 **每 panel 15s** 生成（`target_duration_s` 与 panel 列表对齐）。

## 工具调用顺序（execution_hint 模板）

Actor 执行本阶段时，**必须**按以下顺序调用工具：

1. **若 plan 中仅有 novel_text、无 novel_path**：先调用 `write_tool(path=output_dir/novel_input.txt, content=novel_text)`，得到 path；后续将 path 作为 novel_path 使用。
2. **若 plan 中已有 novel_path**（或步骤 1 已写入）：依次调用：
   - `novel_analyze_tool(novel_path, output_dir)` → 得到 cp1_characters_path、cp1_locations_path
   - `novel_split_clips_tool(novel_path, cp1_characters_path, cp1_locations_path, output_dir)` → 得到 cp2_clips_path
   - `novel_convert_clips_tool(cp2_clips_path, cp1_characters_path, cp1_locations_path, output_dir)` → 得到 cp3_clips_dir
   - `novel_merge_screenplay_tool(cp3_clips_dir, cp1_characters_path, cp1_locations_path, output_dir, title)` → 得到 cp2_full_screenplay_path、cp3_merged_path
   - `novel_script_to_storyboard_tool(cp3_merged_path, output_dir, title, storyboard_style)` → 得到 cp4_plan_path、cp5_storyboards_path
3. 最后调用 `set_stage_outputs_tool(stage_id, outputs_json)`，其中 outputs_json 至少包含以下键（供下游 reference_source 使用）：
   - cp1_characters_path
   - cp1_locations_path
   - cp2_clips_path
   - cp3_clips_dir
   - cp3_merged_path
   - cp4_plan_path
   - cp5_storyboards_path

各工具入参来源：上一步工具返回的路径键名 → 下一步对应参数；output_dir、title、storyboard_style 来自 plan。

## 路径与摘要说明

- 所有中间产物以**路径**交互，不在上下文中传递长文本。
- 各 novel_* 工具返回的 `summary` 字段仅作可读摘要，可不写入 set_stage_outputs；set_stage_outputs 以路径键为主。

## 与下游阶段组合

- 若用户需求为「根据小说生成视频」，可先本阶段产出 cp5_storyboards_path，再 LONG_VIDEO_CREATE：**每个 panel 对应约 15 秒视频**（与 cp5 中 `duration` 一致），勿再按 5 秒拆 panel。
