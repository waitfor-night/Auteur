---
name: bilibili-upload
description: 当 agent 需要将视频发布到 B站（哔哩哔哩）时使用这个 skill。通过 sau CLI 调用 biliup 上传，无需代理和有头模式，内置重试逻辑。
---

# B站上传 Skill

## Skill 文件结构

```
.claude/skills/bilibili-upload/
├── SKILL.md
└── scripts/
    └── upload.py    ← 主入口，自动重试
```

## 前置条件

1. `sau` CLI 已安装（`pip install -e /path/to/social-auto-upload`）
2. Cookie 有效：`social-auto-upload/cookies/bilibili_<account>.json`
   - 验证：`sau bilibili check --account <account>`
   - 过期时重新登录：`sau bilibili login --account <account>`

## 上传命令

```bash
SKILL_DIR="$HOME/.claude/skills/bilibili-upload"
python3 "$SKILL_DIR/scripts/upload.py" \
  --account "<your-account>-bilibili" \
  --file    "<视频文件路径>" \
  --title   "<视频标题>" \
  --desc    "<视频描述>" \
  --tags    "tag1,tag2,tag3" \
  --tid     188
```

**参数说明：**

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--account` | 是 | — | sau 账号名，如 `<your-account>-bilibili` |
| `--file` | 是 | — | 视频文件路径 |
| `--title` | 是 | — | 视频标题 |
| `--desc` | 是 | — | 视频描述（B站必填） |
| `--tags` | 是 | — | 逗号分隔的标签 |
| `--tid` | 否 | `188` | B站分区 ID |
| `--schedule` | 否 | — | 定时发布，格式 `YYYY-MM-DD HH:MM` |
| `--max-retries` | 否 | `3` | 最大重试次数 |

**常用 tid 分区：**

| tid | 分区 |
|-----|------|
| 188 | 科技（通用） |
| 95 | 数码 |
| 231 | 计算机技术 |
| 201 | 科学科普 |
| 17 | 单机游戏 |
| 171 | 电子竞技 |

## 注意事项

- B站上传使用 biliup（非 Playwright），**不需要代理和 DISPLAY**
- 上传大文件（>500MB）时建议将 `--max-retries` 设为 1，避免重复投稿
- `--desc` 为必填，传入空字符串会被 B站拒绝

## 发布后验证

```python
from tikhub import TikHub
import os
from dotenv import load_dotenv
load_dotenv()

client = TikHub(api_key=os.environ['TIKHUB_API_TOKEN'])
# uid 从 workspace/<username>/platform_config.json 读取
uid = "<uid>"
r = client.bilibili_web.fetch_user_video_list(uid=uid, ps=5)
items = r.get('data', {}).get('list', {}).get('vlist', [])
for item in items:
    print(item.get('bvid'), item.get('title', '')[:60])
```
