---
name: meta-skill
description: 定义任务编排逻辑（如何调用不同 skill 完成任务）。
---

# Meta Skill - 任务编排

## 任务编排逻辑

本 skill 定义了如何根据用户需求编排调用不同的 sub skill 来完成视频处理任务。

### 任务类型识别与 Skill 选择

根据用户提供的信息，识别任务类型并加载对应的 skill：

| 任务类型 | 对应 Skill | 触发关键词 | 典型场景 |
|---------|-----------|-----------|---------|
| SEARCH_AND_EXTRACT | search-and-extract | 找、筛选、提取、find all | 从视频中找出特定镜头并合并 |
| VFX_EDIT | vfx-edit | 换人、换脸、换风格、参考图 | 对视频片段进行视觉编辑 |
| PLOT_EXTENSION | plot-extension | 续写、延长、生成短片 | 续写或生成视频内容 |
| SUMMARY | summary | 摘要、字幕、caption | 仅生成文字描述 |
| LONG_VIDEO_CREATE | long_video_create | 剧本、脚本、故事 | 根据剧本生成长视频 |

### 编排流程

1. **分析用户意图**：从 InitUserMessage 识别所需的 skill
2. **调用 load_skill_tool**：加载对应的 skill 指导
3. **按 skill 指导执行工具调用**：每个 skill 定义了具体的工具调用顺序
4. **输出 Video Execution Plan**：最终输出符合 schema 的 Plan JSON

### 多次 Skill 调用（分阶段执行）

**`load_skill_tool` 支持在一次 plan 执行过程中多次调用**，实现分阶段加载不同 skill：

```
典型场景：剧本 → 长视频生成

调用流程：
1. load_skill_tool(skill_type="SCRIPT_LENS")  
   → 获取 ScriptLens 指导
   → 执行：extract_script_entities_tool → generate_image_tool
   → 产出：角色/场景的参考图

2. load_skill_tool(skill_type="LONG_VIDEO_CREATE")
   → 获取 LongVideoCreate 指导
   → 执行：分镜规划 → batch_video_generate_tool → merge_video_tool
   → 产出：最终的 Video Execution Plan
```

支持的 skill 类型（统一管理）：
- SEARCH_AND_EXTRACT: 找/筛选/提取镜头
- VFX_EDIT: 风格/换人/换脸等编辑
- PLOT_EXTENSION: 续写/延长
- SUMMARY: 仅摘要/字幕
- LONG_VIDEO_CREATE: 分镜规划与视频生成
- SCRIPT_LENS: 剧本解析与参考图生成（剧本解析与参考图生成）

### 多任务组合

当用户需求涉及多个任务类型时（较少见），可以：
- 先完成主任务，再处理附加需求
- 或将复杂需求拆分为多个独立的 Plan
- **或使用多次 skill 调用**：分阶段执行不同的 skill

## 任务 Skill 调用示例



### 示例 ：LONG_VIDEO_CREATE 任务编排（多次 Skill 调用）

```
用户输入：根据以下剧本生成一个视频...

编排流程（分阶段执行）：

【阶段1：剧本解析与参考图生成】
1. 调用 load_skill_tool(skill_type="SCRIPT_LENS")
2. 按 ScriptLens skill 指导执行：
   - extract_script_entities_tool → 抽取角色和场景
   - generate_image_tool × N → 为角色/场景生成参考图
3. 中间产出：角色/场景的参考图 image_paths

【阶段2：分镜规划与视频生成】
4. 调用 load_skill_tool(skill_type="LONG_VIDEO_CREATE")
5. 按 LongVideoCreate skill 指导执行：
   - 分析剧本连续性 → 判断各段之间的依赖关系
   - 生成 timeline 分镜
6. 输出 Video Execution Plan（多段 GENERATE，含连续性标记）
```

