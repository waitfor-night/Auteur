# 沙箱配置：5 个虚拟形象与任务文件

## 角色与任务文件

| role_id (username) | 任务文件 | 创作逻辑简述 |
|--------------------|----------|--------------|
| doc_rigorous | `config/tasks/doc_rigorous.txt` | 考据优先：先 image 理解再生成，禁止未检查就出图 |
| short_reversal | `config/tasks/short_reversal.txt` | 结尾后处理、merge 在最后 |
| rhythm_editor | `config/tasks/rhythm_editor.txt` | 分段合理、GENERATE 条数与 duration 匹配 |
| storyboard_first | `config/tasks/storyboard_first.txt` | 先剧本/分镜、extract_script_entities，再生成 |
| quality_inspector | `config/tasks/quality_inspector.txt` | 理解→生成→再理解，每步可检查 |

每个任务文件 10 个任务，覆盖：剪辑、生成、理解、以及需多 sub-skill 的复杂任务。  
任务文件每行一个任务；以 `#` 开头的行会在加载时被忽略。

## 跑优化前后对比实验（单角色）

```bash
# 从项目根目录执行，以 doc_rigorous 为例
python -m sandbox.run_episode_loop \
  --username storyboard_first \
  --tasks_file sandbox/config/tasks/storyboard_first.txt \
  --role_id storyboard_first
```

依次跑 5 个角色可写脚本循环调用，或分别执行上述命令并替换 `doc_rigorous` 为 `short_reversal`、`rhythm_editor`、`storyboard_first`、`quality_inspector`。
