---
name: youtube-upload
description: 当 agent 需要通过 `ytcli` 完成 YouTube 视频上传、OAuth 授权或视频状态查询时使用这个 skill。脚本内置于 skill 的 scripts/ 目录，换机器时可自动部署，secrets 需单独配置。
---

# YouTube 上传 Skill

优先使用 skill 自带的 `scripts/upload.py` 作为主入口。

不要假设 token 一定有效，若遇到 401/403 错误，先走重新授权流程。
只有命令失败时，才排查 token 或 credentials 问题。

## Skill 文件结构

```
~/.claude/skills/youtube-upload/
├── SKILL.md          # 本文件
└── scripts/
    ├── upload.py     # 上传脚本（主入口）
    └── auth.py       # OAuth 授权脚本
```

secrets 目录（含密钥，不放入 skill）：
```
<project>/ytcli/secrets/
├── credentials.json  # Google OAuth 客户端凭证（从 Google Cloud Console 下载）
└── token.json        # 用户授权 token（首次运行 auth.py 生成）
```

## 首次部署（换机器时）

```bash
# 1. 将脚本部署到项目
SKILL_DIR="$HOME/.claude/skills/youtube-upload/scripts"
mkdir -p <project>/ytcli/secrets
cp "$SKILL_DIR/upload.py" <project>/ytcli/upload.py
cp "$SKILL_DIR/auth.py"   <project>/ytcli/auth.py

# 2. 从 Google Cloud Console 下载 credentials.json 放入 ytcli/secrets/

# 3. 首次授权（会打开浏览器）
python3 <project>/ytcli/auth.py
```

## 功能概览

| 功能 | 入口 | 说明 |
|------|------|------|
| 上传视频 | `python3 ytcli/upload.py --file ... --title ...` | 上传并发布 YouTube 视频 |
| 重新授权 | `python3 ytcli/auth.py` | 重走 OAuth 流程，刷新 token.json |
| TikHub 查询 | `client.youtube_web.get_video_info(video_id=...)` | 用 TikHub 查询视频是否上线及基本数据 |

## 默认工作流

1. 确认视频文件路径存在。
2. 确认 `ytcli/secrets/token.json` 存在。
3. 执行 `python3 ytcli/upload.py` 上传，等待返回 `videoId`。
4. 用 TikHub `youtube_web.get_video_info` 验证视频已上线。
5. 如遇 401 `youtubeSignupRequired` 或 token 过期，执行 `python3 ytcli/auth.py` 重新授权后重试。

## 标题与标签语言

**YouTube 必须使用英文**，从 VideoAssistant 结果中取 `en_title` 和 `en_tags`（不要用 `xhs_title`/`xhs_tags`）：

```python
en_title = result["en_title"]          # 英文标题，5-10 词
en_tags  = result["en_tags"]           # 英文标签列表，不含 #
tags_str = ",".join(en_tags)
```

## 上传命令完整参数

```bash
python3 ytcli/upload.py \
  --file      "<视频文件路径>" \
  --title     "<en_title（英文）>" \
  --description "<en_title（英文）>" \
  --tags      "<en_tags 逗号分隔（英文）>" \
  --category  "<分类 ID>" \
  --privacy   "public|private|unlisted"
```

**常用 category ID：**

| ID | 分类 |
|----|------|
| 22 | People & Blogs（人物与博客）|
| 28 | Science & Technology（科技）|
| 24 | Entertainment（娱乐）|
| 25 | News & Politics（新闻）|

**privacy 默认值：** `private`（建议先用 private 测试，确认无误后改 public）

## Token 与授权

- token 文件路径：`ytcli/secrets/token.json`
- credentials 文件路径：`ytcli/secrets/credentials.json`
- token 过期时 `upload.py` 会自动用 refresh_token 刷新，无需手动操作
- 若报 `youtubeSignupRequired`，说明绑定的 Google 账号未开通 YouTube 频道，需先在 YouTube 创建频道，再运行 `python3 ytcli/auth.py` 重新授权

## 重新授权流程

```bash
python3 ytcli/auth.py
# 会打开浏览器完成 OAuth，token.json 自动更新
```

授权后重新执行上传命令即可。

## 用 TikHub 验证视频上线

```python
from tikhub import TikHub
import os
from dotenv import load_dotenv
load_dotenv()

client = TikHub(api_key=os.environ['TIKHUB_API_TOKEN'])
r = client.youtube_web.get_video_info(video_id='<videoId>')
print(r['data']['title'], r['data']['channel']['name'])
```

返回 `errorId: "Success"` 且有 title 说明视频已公开可检索。

## 执行前检查

- 确认工作目录为项目根目录（`ytcli/` 相对路径正确）
- 确认 `ytcli/secrets/token.json` 存在且非空
- 视频文件路径使用绝对路径或相对于项目根目录的路径
- 上传成功后记录返回的 `videoId`，用于后续状态查询和 publish_log 写入
