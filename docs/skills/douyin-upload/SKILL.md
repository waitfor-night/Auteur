---
name: douyin-upload
description: 当 agent 需要将视频发布到抖音时使用这个 skill。通过 sau CLI 调用 Playwright 自动化上传，内置重试逻辑。抖音必须使用有头模式 + 系统代理。
---

# 抖音上传 Skill

## Skill 文件结构

```
.claude/skills/douyin-upload/
├── SKILL.md
└── scripts/
    └── upload.py    ← 主入口，自动重试
```

## 前置条件

1. `sau` CLI 已安装（`pip install -e /path/to/social-auto-upload`）
2. Cookie 有效：`social-auto-upload/cookies/douyin_<account>.json`
   - 验证：`sau douyin check --account <account>`
   - 过期时重新登录：`DISPLAY=:0 sau douyin login --account <account> --headed`
3. 系统代理运行在 `http://127.0.0.1:7897`（抖音创作者平台需要）
4. WSL 环境需要 `DISPLAY=:0`

## 上传命令

```bash
SKILL_DIR="$HOME/.claude/skills/douyin-upload"
DISPLAY=:0 python3 "$SKILL_DIR/scripts/upload.py" \
  --account  "<your-account>-douyin" \
  --file     "<视频文件路径>" \
  --title    "<视频标题>" \
  --tags     "tag1,tag2,tag3"
```

**参数说明：**

| 参数 | 必填 | 说明 |
|------|------|------|
| `--account` | 是 | sau 账号名，如 `<your-account>-douyin` |
| `--file` | 是 | 视频文件路径 |
| `--title` | 是 | 视频标题 |
| `--tags` | 是 | 逗号分隔的标签 |
| `--desc` | 否 | 可选描述文字 |
| `--schedule` | 否 | 定时发布，格式 `YYYY-MM-DD HH:MM` |
| `--max-retries` | 否 | 最大重试次数（默认 3） |

## 注意事项

- **必须有头模式**：抖音 Playwright 在无头模式下无法访问创作者平台（代理不生效）
- **代理依赖**：`http://127.0.0.1:7897` 必须在运行，否则页面无法加载
- **post_id 延迟**：上传成功后 TikHub 约 2 小时才完成索引，期间 backfill 返回空属正常

## 发布后验证

```python
from tikhub import TikHub
import os
from dotenv import load_dotenv
load_dotenv()

client = TikHub(api_key=os.environ['TIKHUB_API_TOKEN'])
# sec_user_id 从 workspace/<username>/platform_config.json 读取
sec_user_id = "<sec_user_id>"
r = client.douyin_web.fetch_user_post_v2(secUid=sec_user_id, count=5)
items = r.get('data', {}).get('aweme_list', [])
for item in items:
    print(item.get('aweme_id'), item.get('desc', '')[:60])
```
