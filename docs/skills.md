# Skill 目录说明

## 两类 Skill 的区别

| 目录 | 用途 | 消费者 |
|------|------|--------|
| **`docs/skills/`**（本目录） | 通用 Skill，面向 Agent 客户端（Claude Code、OpenClaw、Codex 等），以 slash command 形式调用，描述端到端工作流（如抓取热点、视频生成流水线、多平台发布） | 任意 Agent 客户端 |
| **`skills/`** | MoMo Agent 内部 Skill，仅供 MoMo Planner 在运行时加载，通过 `load_skill_tool` / `skill_loader.py` 注入 Planner prompt，控制视频生成任务的编排逻辑 | MoMo Planner（内部） |

`docs/skills/` 中的每个子目录对应一个 slash command skill，包含 `skill.md`（描述 + 命令 + 参考实现）及可选的 `reference/`（底层实现代码副本，用于分发）。

`skills/` 中的每个子目录包含 `SKILL.md`（YAML frontmatter + 正文），格式与 Cursor skill 一致，由 `skill_loader.py` 按任务类型动态选择并注入 Planner 系统 prompt。

---

# MoMo 内部 Planner Skill（`skills/` 目录）

按「任务类型」存放 skill，通过**任务判断**在运行时导入对应 skill 注入 Planner prompt。

## 架构说明

### Plan Schema 位置

**Video Execution Plan 的 JSON Schema 定义**已移至 `prompts.py` 的 `PLANNER_AGENT_PROMPT_CORE` 中，不再从 skill 文件加载。这样做的好处是：
- Schema 作为核心规范，始终存在于 Planner prompt 中
- 减少运行时加载的复杂性

### Skill 目录职责

所有 skill 统一管理在 `ALL_SKILLS` 中，通过 `load_skill_tool` 加载：

- **meta-skill/** — 任务编排逻辑与用户偏好（默认加载，不在 ALL_SKILLS 中）
- **search-and-extract/** — `SEARCH_AND_EXTRACT`：找/筛选/提取镜头再合并
- **vfx-edit/** — `VFX_EDIT`：风格/换人/换脸等编辑
- **plot-extension/** — `PLOT_EXTENSION`：续写/延长
- **summary-only/** — `SUMMARY_ONLY`：仅摘要/字幕
- **long_video_create/** — `LONG_VIDEO_CREATE`：分镜规划与视频生成
- **script-lens/** — `SCRIPT_LENS`：剧本解析与参考图生成

每个 skill 是一个子目录，内含 `SKILL.md`（YAML frontmatter + 正文），与 Cursor skill 格式一致。

## 任务判断与导入

- **skill_loader.py**：从本目录读取 `SKILL.md`，按任务类型选择要加载的 skill。
  - `infer_task_type(edit_prompt)`：根据用户指令推断任务类型（关键词启发式）。
  - `build_planner_final_section(task_type=None)`：组装 skill 部分；
    - **默认加载 meta-skill**（任务编排逻辑与用户偏好）
    - `task_type` 为 None 时加载 meta-skill + 全部 5 个 task skill
    - `task_type` 为 `"__NO_TASK_SKILL__"` 时只加载 meta-skill，不加载 task skill
    - 指定具体 `task_type` 时加载 meta-skill + 该任务的 skill
- **prompts.py**：
  - `PLANNER_AGENT_PROMPT_CORE`：包含核心 prompt + Plan Schema 定义
  - `PLANNER_AGENT_PROMPT`：核心 + `build_planner_final_section("__NO_TASK_SKILL__")`（加载 meta-skill，不加载 task skill）
  - `get_planner_agent_prompt(edit_prompt)`：按 `edit_prompt` 做任务判断后注入对应 task skill

## 使用方式

- **默认 agent**：使用 `PLANNER_AGENT_PROMPT`，包含核心 prompt、Plan Schema 和 meta-skill，不包含具体 skill（通过 load_skill_tool 运行时加载）。
- **按任务导入**：调用 `get_planner_agent_prompt(InitUserMessage="向前延长12秒科幻短片")`，则加载 meta-skill + `plot-extension` 的 skill。
- **显式指定 skill 类型**：调用 `get_planner_agent_prompt(skill_type="LONG_VIDEO_CREATE")`，直接加载 meta-skill + 指定的 skill。

## 多次 Skill 调用（分阶段执行）

`load_skill_tool` 支持在一次 plan 执行过程中多次调用，加载不同的 skill：

```python
# 阶段1：加载 ScriptLens，执行剧本解析和参考图生成
load_skill_tool(skill_type="SCRIPT_LENS")
# → 执行 extract_script_entities_tool、generate_image_tool
# → 产出：角色/场景的参考图

# 阶段2：加载 LongVideoCreate，执行分镜规划
load_skill_tool(skill_type="LONG_VIDEO_CREATE")
# → 执行分镜规划、生成 timeline
# → 产出：最终的 Video Execution Plan
```

支持的 skill_type（统一管理在 ALL_SKILLS 中）：
- SEARCH_AND_EXTRACT: 找/筛选/提取镜头
- VFX_EDIT: 风格/换人/换脸等编辑
- PLOT_EXTENSION: 续写/延长
- SUMMARY_ONLY: 仅摘要/字幕
- LONG_VIDEO_CREATE: 分镜规划与视频生成
- SCRIPT_LENS: 剧本解析与参考图生成
