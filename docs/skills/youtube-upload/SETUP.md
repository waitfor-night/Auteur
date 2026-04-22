# YouTube Data API + OAuth 2.0 配置指南

本文档说明如何从零配置 Google Cloud 项目，获得 `credentials.json` 和 `token.json`，使 `upload.py` 脚本能够自动上传视频到 YouTube 频道。

---

## 目录结构

```
ytcli/
├─ secrets/
│  ├─ credentials.json   ← 第 7 步下载（应用身份凭据）
│  └─ token.json         ← 第 8 步授权后自动生成（用户授权令牌）
├─ auth.py
└─ upload.py
```

---

## 第 1 步：创建或选择 Google Cloud 项目

进入 [Google Cloud Console](https://console.cloud.google.com/)，选择已有项目或新建一个专用项目（如 `youtube-uploader`）。后续所有操作均在该项目内完成。

---

## 第 2 步：启用 YouTube Data API v3

1. 在项目内打开 **API Library**
2. 搜索 `YouTube Data API v3`
3. 点击 **Enable（启用）**

> 未启用 API 时，即使授权成功，上传接口调用也会报错。

---

## 第 3 步：配置 OAuth 同意屏幕

进入 **Google Auth Platform → OAuth consent screen**，完成以下三项：

| 项目 | 填写内容 |
|------|----------|
| 应用名称 | 任意，如 `YouTube Uploader` |
| 支持邮箱 | 你的 Google 账号邮箱 |
| Audience | `External`（个人账号选此项） |

在 **Data Access** 中添加所需 Scope（见第 4 步）。

---

## 第 4 步：添加最小必要权限 Scope

只需添加一个 Scope：

```
https://www.googleapis.com/auth/youtube.upload
```

这是 `videos.insert` 接口所需的最小权限，仅用于上传视频，不授予其他操作。

---

## 第 5 步：添加测试用户（Testing 状态必须）

如果 OAuth 应用处于 **Testing** 状态：
- 在 **Test users** 中把你自己的 Google 账号加入

> **注意**：Testing 状态下的 refresh token 约 **7 天后失效**。如需长期无人值守自动发布，配置完成后将状态切换为 **In production**。

---

## 第 6 步：创建 OAuth Client ID

1. 进入 **Credentials（凭据）**
2. 点击 **Create Credentials → OAuth client ID**
3. 应用类型选 **Desktop app**
4. 命名后点击创建

> 本地 CLI 脚本必须使用 Desktop app 类型，Google 官方对此有专门的桌面应用 OAuth 流程支持。不要使用 Service Account（YouTube Data API 不支持 Service Account 操作普通频道，会报 `NoLinkedYouTubeAccount`）。

---

## 第 7 步：下载 credentials.json

创建成功后，点击 **下载 JSON**，将文件保存为：

```
ytcli/secrets/credentials.json
```

此文件是「应用是谁」的身份凭据，不含用户授权信息，妥善保管勿泄露。

---

## 第 8 步：首次运行授权，生成 token.json

```bash
python3 ytcli/auth.py
```

脚本会：
1. 读取 `credentials.json`
2. 打开浏览器，引导你登录 Google 账号并同意授权
3. 授权成功后自动保存 `ytcli/secrets/token.json`

之后每次上传，脚本会用 `token.json` 自动刷新 access token，无需重复授权。

---

## 第 9 步：上传视频

```bash
python3 ytcli/upload.py \
    --file  "视频路径.mp4" \
    --title "视频标题" \
    --description "视频描述" \
    --tags  "tag1,tag2,tag3" \
    --category "22" \
    --privacy "public"
```

| 参数 | 说明 |
|------|------|
| `--category` | YouTube 分类 ID，`22` = People & Blogs |
| `--privacy` | `public` / `unlisted` / `private` |

---

## 常见问题

| 错误 | 原因 | 解决 |
|------|------|------|
| `NoLinkedYouTubeAccount` | 使用了 Service Account | 改用 Desktop app OAuth |
| `Token expired` | refresh token 7 天失效 | 重新运行 `auth.py` 或切换为 In production |
| `quotaExceeded` | YouTube API 每日配额用完（默认 10000 units） | 次日重试或申请配额提升 |
| `forbidden` | Scope 不够或账号未通过审核 | 检查 Scope 是否含 `youtube.upload` |
