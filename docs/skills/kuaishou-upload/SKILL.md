---
name: kuaishou-upload
description: 当 agent 需要将视频发布到快手时使用这个 skill。通过 sau CLI 调用 Playwright 自动化上传，无需代理，内置重试逻辑。
---

# 快手上传 Skill

## Skill 文件结构

```
.claude/skills/kuaishou-upload/
├── SKILL.md
└── scripts/
    └── upload.py    ← 主入口，自动重试
```

## 前置条件

1. `sau` CLI 已安装（`pip install -e /path/to/social-auto-upload`）
2. Cookie 有效：`social-auto-upload/cookies/kuaishou_<account>.json`
   - 验证：`sau kuaishou check --account <account>`
   - 过期时重新登录：`sau kuaishou login --account <account>`

## 上传命令

```bash
SKILL_DIR="$HOME/.claude/skills/kuaishou-upload"
python3 "$SKILL_DIR/scripts/upload.py" \
  --account "<your-account>-kuaishou" \
  --file    "<视频文件路径>" \
  --title   "<视频标题>" \
  --tags    "tag1,tag2,tag3"
```

**参数说明：**

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--account` | 是 | — | sau 账号名，如 `<your-account>-kuaishou` |
| `--file` | 是 | — | 视频文件路径 |
| `--title` | 是 | — | 视频标题 |
| `--tags` | 是 | — | 逗号分隔的标签 |
| `--desc` | 否 | — | 可选描述文字 |
| `--schedule` | 否 | — | 定时发布，格式 `YYYY-MM-DD HH:MM` |
| `--max-retries` | 否 | `3` | 最大重试次数 |

## 注意事项

- 快手**不需要系统代理**（直连可达），也不需要 `DISPLAY=:0`
- TikHub `fetch_one_video_v2` 偶发 HTTP 400，属接口问题，不影响发布结果，刷新指标时直接跳过即可

## 发布后验证

```python
from tikhub import TikHub
import os
from dotenv import load_dotenv
load_dotenv()

client = TikHub(api_key=os.environ['TIKHUB_API_TOKEN'])
# user_id 从 workspace/<username>/platform_config.json 读取
user_id = "<user_id>"
r = client.kuaishou_web.fetch_user_post(userId=user_id, count=5)
items = r.get('data', {}).get('visionVideoList', [])
for item in items:
    photo = item.get('photo', {})
    print(photo.get('id'), photo.get('caption', '')[:60])
```
