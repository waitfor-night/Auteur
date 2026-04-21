从热点话题状态文件中取下一个未处理的话题，生成视频，发布到所有平台，写入发布日志，完成后自动进入下一轮。

## 参数

`$ARGUMENTS` 格式：`--username <username> [--max N]`，例如 `--username test --max 3`

- `--username`：必填，后续所有步骤均使用该值
- `--max N`：本轮最多处理 N 个话题后停止，默认 3

执行前从 `$ARGUMENTS` 中解析出 `username` 和 `max`（未指定则 max=3），初始化计数器 `completed=0`。

sau 账号命名规律：`<username>-douyin`、`<username>-bilibili`、`<username>-kuaishou`（如 username=test，则账号为 test-douyin）。

sau CLI 路径：`/home/shuyun/work/multi-shot-multi-object-long-video-edit/.venv/bin/sau`（下文 `sau` 均指此路径）。

---

## Step 0 — 前置检查：platform_config.json

检查 `workspace/<username>/platform_config.json` 是否存在，缺失时提示但不阻断：

```bash
python3 -c "
import json, sys
from pathlib import Path

username = '<username>'
cfg_path = Path('workspace') / username / 'platform_config.json'

if not cfg_path.exists():
    print('[warn] platform_config.json 不存在，各平台 post_id 补全和指标刷新将依赖搜索发现（首次较慢）。')
    print('[warn] 可在 workspace/<username>/platform_config.json 中预配置：')
    print(json.dumps({
        'douyin':   {'sec_user_id': 'MS4wLjABAAAA...'},
        'bilibili': {'uid': '123456789'},
        'kuaishou': {'user_id': 'abc123'},
    }, ensure_ascii=False, indent=2))
else:
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    missing = [p for p in ['douyin', 'bilibili', 'kuaishou'] if not cfg.get(p)]
    if missing:
        print(f'[warn] platform_config.json 缺少以下平台：{missing}，对应平台将用标题搜索发现 user_id。')
    else:
        print('[ok] platform_config.json:', list(cfg.keys()))
"
```

---

## Step 1 — 刷新热点并取下一个话题

先尝试刷新热点（2 天内有缓存会自动跳过），然后取第一个 unused 话题标记为 in_progress：

```bash
python3 utils/hot_topics_cron.py --username <username> 2>/dev/null; \
python3 -c "
import json, sys
from pathlib import Path

username = '<username>'
state_path = Path('workspace') / username / 'hot_topics_state.json'

if not state_path.exists():
    print('ERROR: 状态文件不存在，请先运行: python3 utils/hot_topics_cron.py --username <username> --force')
    sys.exit(1)

state = json.loads(state_path.read_text(encoding='utf-8'))
topics = state.get('topics', [])
if not topics:
    print('ERROR: 话题列表为空')
    sys.exit(1)

unused = [t for t in topics if t['status'] == 'unused']
if not unused:
    for t in topics:
        t['status'] = 'unused'
        t['use_count'] = t.get('use_count', 0) + 1
    unused = topics
    print('[pipeline] 所有话题已轮完，从头循环', file=sys.stderr)

topic = unused[0]
topic['status'] = 'in_progress'
state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(topic, ensure_ascii=False))
"
```

记录输出的 topic JSON，后续步骤需要以下字段：

| 字段 | 说明 |
|------|------|
| `title` | 话题标题 |
| `platform` | 来源平台 |
| `url` | 原始链接 |
| `video_style` | `showcase` / `question` / `narrative`，未填时按 `showcase` 处理 |
| `preferred_ratio` | `9:16` 或 `16:9`，未填时按 `9:16` 处理 |
| `video_concept` | 一句话视觉概念，未填时为空字符串 |
| `media.ref_video` | 本地参考视频绝对路径；字段不存在或为 `null` 时忽略 |

---

## Step 2 — 生成视频

用 Step 1 拿到的话题调用 VideoAssistant：

```python
import json
from video_assistant import VideoAssistant

username      = "<username>"
title         = "<Step1.title>"
platform      = "<Step1.platform>"
url           = "<Step1.url>"
video_style   = "<Step1.video_style or 'showcase'>"
preferred_ratio = "<Step1.preferred_ratio or '9:16'>"
video_concept = "<Step1.video_concept or ''>"
ref_video     = "<Step1.media.ref_video or ''>"   # 本地绝对路径，可能为空

# 构造 user_input：将媒体路径和创作方向一起交给 MoMo
lines = [f"根据热点话题「{title}」制作一个{video_style}风格的短视频。"]
if video_concept:
    lines.append(f"视觉概念：{video_concept}")
lines.append(f"画面比例：{preferred_ratio}")
lines.append(f"来源平台：{platform}，参考链接：{url}")
if ref_video:
    lines.append(f"本地参考视频（可直接调用工具加载）：{ref_video}")
user_input = "\n".join(lines)

assistant = VideoAssistant(
    output_dir=f"workspace/output/hot_topics_{title[:10]}",
    username=username,
    allow_interactive=False,
)
result = assistant.run(user_input=user_input)

with open('/tmp/va_result.json', 'w') as f:
    json.dump(result, f, ensure_ascii=False)

print(json.dumps(result, ensure_ascii=False))
```

记录 `trace_id`、`trace_path`、`result_video`（若为空，从 output_dir 下查找实际 mp4 路径）、`xhs_title`、`xhs_tags`。

---

## Step 3 — 发布到小红书（MCP）

```bash
python3 utils/xhs_publish.py auto-publish \
    --username <username> \
    --result-json-str "$(cat /tmp/va_result.json)"
```

记录输出 JSON 中的 `trace_id`、`trace_path`、`result_video`、`title`、`tags`（列表）、`post_id`、`xsec_token`。

若发布失败（exit != 0），标记 `XHS_SUCCESS=false`，继续执行 Step 4（其他平台不受影响）。
若成功，标记 `XHS_SUCCESS=true`。

---

## Step 4 — 发布到抖音 / Bilibili / 快手（sau CLI）

依次执行以下命令，记录每个平台退出码（0 = 成功）：

```bash
# 抖音（标题限 30 字）
/home/shuyun/work/multi-shot-multi-object-long-video-edit/.venv/bin/sau douyin upload-video \
    --account <username>-douyin \
    --file "<Step3.result_video>" \
    --title "<Step3.title 前30字>" \
    --tags "<Step3.tags 逗号分隔>" 2>&1
echo "DOUYIN_EXIT:$?"

# Bilibili（标题限 80 字，tid=25 为生活区）
/home/shuyun/work/multi-shot-multi-object-long-video-edit/.venv/bin/sau bilibili upload-video \
    --account <username>-bilibili \
    --file "<Step3.result_video>" \
    --title "<Step3.title 前80字>" \
    --desc "<Step3.title 前80字>" \
    --tags "<Step3.tags 逗号分隔>" \
    --tid 25 2>&1
echo "BILIBILI_EXIT:$?"

# 快手（标题限 30 字）
/home/shuyun/work/multi-shot-multi-object-long-video-edit/.venv/bin/sau kuaishou upload-video \
    --account <username>-kuaishou \
    --file "<Step3.result_video>" \
    --title "<Step3.title 前30字>" \
    --tags "<Step3.tags 逗号分隔>" 2>&1
echo "KUAISHOU_EXIT:$?"
```

**仅将退出码为 0 的平台加入 `SUCCEEDED_PLATFORMS` 列表。**
小红书若 `XHS_SUCCESS=true`，也加入列表（格式：`xiaohongshu:<username>-xiaohongshu`）。

---

## Step 5 — 写入多平台发布日志

**仅记录 Step 3/4 中发布成功的平台**，格式为 `platform:account`：

```bash
python3 utils/publish_record_v2.py record \
    --username <username> \
    --trace-id "<Step3.trace_id>" \
    --trace-path "<Step3.trace_path>" \
    --result-video "<Step3.result_video>" \
    --title "<Step3.title>" \
    --tags <Step3.tags 空格分隔> \
    --platforms <SUCCEEDED_PLATFORMS 空格分隔>
```

示例（抖音和小红书成功，B站和快手失败）：
```bash
    --platforms douyin:<username>-douyin xiaohongshu:<username>-xiaohongshu
```

> 失败的平台不传入 `--platforms`，后续可用 `publish_record_v2.py retry-publish` 重试。

---

## Step 5.5 — 发布后立即采集指标（post_id 补全 + 首次指标）

等待约 30 秒让平台完成收录，然后运行统一刷新脚本：

```bash
python3 -c "import time; time.sleep(30)"
python3 utils/refresh_tikhub_all.py --username <username>
```

脚本内部：
1. 读 `platform_config.json` 获取各平台 user_id（首次自动通过标题搜索发现并缓存）
2. 按标题匹配补全各平台 `post_id`
3. 拉取赞/评/播放/收藏指标 + 评论树
4. 写入 `publish_log_v2.json`
5. 回写 trace 文件（`social-media-feedback` 字段）

---

## Step 6 — 标记话题为 done

```bash
python3 -c "
import json
from pathlib import Path

username   = '<username>'
title      = '<Step1.title>'
state_path = Path('workspace') / username / 'hot_topics_state.json'
state      = json.loads(state_path.read_text(encoding='utf-8'))

for t in state['topics']:
    if t['title'] == title:
        t['status'] = 'done'
        break

state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
print('[pipeline] 话题已标记为 done：' + title)
"
```

---

## Step 7 — 计数并决定是否继续

```python
completed += 1
```

- 若 `completed >= max`：输出本轮完成提示，**停止**（调用方 `run_pipeline_cron.sh` 负责下次调度）
- 若 `completed < max`：**等待 20 分钟**（防风控）后回到 Step 1

```bash
if [ "$completed" -ge "$max" ]; then
    echo "[pipeline] 本轮已完成 ${completed}/${max} 个话题，退出。"
    exit 0
else
    echo "[pipeline] 进度 ${completed}/${max}，等待 20 分钟后继续..."
    python3 -c "import time; time.sleep(1200)"
fi
```

> **注意**：若某个话题视频生成或所有平台发布均失败，将该话题重置为 `unused` 等下轮重试，失败计数累计 2 次则跳过（标记 done）。
