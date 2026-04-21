# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MoMo** — Self-Evolving Personal Video Assistant via Dynamic Skill Generation. The system uses a Planner-Actor agent architecture to generate long-form videos through multi-round user interaction and adaptive meta-skill evolution.

## Environment Setup

### 1. Python 依赖

```bash
# 激活虚拟环境（Python 3.12）
source .venv/bin/activate        # 或直接用 .venv/bin/python

pip install -r requirements.txt  # 主依赖
pip install tikhub               # 多平台指标采集（TikHub SDK）
sudo apt install -y ffmpeg       # 视频裁剪系统依赖
```

### 2. 环境变量（`.env` 文件，项目根目录）

```bash
ARK_API_KEY=        # Doubao/Ark LLM API（必填）
TOS_ACCESS_KEY=     # ByteDance TOS 文件上传（必填）
TOS_SECRET_KEY=     # ByteDance TOS 文件上传（必填）
TIKHUB_API_TOKEN=   # TikHub 多平台数据采集 API Token（必填，用于刷新指标）
                    # 申请地址：https://tikhub.io
DEEPSEEK_API_KEY=   # 可选
KIMI_API_KEY=       # 可选
# VIDEO_EDIT_API / VIDEO_EDIT_WS 为视频编辑后端，纯生成任务不需要
```

`.env` 已在本机配置好，换机器时按上述格式重新填写。

### 3. social-auto-upload（sau CLI）

sau 是多平台视频发布自动化工具，用于抖音/B站/快手发布。

```bash
# 克隆仓库（首次，放在本项目同级目录）
cd ..
git clone https://github.com/dreammis/social-auto-upload.git
cd social-auto-upload

# 安装为可编辑包，sau 命令即可用
pip install -e .

# 各平台登录（首次，会弹出浏览器扫码），<account> 由用户自定义，如 myname-douyin
sau douyin login --account <account>-douyin
sau bilibili login --account <account>-bilibili
sau kuaishou login --account <account>-kuaishou

# Cookie 验证
sau douyin check --account <account>-douyin
sau bilibili check --account <account>-bilibili
sau kuaishou check --account <account>-kuaishou

# Cookie 自动存储在 social-auto-upload/cookies/ 目录下：
#   douyin_<account>-douyin.json
#   bilibili_<account>-bilibili.json
#   kuaishou_<account>-kuaishou.json
```

> `refresh_tikhub_all.py` 会自动从 sau_cli 包位置推断 cookie 目录，无需额外配置。
> 若自动推断失败，可在 `.env` 中设置 `SAU_COOKIE_DIR=/path/to/social-auto-upload/cookies`。

### 4. 平台 user_id 配置（`workspace/<username>/platform_config.json`）

用于 TikHub 自动补全各平台 post_id 和刷新指标，**首次配置一次**：

```json
{
  "douyin":   { "sec_user_id": "MS4wLj...（抖音分享链接中的 sec_uid）" },
  "bilibili": { "uid": "700365685（B站主页 URL 数字）" },
  "kuaishou": { "user_id": "3xbchqnk8vkfsxs（快手分享链接解析）" }
}
```

快手 user_id 通过分享链接解析：
```bash
python3 -c "
from tikhub import TikHub; import os
from dotenv import load_dotenv; load_dotenv()
client = TikHub(api_key=os.environ['TIKHUB_API_TOKEN'])
r = client.kuaishou_web.fetch_get_user_id(share_link='<快手视频分享链接>')
print(r.get('data', {}).get('user_id'))
"
```

小红书发布相关（可选，换机器时按实际路径配置）：
```bash
# XHS_IMAGES_DIR        宿主机上 xiaohongshu-mcp 挂载目录（默认：/tmp/xiaohongshu_images）
# XHS_MCP_CONTAINER_PATH 容器内路径前缀（默认：/app/images）
# XHS_MCP_URL           MCP 服务地址（默认：http://localhost:18060/mcp）
```

### 5. Claude Code Skill 注册

social-auto-upload 的 skill 整体复制到 `~/.claude/skills/`，在任意 Claude Code 会话中均可使用。

```bash
# social-auto-upload 克隆完成后执行一次
cp -r ../social-auto-upload/skills/* ~/.claude/skills/

# 验证
ls ~/.claude/skills/
```

注册后可用的 slash command：

| 命令 | 来源 | 功能 |
|------|------|------|
| `/video-pipeline` | 本项目 | 热点视频生成 + 多平台发布流水线 |
| `/fetch-hot-topics` | 本项目 | 抓取当前热点话题 |
| `/douyin-upload` | social-auto-upload | 抖音登录/上传 |
| `/bilibili-upload` | social-auto-upload | B站登录/上传 |
| `/kuaishou-upload` | social-auto-upload | 快手登录/上传 |
| `/xiaohongshu-upload` | social-auto-upload | 小红书登录/上传 |
| `/multi-platform-video-pipeline` | social-auto-upload | 多平台发布完整链路 |

## 小红书 MCP 环境配置

```bash
# ── 仅首次：拉取仓库并配置 Go 环境 ──────────────────────────
git clone https://github.com/xpzouying/xiaohongshu-mcp.git
go env -w GOPROXY=https://goproxy.cn,direct  # 国内必须，参考 https://go.dev/doc/install 安装 Go

# ── 仅首次：登录小红书（浏览器扫码/账密，cookies 自动保存）──
cd xiaohongshu-mcp && go run cmd/login/main.go
# 注意：登录后不要在其他网页端登录同一账号，否则会顶掉 MCP 登录状态

# ── 仅首次：注册 MCP 到 Claude Code ─────────────────────────
claude mcp add --transport http xiaohongshu-mcp http://localhost:18060/mcp
claude mcp list  # 验证是否添加成功

# ── 仅首次：绑定小红书账号 ───────────────────────────────────
python3 utils/xhs_publish.py set-account \
    --username <username> --xhs-account <小红书昵称> --xhs-user-id <user_id>

# ── 每次使用前：启动 MCP 服务 ────────────────────────────────
cd xiaohongshu-mcp && go run .
# 服务监听 http://localhost:18060/mcp，首次运行自动下载无头浏览器（约 150MB）
# 启动后 CC 即可调用 publish_with_video / get_feed_detail 完成发布与指标刷新
```

## Common Commands

```bash
# 运行视频助手（交互式）
python video_assistant.py

# 非交互式调用（推荐调试方式）
.venv/bin/python -c "
from video_assistant import VideoAssistant
assistant = VideoAssistant(
    output_dir='workspace/output/<task_name>',
    time_length=15,
    username='<username>',
    allow_interactive=False,
)
result = assistant.run(user_input='<用户指令>')
print(result)
"

# 运行 sandbox 三阶段实验流水线
python -m sandbox.run_episode_loop --username doc_rigorous --role_id doc_rigorous

# 优化 meta-skill 与用户 memory（新）
python -m learning.learning \
    --meta_skill skills/SKILL_doc_rigorous.md \
    --username doc_rigorous \
    --trace workspace/doc_rigorous/context \
    --engine kimi-k2-turbo-preview

# 仅优化 meta-skill（旧接口，仍可用）
python optimize_meta_skill.py --meta_skill skills/meta-skill/SKILL.md \
    --trace workspace/context --output skills/meta-skill/SKILL_opt.md

# 计算实验指标
python -m sandbox.eval_metrics --username doc_rigorous --output sandbox/output/metrics.json
```


## Architecture

### Planner-Actor Pattern

`video_assistant.py` 是主入口，orchestrates 多轮迭代：
1. **Planner**（`planner.py`）— 读取系统 prompt + 动态加载 skill，通过 `submit_multi_stage_plan` 生成结构化多阶段执行计划
2. **Actor**（`actor.py`）— 接收 plan，按阶段调用工具执行；工具调用序列会被 TraceRecorder 全量记录
3. 执行完成后，VLM 判断用户是否满意；不满意则携带历史记忆进入下一轮

两个 Agent 均使用 Agno 框架，后端为 Doubao（火山引擎 Ark）模型，OpenAI-compatible API。

### Skill System

Skill 是 `skills/<category>/` 下的 Markdown 文件，定义任务特定的执行工作流。运行时 `skills/skill_loader.py` 从用户输入推断任务类型，将对应 skill 注入 Planner 的系统 prompt。

**Skill 分类：**
- `long-video-create` — 从剧本/文案生成长视频
- `plot-extension` — 视频续写/延长，或短时长纯生成
- `script-lens` — 分镜脚本生成
- `vfx-edit` — 视觉特效编辑
- `search-and-extract` — 搜索与内容提取
- `summary` — 视频摘要
- `novel-to-screenplay` — 小说转剧本
- `video-generate` — 通用视频生成
- `visual-prompt-opt` — 视觉 prompt 优化

**用户专属 skill**（`skills/SKILL_<username>.md`）：由 learning 模块优化生成，覆盖默认 meta-skill。

### Tool Layers

- `tools/plannerTools.py` — Planner 可用工具的聚合入口
- `tools/actorTools.py` — Actor 可用工具的聚合入口
- `tools/skillTools/` — `load_skill_tool`（动态加载 skill，自动写入 RunContext）
- `tools/submitPlanTools/` — `submit_multi_stage_plan`（提交执行计划）
- `tools/generationTools/` — `video_generate_tool`、`batch_video_generate_tool`（调用 Seedance API）
- `tools/logicSplitTools/` — 视频逻辑分镜
- `tools/mmUnderstandingTools/` — 多模态视频/图像理解
- `tools/ioTools/` — 视频合并、文件 IO
- `tools/physicsEditTools/` — 物理编辑工具
- `tools/searchTools/` — 搜索工具
- `tools/userTools/` — 用户反馈工具

### Context & Trace Persistence

- `utils/context_recorder.py`（`RunContext`）— 多轮迭代短期记忆；每轮记录 plan、skill_loaded、planner_tools、actor_tools、feedback、satisfied；写入 `workspace/<username>/context/ctx_*.json`
- `utils/trace_recorder.py`（`TraceRecorder`）— 每次 tool 调用的完整埋点（入参、返回、耗时、状态）；写入 `workspace/<username>/trace/ep_*.json`
- `utils/trajectory_io.py` — trace 文件读取接口，供 learning 模块消费

**记录文件路径规律：**
```
workspace/<username>/context/ctx_<timestamp>_<hash>.json   # RunContext
workspace/<username>/trace/ep_<timestamp>_<hash>.json      # TraceRecorder
```

### Learning & Meta-Skill Optimization

`learning/` 模块实现 TextGrad 优化循环：

- `learning/learning.py` — **主入口**，同时优化 meta-skill 和用户 memory
- `learning/optimize_meta_skill.py` — 仅优化 meta-skill
- `learning/optimization/trajectory_loss.py` — 对比实际 tool 调用序列与角色期望序列，计算 loss
- `learning/optimization/memory_loss.py` — 基于用户反馈计算 memory loss
- `learning/tgd_engine.py` — TextGrad engine 封装
- `learning/update_memory.py` — 更新用户 memory 文件

根目录 `optimize_meta_skill.py` 是旧版单独优化入口，仍可用。

### Sandbox / Experiment Framework

`sandbox/run_episode_loop.py` 运行三阶段实验：
1. **Learning phase** — 固定 meta-skill 重复任务，直到用户模拟器满意
2. **Optimization phase** — `learning/learning.py` 用 TextGrad 优化 meta-skill 和 memory
3. **Generalization phase** — 用优化后的 skill 重跑，衡量改进

`sandbox/user_simulator.py` 基于 `sandbox/config/roles.yaml` 中的 5 个角色 persona 模拟用户反馈：
`doc_rigorous`、`short_reversal`、`rhythm_editor`、`storyboard_first`、`quality_inspector`

### utils/ 目录（当前有效文件）

| 文件 | 用途 |
|------|------|
| `context_recorder.py` | RunContext，多轮迭代记忆 |
| `trace_recorder.py` | TraceRecorder，工具调用埋点 |
| `trajectory_io.py` | trace 文件读取 |
| `video_utils.py` | ffprobe 视频元数据 |
| `auto_video_splite.py` | 视频自动分割 |
| `story_gen_tools.py` | 剧本实体提取、图片生成 |
| `tools_implement.py` | 旧工作流遗留（`long_video_edit_workflow.py` 仍在使用） |
| `seedance_edit.py` | Seedance 编辑 API 封装（含 `__main__` 手动测试入口） |
| `xhs_log_io.py` | publish_log 读写接口，记录每条小红书帖子及历史指标 |
| `xhs_publish.py` | 小红书发布全流程 CLI：set-account / prepare / record / auto-publish / refresh / list |
| `xhs_refresh_cron.py` | 定时刷新调度器：由系统 crontab 每 30 分钟触发，依次调用 `pending-refresh` 获取待刷新列表、HTTP 调 MCP `get_feed_detail` 拉取详情、`refresh-all` 写入 publish_log |

## Notes
## 小红书发布自动化流程（CC 执行）

用户说"发布到小红书"或"帮我发布"时，CC 按以下步骤全自动完成，**无需用户逐步确认**。
账号信息自动从 `workspace/<username>/xhs_config.json` 读取（首次使用前需执行 `set-account`）。

**Step 0（首次）— 绑定账号**
```bash
python3 utils/xhs_publish.py set-account \
    --username <username> \
    --xhs-account <小红书昵称> \
    --xhs-user-id <user_id>
```

**Step 1 — 生成视频**

```python
import json
from video_assistant import VideoAssistant
a = VideoAssistant(output_dir='workspace/output/<task>', username='<username>', allow_interactive=False)
result = a.run(user_input='<用户指令>')
# result 已包含 xhs_title / xhs_tags / trace_id / trace_path / result_video
with open('/tmp/va_result.json', 'w') as f:
    json.dump(result, f, ensure_ascii=False)
```

**Step 2 — 直接发布（HTTP API，无需 Docker/MCP 发布工具）**

```bash
python3 utils/xhs_publish.py auto-publish \
    --username <username> \
    --result-json-str "$(cat /tmp/va_result.json)"
# 直接调用 xiaohongshu-mcp HTTP API 发布，自动轮询 feed_id
# 输出 JSON：trace_id / trace_path / result_video / post_id / xsec_token / title / tags
```

> 依赖：`xiaohongshu-mcp` 服务已在 `:18060` 运行（`nohup ./start.sh > /tmp/xhs-mcp.log 2>&1 &`）
> 可通过环境变量 `XHS_MCP_BASE_URL` 覆盖默认地址（默认 `http://localhost:18060/api/v1`）

**Step 3 — 写入 publish_log**

```bash
python3 utils/xhs_publish.py record \
    --username <username> \
    --trace-id <Step2.trace_id> \
    --trace-path <Step2.trace_path> \
    --post-id <Step2.post_id> \
    --xsec-token <Step2.xsec_token> \
    --title "<Step2.title>" \
    --tags <Step2.tags...>
# xhs_account / xhs_user_id 自动从 xhs_config.json 读取（监控用，可选）
```

> **定时刷新**：系统 crontab 每 30 分钟自动执行 `utils/xhs_refresh_cron.py`，直接 HTTP 调用 MCP 刷新所有 published 帖子指标，日志写入 `workspace/<username>/xhs_refresh_cron.log`。

**Step 4 — 自动补充缺失的 post_id（发布时未立即返回时）**

```bash
# CC 查询哪些记录缺少 post_id
python3 utils/xhs_publish.py find-missing --username <username>
# 输出 JSON：[{"trace_id": "ep_xxx", "title": "...", "publish_time": "..."}]
```

CC 拿到列表后，调 MCP `user_profile`（user_id=`6415565300000000120118f4`，xsec_token 从任意已知帖子获取）拿到账号全部帖子列表，按 `displayTitle` ≈ `title` 匹配，找到匹配帖子后直接调 `update-ids` 写入，**无需询问用户**（不用 `search_feeds`，避免超时）：

```bash
python3 utils/xhs_publish.py update-ids \
    --username <username> \
    --trace-id <trace_id> \
    --post-id <post_id> \
    --xsec-token <xsec_token>
```
