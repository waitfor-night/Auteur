# Sandbox 实验框架

Sandbox 采用**仅 Planner 模式**，记录 Plan–Feedback；每个用户拥有独立的 `workspace/<username>/context/` 与 meta-skill 文件。

## 三阶段流程

| 阶段 | 说明 |
|------|------|
| **Phase 1 学习** | 每 task 一个 ctx 文件，Planner → Plan → UserSimulator 反馈，不满意则 RePlan，直到满意。所有 round 记录在同一 `ctx_*.json` 的 `rounds` 数组。目录：`workspace/<username>/context/task_<i>/phase1/` |
| **Phase 2 优化** | 全部 task 满意后，用 Phase1 的 context 更新 meta-skill 一次（`SKILL_opt_<username>.md`） |
| **Phase 3 泛化** | 用更新后的 meta-skill 重跑任务，衡量 MTTA（平均修正轮次）是否下降。若传 `--tasks_file_gen`（相似域新任务），写入 `task_<i>/phase2_gen/`；否则用同批 task 重跑，写入 `task_<i>/phase3/` |

---

## 环境准备

从**项目根目录**执行（不要进 `sandbox` 子目录）：

```bash
cd /path/to/multi-shot-multi-object-long-video-edit
source .venv/bin/activate
export ARK_API_KEY=your_key   # Planner 与 UserSimulator 均需 Doubao/Ark
```

依赖已在 `requirements.txt` 中（含 PyYAML、openai 等）。若需 Phase 2 meta-skill 优化，还需 `KIMI_API_KEY`（见 `learning/learning.py`）。

---

## 角色与任务文件

`sandbox/config/roles.yaml` 定义 5 个虚拟用户角色：

| role_id | 任务文件 | 创作偏好 |
|---------|----------|----------|
| `doc_rigorous` | `config/tasks/doc_rigorous.txt` | 考据优先：先 image_understanding 再生成，禁止未检查就出图 |
| `short_reversal` | `config/tasks/short_reversal.txt` | 结尾后处理，merge 在最后 |
| `rhythm_editor` | `config/tasks/rhythm_editor.txt` | 分段合理，GENERATE 条数与 duration 匹配 |
| `storyboard_first` | `config/tasks/storyboard_first.txt` | 先剧本/分镜（`extract_script_entities`），再生成 |
| `quality_inspector` | `config/tasks/quality_inspector.txt` | 理解 → 生成 → 再理解，每步可检查 |

每个任务文件 10 个任务，覆盖剪辑、生成、理解及需多 sub-skill 的复杂任务。以 `#` 开头的行加载时忽略。

每个 Role 的 YAML 可包含评估字段：
- `golden_tool_sequence`：黄金工具顺序，用于 Workflow Match Rate
- `preferred_params`：隐式参数偏好，用于 Auto Recall Rate

---

## 运行实验

### 完整三阶段（推荐）

```bash
# 内联指定 task
python -m sandbox.run_episode_loop \
  --username doc_rigorous \
  --role_id doc_rigorous \
  --task "制作一段秦始皇视察长城的纪录片片段" \
  --task "制作诸葛亮草船借箭的长故事视频" \
  --comparison_output sandbox/output/comparison.json

# 从任务文件读取
python -m sandbox.run_episode_loop \
  --username doc_rigorous \
  --role_id doc_rigorous \
  --tasks_file sandbox/config/tasks/doc_rigorous.txt \
  --comparison_output sandbox/output/comparison.json

# 泛化阶段使用相似域新任务（写入 phase2_gen）
python -m sandbox.run_episode_loop \
  --username doc_rigorous \
  --role_id doc_rigorous \
  --tasks_file sandbox/config/tasks/doc_rigorous.txt \
  --tasks_file_gen sandbox/config/tasks/doc_rigorous_gen.txt \
  --comparison_output sandbox/output/comparison.json
```

### 仅 Phase 1（不优化、不对比）

```bash
python -m sandbox.run_episode_loop \
  --username test_user \
  --role_id doc_rigorous \
  --task "制作一段历史纪录片片段" \
  --no_optimizer \
  --no_compare \
  --max_rounds_per_task 3
```

跑完后查看：`workspace/test_user/context/task_0/phase1/ctx_*.json`，内含该 task 多轮 plan–feedback。

### 所有角色批量跑

```bash
for role in doc_rigorous short_reversal rhythm_editor storyboard_first quality_inspector; do
  python -m sandbox.run_episode_loop \
    --username $role \
    --role_id $role \
    --tasks_file sandbox/config/tasks/${role}.txt \
    --comparison_output sandbox/output/comparison_${role}.json
done
```

### 参数速查

| 参数 | 说明 | 默认 |
|------|------|------|
| `--username` | 用户标识，决定 context/meta-skill 路径 | 必填 |
| `--role_id` | 虚拟用户角色（与 roles.yaml 一致） | 必填 |
| `--task` | 任务描述，可多次传入 | — |
| `--tasks_file` | 学习阶段任务文件（每行一个 task） | — |
| `--tasks_file_gen` | 泛化阶段相似域新任务文件 | 不传则重跑学习 task |
| `--max_rounds_per_task` | 单 task 最多 RePlan 轮次 | 5 |
| `--comparison_output` | 对比结果 JSON 输出路径 | — |
| `--no_optimizer` | 跳过 Phase 2（不更新 meta-skill） | — |
| `--no_compare` | 跳过泛化阶段 | — |

---

## 结果与分析

### 输出文件

| 路径 | 内容 |
|------|------|
| `workspace/<username>/context/task_<i>/phase1/ctx_*.json` | 学习阶段各 task 的 plan–feedback 轮次 |
| `workspace/<username>/context/task_<i>/phase3/ctx_*.json` | 泛化阶段重跑（同批 task） |
| `workspace/<username>/context/task_<i>/phase2_gen/ctx_*.json` | 泛化阶段新任务（`--tasks_file_gen`） |
| `skills/SKILL_<username>.md` | 初始 meta-skill |
| `skills/SKILL_opt_<username>.md` | Phase 2 更新后的 meta-skill |
| `comparison_output.json` | MTTA 对比 + 每 task 回合数 |

### 解读对比结果

```bash
cat sandbox/output/comparison.json | head -80
```

关注：
- `mtta_learning` vs `mtta_generalization`：泛化阶段平均修正轮次应显著低于学习阶段
- `per_task`（同批 task）：每个 task 在 phase1 vs phase3 的回合数是否减少
- `per_task_learning` / `per_task_generalization`（使用 `--tasks_file_gen` 时）

---

## 指标计算与绘图

### 计算五项指标

```bash
python -m sandbox.eval_metrics --username doc_rigorous --output sandbox/output/metrics.json
```

需要 `workspace/<username>/trace/` 下有 trace（仅有 context 时部分指标从 plan 推断）。

| 指标 | 说明 |
|------|------|
| **Recall** | 偏好工具在序列中的召回率 |
| **Violation Rate** | 约束违反率 |
| **MetaSkillLength** | meta-skill 文件字符数 |
| **Workflow Match Rate** | 实际工具序列与 `golden_tool_sequence` 的流程命中率（1 − 归一化编辑距离） |
| **Auto Recall Rate** | `preferred_params` 在 trace 中的自动召回率 |

### 绘图

```bash
python -m sandbox.plot_metrics --username doc_rigorous --output sandbox/output/metrics_plot.png
# 或基于已有 JSON
python -m sandbox.plot_metrics --input sandbox/output/metrics.json --output sandbox/output/metrics_plot.png
```

---

## Meta-skill 单独优化

如需脱离 sandbox 流程独立优化 meta-skill（也可对接 learning 模块）：

```bash
# 新接口：同时优化 meta-skill + 用户 memory
python -m learning.learning \
    --meta_skill skills/SKILL_doc_rigorous.md \
    --username doc_rigorous \
    --trace workspace/doc_rigorous/context \
    --engine kimi-k2-turbo-preview

# 旧接口：仅优化 meta-skill（仍可用）
python optimize_meta_skill.py \
    --meta_skill skills/meta-skill/SKILL.md \
    --trace workspace/context \
    --output skills/meta-skill/SKILL_opt.md
```
