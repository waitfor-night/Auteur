---
name: dashboard
description: 启动 MoMo 看板（dashboard/app.py）。检查是否已在运行，未运行则后台启动，输出访问地址和当前 Episode/发布数据摘要。
---

# MoMo Dashboard Skill

## Skill 文件结构

```
docs/skills/dashboard/
├── SKILL.md
└── scripts/
    └── start.py    ← 检查 + 启动 + 数据摘要
```

## 前置条件

| 依赖 | 说明 |
|------|------|
| `momo` conda 环境 | 需安装 Flask（`pip install flask`） |
| `MOMO_USER` | 看板读取 `workspace/<username>/` 下的数据 |
| `dashboard/app.py` | 项目根目录下的 Flask 后端 |

## 启动命令

```bash
SKILL_DIR="$(python3 -c "import subprocess,sys; print(subprocess.check_output(['git','rev-parse','--show-toplevel']).decode().strip())")/docs/skills/dashboard"
python3 "$SKILL_DIR/scripts/start.py" --username zhaili --port 8080
```

或直接指定项目路径：

```bash
python3 /mnt/d/videorepo/Auteur/docs/skills/dashboard/scripts/start.py \
    --username zhaili \
    --port 8080
```

**参数说明：**

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--username` | 否 | `zhaili` | 读取 `workspace/<username>/` 数据 |
| `--port` | 否 | `8080` | Flask 监听端口 |

## 执行逻辑

1. **探测**：访问 `http://localhost:<port>/api/config`，若已有响应则跳过启动
2. **启动**：`nohup` 后台运行 `dashboard/app.py`，日志写入 `/tmp/momo_dashboard.log`，最多等待 12 秒就绪
3. **数据摘要**：拉取 `/api/episodes` 和 `/api/publish`，输出：
   - Episode 总计 / 已完成 / 待验证 / 已发布数量
   - 最近 3 条 Episode 的状态、标题、耗时、发布平台
   - 各平台累计帖子数、播放量、点赞数
4. **输出访问地址**：`http://localhost:<port>`

## 停止看板

```bash
pkill -f "dashboard/app.py"
```

## 查看日志

```bash
tail -f /tmp/momo_dashboard.log
```

## API 端点

| 路径 | 说明 |
|------|------|
| `GET /` | 前端页面 |
| `GET /api/config` | 当前用户名 |
| `GET /api/episodes` | 所有 Episode 列表（含发布状态） |
| `GET /api/episode/<ep_id>` | 单条 Episode 详情（tool calls + 阶段计划） |
| `GET /api/publish` | `publish_log_v2.json` 全量数据（含各平台历史指标） |

## 数据来源

| 文件 | 说明 |
|------|------|
| `workspace/<username>/trace/ep_*.json` | 每次视频生成的完整 tool call 记录 |
| `workspace/<username>/publish_log_v2.json` | 多平台发布记录及历史指标（由 `xhs_refresh_cron.py` 定时刷新） |
