# 沙箱实验操作指南

## 一、环境准备

1. **在项目根目录执行**（不要进 `sandbox` 子目录）：
   ```bash
   cd /path/to/multi-shot-multi-object-long-video-edit
   ```

2. **环境变量**（Planner 与 UserSimulator 会调 LLM）：
   - `ARK_API_KEY`：Planner 与 UserSimulator 用的豆包/方舟 API Key。
   - 若用 `optimize_meta_skill`，还需其脚本里配置的 key（如 `KIMI_API_KEY` 等，见 `optimize_meta_skill.py`）。

3. **依赖**：已安装项目 `requirements.txt`（含 PyYAML、openai 等）。

---

## 二、准备任务与角色

1. **选一个 Role**（与 `sandbox/config/roles.yaml` 对应）：
   - `doc_rigorous`：严谨考据派纪录片（期望先 image_understanding 再生成）
   - `short_reversal`：短视频反转博主
   - `rhythm_editor`：节奏控剪辑师

2. **准备 task 列表**（每个 task 一句自然语言描述），任选一种方式：
   - **方式 A**：命令行多次 `--task`  
   - **方式 B**：写一个文本文件，每行一个 task，用 `--tasks_file` 指定

   示例 `sandbox/config/tasks.txt`：
   ```
   制作一段秦始皇视察长城的纪录片片段
   制作诸葛亮草船借箭的长故事视频
   做一个关于宋代夜市的短视频
   ```

3. **用户名**：用 `--username` 指定，同一用户下 context 与 meta-skill 隔离（如 `doc_rigorous` 或任意英文标识）。

---

## 三、运行完整实验（推荐）

**一次命令跑完：学习阶段 Phase1（多 task 各 RePlan 到满意）→ Phase2（更新 meta-skill）→ 泛化阶段（用新 meta-skill 再跑任务并对比，输出 MTTA）。**

```bash
# 多个 task，并把「同一任务前后两次 context」对比写入 JSON
python -m sandbox.run_episode_loop \
  --username doc_rigorous \
  --role_id doc_rigorous \
  --task "制作一段秦始皇视察长城的纪录片片段" \
  --task "制作诸葛亮草船借箭的长故事视频" \
  --comparison_output sandbox/output/comparison.json
```

或使用 task 文件：

```bash
# 先确保有 task 列表文件
echo '制作一段秦始皇视察长城的纪录片片段' > sandbox/config/tasks.txt
echo '制作诸葛亮草船借箭的长故事视频' >> sandbox/config/tasks.txt

python -m sandbox.run_episode_loop \
  --username doc_rigorous \
  --role_id doc_rigorous \
  --tasks_file sandbox/config/tasks.txt \
  --comparison_output sandbox/output/comparison.json
```

**常用参数：**

| 参数 | 说明 | 示例 |
|------|------|------|
| `--username` | 用户标识，决定 context/meta-skill 目录与文件 | `doc_rigorous` |
| `--role_id` | 虚拟用户角色，与 roles.yaml 一致 | `doc_rigorous` |
| `--task` | 任务描述，可多次 | `--task "任务1" --task "任务2"` |
| `--tasks_file` | 学习阶段：每行一个 task 的文件 | `sandbox/config/tasks.txt` |
| `--tasks_file_gen` | 泛化阶段：相似域新任务文件（不传则用同一批 task 重跑） | `sandbox/config/tasks_gen.txt` |
| `--max_rounds_per_task` | 单 task 最多 RePlan 几轮 | 默认 5 |
| `--comparison_output` | 对比结果 JSON（含 mtta_learning、mtta_generalization） | `sandbox/output/comparison.json` |
| `--no_optimizer` | 不跑 Phase2（不更新 meta-skill） | 仅测 Phase1 时用 |
| `--no_compare` | 不跑泛化阶段（不重跑、不对比） | 只做 Phase1+2 时用 |

---

## 四、只看 Phase1（不更新 meta-skill、不对比）

若只想看「多 task、各 RePlan 到满意」并写 context，不更新 meta-skill、不重跑对比：

```bash
python -m sandbox.run_episode_loop \
  --username doc_rigorous \
  --role_id doc_rigorous \
  --task "制作一段历史纪录片片段" \
  --no_optimizer \
  --no_compare
```

---

## 五、结果在哪里、怎么分析

1. **Context 文件（每个 task 一个 ctx，多轮在同一文件）**
   - 学习阶段：`workspace/<username>/context/task_<i>/phase1/ctx_*.json`
   - 泛化阶段（同批 task 重跑）：`workspace/<username>/context/task_<i>/phase3/ctx_*.json`
   - 泛化阶段（相似域新任务）：`workspace/<username>/context/task_<i>/phase2_gen/ctx_*.json`
   - 每个 ctx 里 `rounds` 数组即该 task 在该 phase 下所有 round 的 plan–feedback。

2. **Meta-skill 文件**
   - 用户初始/基线：`skills/SKILL_<username>.md` 或 `skills/meta-skill/SKILL_<username>.md`（视项目结构）
   - 更新后：`skills/SKILL_opt_<username>.md`（Phase2 生成）

3. **对比结果与三项评估维度**
   - **MTTA（交互摩擦力）**：对比 JSON 中的 `mtta_learning`、`mtta_generalization`，即学习阶段与泛化阶段平均修正轮次；期望泛化阶段显著降低。
   - 若传了 `--comparison_output`，JSON 包含：
     - `mtta_learning` / `mtta_generalization`
     - `per_task`（同批 task 时）：每个 task 的 phase1 vs phase3 路径、轮数、是否接受。
     - `per_task_learning` / `per_task_generalization`（使用 `--tasks_file_gen` 时）：学习期与泛化期每 task 的回合数、是否接受。

4. **看对比 JSON 示例**
   ```bash
   cat sandbox/output/comparison.json | head -80
   ```
   可关注：泛化阶段 MTTA 是否低于学习阶段、同一 task 在更新 meta-skill 后回合数是否减少，用于说明 meta-skill 是否学到该 Role 的流程偏好。

---

## 六、可选：五项指标与绘图

- **五项指标**：需要该用户在 `workspace/<username>/trace/` 下有 trace；若只有 context 无 trace，部分指标会从 plan 推断。
  - **Recall**：偏好工具召回。
  - **Violation Rate**：约束违反率。
  - **Meta-skill 长度**：文件字符数。
  - **Workflow Match Rate**：实际工具序列与 Role 的 `golden_tool_sequence` 的流程命中率（1 - 归一化编辑距离）。
  - **Auto Recall Rate**：Role 的 `preferred_params` 在 trace 的 tool 参数中的自动召回率。
  ```bash
  python -m sandbox.eval_metrics --username doc_rigorous --output sandbox/output/metrics.json
  ```

- **绘图**（需 matplotlib）：
  ```bash
  python -m sandbox.plot_metrics --username doc_rigorous --output sandbox/output/metrics_plot.png
  ```

---

## 七、最小可跑示例（单 task、不优化不对比）

```bash
cd /path/to/multi-shot-multi-object-long-video-edit
export ARK_API_KEY=your_key

python -m sandbox.run_episode_loop \
  --username test_user \
  --role_id doc_rigorous \
  --task "制作一段历史纪录片片段" \
  --no_optimizer \
  --no_compare \
  --max_rounds_per_task 3
```

跑完后可查看：`workspace/test_user/context/task_0/phase1/ctx_*.json`，内含该 task 多轮 plan–feedback。
