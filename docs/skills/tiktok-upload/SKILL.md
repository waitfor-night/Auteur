---
name: tiktok-upload
description: 当 agent 需要将视频发布到 TikTok 时使用这个 skill。使用 tiktok-uploader + Playwright chromium，优先无头模式，失败自动切换有头模式重试。适用于已配置 cookies.txt 的环境。
---

# TikTok 上传 Skill

优先使用 `scripts/upload.py` 作为主入口，它会自动处理无头/有头模式切换。

## Skill 文件结构

```
.claude/skills/tiktok-upload/
├── SKILL.md
└── scripts/
    └── upload.py    ← 主入口，自动切换无头/有头模式
```

## 上传命令

```bash
SKILL_DIR="$HOME/.claude/skills/tiktok-upload"
DISPLAY=:0 python3 "$SKILL_DIR/scripts/upload.py" \
  --file      "<视频文件路径>" \
  --description "<描述文字 #tag1 #tag2>" \
  --cookies   "$SKILL_DIR/cookies.txt"
```

**参数说明：**

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--file` | 是 | — | 视频文件路径 |
| `--description` | 是 | — | 描述文字，直接包含 `#tag`，不需要单独传 tags |
| `--cookies` | 否 | `$SKILL_DIR/cookies.txt` | cookies 文件路径（skill 目录内已内置） |
| `--max-retries` | 否 | `2` | 每种模式的最大重试次数 |

## 执行逻辑

1. **无头模式**（headless=True）优先，最多重试 2 次
2. 无头失败后自动切换**有头模式**（headless=False），最多重试 2 次
3. 全部失败才报错退出

## 为什么新账号无头模式会失败

新账号进入上传页时 TikTok 会弹出两种弹窗遮挡描述输入框：
- **版权检测弹窗**（`TUXModal-overlay`）— 上传后必弹
- **新用户引导弹窗**（`react-joyride__overlay`）— 新账号首次使用

有头模式下这些弹窗会自动消失，因此更稳定。账号使用一段时间后引导弹窗消失，无头模式成功率会提高。

## 前置条件

- 已安装 `tiktok-uploader`：`pip install tiktok-uploader`
- 已安装 Playwright chromium：`playwright install chromium`
- `cookies.txt` 为 Netscape 格式，通过浏览器插件导出
- WSL 环境需要有 `DISPLAY=:0`（有头模式必须）

## 账号信息（workspace/zhaili/platform_config.json）

```json
"tiktok": {
  "unique_id": "loner_968",
  "sec_uid": "MS4wLjABAAAAxNn1Nu4UtZPa-H962KUghGEmuI6KrP9dlrIef79Qopm2CV8h4DKS7jqzfszurNeZ"
}
```

## 发布后验证

```python
from tikhub import TikHub
import os
from dotenv import load_dotenv
load_dotenv()

client = TikHub(api_key=os.environ['TIKHUB_API_TOKEN'])
sec_uid = "MS4wLjABAAAAxNn1Nu4UtZPa-H962KUghGEmuI6KrP9dlrIef79Qopm2CV8h4DKS7jqzfszurNeZ"
r = client.tiktok_web.fetch_user_post(secUid=sec_uid, count=3)
items = r.get('data', {}).get('itemList', [])
for item in items:
    print(item.get('id'), item.get('desc', '')[:60])
```
