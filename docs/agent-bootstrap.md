# Agent Bootstrap Prompt

这份文档是写给 OpenClaw、Codex、Claude Code / cc 这类 agent 客户端用户的。目标不是让 agent 先通读整个仓库，而是先把该仓库安装到**可运行、可验证、可继续执行任务**的状态。

将以下 prompt 粘贴到新会话的开头：

```
You are helping me set up and run the Auteur social media deployment experiment.
Work through the following steps in order. At each step, check the current
state before acting — do not repeat work that is already done.

## Step 1 — Confirm username

Ask me: "What username do you want to use for this experiment?"
This username isolates all workspace data under workspace/<username>/.
All subsequent steps depend on it.

## Step 2 — Verify environment

Check that the following are in place. For each missing item, show me
the exact command to fix it:

1. `.env` exists at the project root with ARK_API_KEY, TOS_ACCESS_KEY,
   TOS_SECRET_KEY, TIKHUB_API_TOKEN filled in.
2. `social-auto-upload` submodule is present and `sau` CLI is importable
   (run: `sau --version`).
3. `xiaohongshu-mcp/xiaohongshu-mcp` binary exists (if not, run:
   `cd xiaohongshu-mcp && go build -o xiaohongshu-mcp .`).
4. ffmpeg is installed (`ffmpeg -version`).

## Step 3 — Platform account setup

### Douyin / Bilibili / Kuaishou (sau)
Check whether cookies exist for each platform:
  social-auto-upload/cookies/douyin_<username>-douyin.json
  social-auto-upload/cookies/bilibili_<username>-bilibili.json
  social-auto-upload/cookies/kuaishou_<username>-kuaishou.json

For any missing cookie, guide me through login:
  sau douyin login --account <username>-douyin
  sau bilibili login --account <username>-bilibili
  sau kuaishou login --account <username>-kuaishou

### Xiaohongshu (xiaohongshu-mcp)
Check workspace/<username>/xhs_config.json for xhs_account and xhs_user_id.
If missing, ask me for my Xiaohongshu nickname and user ID, then run:
  python3 utils/xhs_publish.py set-account \
      --username <username> --xhs-account <nickname> --xhs-user-id <user_id>

Start the MCP service in the background and confirm it responds:
  cd xiaohongshu-mcp && nohup ./start.sh > /tmp/xhs-mcp.log 2>&1 &
  curl -s http://localhost:18060/api/v1/health || echo "service not ready"

### Platform user IDs (for metrics)
Check workspace/<username>/platform_config.json.
If missing, ask me for my Douyin sec_user_id, Bilibili uid, and Kuaishou
user_id, then write the file:
  {
    "douyin":   { "sec_user_id": "<sec_user_id>" },
    "bilibili": { "uid": "<uid>" },
    "kuaishou": { "user_id": "<user_id>" }
  }

## Step 4 — Run a test generation

Run a single generation to verify the full pipeline end-to-end:
  python3 -c "
  from video_assistant import VideoAssistant
  a = VideoAssistant(
      output_dir='workspace/output/bootstrap_test',
      time_length=5,
      username='<username>',
      allow_interactive=False,
  )
  result = a.run(user_input='生成一段5秒的测试视频，展示自然风景')
  print(result)
  "
Show me the result and confirm output_path is a valid video file.

## Step 5 — Start the content pipeline

Once the test passes, start the continuous pipeline:
  python3 utils/pipeline_runner.py --username <username> --max 3

This runs: fetch trending topic → generate video → publish to XHS →
write publish_log → backfill social-media-feedback into trace.

## Step 6 — Monitor

### Dashboard
Start the dashboard and open it in a browser:
  .venv/bin/python dashboard/app.py
  # Opens at http://localhost:8080

The dashboard shows all episodes (trace files), per-episode tool call
timelines, plan structures, and publish metrics.

### Metrics refresh
To pull the latest audience metrics from all platforms:
  python3 utils/refresh_tikhub_all.py --username <username>

### Trigger learning
After collecting enough traces (≥ 3 episodes recommended), run:
  python -m learning.learning \
      --meta_skill skills/SKILL_<username>.md \
      --username <username> \
      --trace workspace/<username>/context \
      --engine kimi-k2-turbo-preview

The optimized skill is written back to skills/SKILL_<username>.md and
takes effect on the next pipeline run.
```
