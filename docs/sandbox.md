# Sandbox：仅 Planner + Plan–Feedback + 按用户隔离

实验采用**仅 Planner 模式**，记录 **Plan–Feedback**；每个用户拥有独立的 **workspace/\<username\>/context/** 与 **meta-skill 文件**。

**两阶段流程：**
- **学习阶段（Phase1）**：一 task 一个 ctx 文件；在「交互一个 task 直到满意」的过程中，所有 round 的 Plan–Feedback 都记录在**同一个** `ctx_*.json` 里（`rounds` 数组逐轮追加）。目录：`workspace/<username>/context/task_<i>/phase1/`。单 task：Planner → Plan → UserSimulator 反馈 → 若不满意则 **RePlan**（不更新 meta-skill），直到满意。
- **Phase2**：全部 task 满意后，**更新 meta-skill 一次**（读 phase1 的 context）。
- **泛化阶段**：用已更新的 meta-skill 再跑任务。若提供 **`--tasks_file_gen`**（相似域新任务文件），则跑该文件中的任务，写入 `task_<i>/phase2_gen/`；若不提供，则用学习阶段**同一批 task** 重跑，写入 `task_<i>/phase3/`。对比输出含 **MTTA**（学习阶段与泛化阶段平均修正轮次）及每 task 的回合数、是否接受。

## 用法

### 1. 多 task：RePlan 直到满意 → 更新 meta-skill → 重跑并对比

```bash
# 多个 task（全部满意后再更新 meta-skill，然后重跑并对比）
python -m sandbox.run_episode_loop --username doc_rigorous \
  --task "制作一段秦始皇视察长城的纪录片片段" \
  --task "制作诸葛亮草船借箭的长故事视频" \
  --role_id doc_rigorous

# 从文件读 task 列表（每行一个）
python -m sandbox.run_episode_loop --username doc_rigorous --tasks_file sandbox/config/tasks.txt

# 不更新 meta-skill、不跑泛化阶段
python -m sandbox.run_episode_loop --username user1 --task "根据剧本生成30秒故事" --no_optimizer --no_compare

# 泛化阶段使用相似域新任务（写入 phase2_gen）
python -m sandbox.run_episode_loop --username doc_rigorous --tasks_file sandbox/config/tasks/doc_rigorous.txt \
  --tasks_file_gen sandbox/config/tasks/doc_rigorous_gen.txt --comparison_output sandbox/comparison.json
```

- `--username`：用户名，context 写入 `workspace/<username>/context/`，meta-skill 使用 `SKILL_<username>.md` / `SKILL_opt_<username>.md`。
- `--task`：可多次传入；与 `--tasks_file`（每行一个 task）二选一。
- `--no_optimizer`：不调用 optimize_meta_skill（Phase2）。
- `--no_compare`：不跑泛化阶段（不重跑 task、不对比）。
- `--comparison_output <path>`：对比结果（含 **mtta_learning**、**mtta_generalization** 及每 task 回合数）写入该 JSON。
- `--tasks_file_gen <path>`：泛化阶段任务文件（每行一个相似域新任务）；不传则用学习阶段同一批 task 重跑（写入 phase3）。传了则写入 `task_<i>/phase2_gen/`。

### 2. 指标计算

```bash
python -m sandbox.eval_metrics --username doc_rigorous --output sandbox/metrics.json
```

从 `workspace/<username>/trace/`（或 context）与对应用户 meta-skill、roles 配置计算五项指标，输出 JSON：
- **Recall**：偏好工具在序列中的召回。
- **Violation Rate**：约束违反率。
- **MetaSkillLength**：meta-skill 文件长度。
- **Workflow Match Rate**：实际工具序列与黄金序列（`golden_tool_sequence`）的流程命中率（1 - 归一化编辑距离）。
- **Auto Recall Rate**：偏好参数（`preferred_params`）在 trace 中的自动召回率。

### 3. 绘图

```bash
python -m sandbox.plot_metrics --username doc_rigorous --output sandbox/metrics_plot.png
# 或使用已有 JSON
python -m sandbox.plot_metrics --input sandbox/metrics.json --output sandbox/metrics_plot.png
```

## 配置

- **Role 定义**：`sandbox/config/roles.yaml`。每个 Role 含 `expected_tasks`、`expected_tool_order`、`preferred_tools`、`constraint_rules`；评估用可选字段 **`golden_tool_sequence`**（黄金工具顺序，用于工作流命中率）、**`preferred_params`**（隐式参数偏好，用于参数对齐度）。
- **按用户隔离**：Context 通过 `workspace/<username>/` 实现；Meta-skill 通过不同文件名（`SKILL_<username>.md`、`SKILL_opt_<username>.md`）实现。
