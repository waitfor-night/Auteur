# Auteur

**Personalizing Video Creation via Prism-Trace Optimization**

Auteur is a video creation agent that learns your creative style over time. A Planner-Actor architecture executes multi-stage video tasks; after each session, Prism-Trace decomposes the execution trace into multiple optimization views and uses TextGrad to refine the agent's task skill and user memory in-place.

---

Using Claude Code, OpenClaw, Codex, or another agent client? See [docs/agent-bootstrap.md](docs/agent-bootstrap.md) to get the repo running in one session.

---

## Setup

```bash
bash setup.sh
```

The setup script initializes git submodules, installs Python dependencies, builds the xiaohongshu-mcp Go binary, and checks for ffmpeg.

**Environment variables** — copy to `.env` in the project root:

```bash
ARK_API_KEY=        # Doubao/Ark LLM API (required)
TOS_ACCESS_KEY=     # ByteDance TOS object storage (required)
TOS_SECRET_KEY=     # ByteDance TOS object storage (required)
TIKHUB_API_TOKEN=   # Multi-platform metrics (required for publishing)
DEEPSEEK_API_KEY=   # Optional
KIMI_API_KEY=       # Optional
```

---

## Run

```python
from video_assistant import VideoAssistant

assistant = VideoAssistant(
    output_dir="workspace/output/my_task",
    time_length=15,
    username="alice",
    allow_interactive=False,
)
result = assistant.run(user_input="生成一段海边日落风景视频")
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `output_dir` | required | Output directory |
| `time_length` | 15 | Max duration per clip (seconds) |
| `total_duration` | None | Total video length; Planner splits into clips automatically |
| `username` | None | Isolates context/trace under `workspace/<username>/` |
| `allow_interactive` | True | Wait for terminal feedback after each round |
| `context_id` | None | Resume an existing session (`ctx_xxx` ID) |

Each run writes:
- `workspace/<username>/context/ctx_*.json` — multi-round memory (plan, skill, feedback)
- `workspace/<username>/trace/ep_*.json` — full tool call log (args, return, duration, status)

---

## Prism-Trace Optimization

A single trace is decomposed into independent optimization views — like white light through a prism:

| View | Signal source | Optimizes |
|------|--------------|-----------|
| `execution_view` | Tool call sequence + plan structure + errors | Meta-skill |
| `preference_view` | Creator feedback + satisfaction signal | User memory |
| `audience_view` | Social media metrics (likes, views, growth) | Content strategy |

`execution_view` is always present. `preference_view` is active in sandbox experiments (creator feedback loop); `audience_view` is active in real-world deployment (social media metrics).

**Joint optimization** (recommended):

```bash
python -m learning.learning \
    --meta_skill skills/SKILL_<username>.md \
    --username <username> \
    --trace workspace/<username>/context \
    --engine kimi-k2-turbo-preview
```

**Meta-skill only:**

```bash
python -m learning.optimize_meta_skill \
    --meta_skill skills/SKILL_<username>.md \
    --trace workspace/<username>/context \
    --output skills/SKILL_<username>_opt.md
```

**User memory only:**

```bash
python -m learning.update_memory \
    --username <username> \
    --trace workspace/<username>/context
```

---

## Sandbox Experiment

```bash
python -m sandbox.run_episode_loop --username doc_rigorous --role_id doc_rigorous
python -m sandbox.eval_metrics --username doc_rigorous --output sandbox/output/metrics.json
```

---

## Real-World Deployment

The second experiment runs Auteur as a live content pipeline on social media. The agent generates videos from trending topics, publishes them across platforms (Douyin, Bilibili, Kuaishou, Xiaohongshu), and collects audience metrics over time. These metrics feed back as `audience_view` in Prism-Trace, optimizing content strategy toward what actually resonates with viewers — without any explicit creator feedback.

```bash
# Generate and publish
python utils/pipeline_runner.py --username <username>

# Refresh audience metrics
python utils/refresh_tikhub_all.py --username <username>

# Trigger learning from deployment traces
python -m learning.learning \
    --meta_skill skills/SKILL_<username>.md \
    --username <username> \
    --trace workspace/<username>/context \
    --engine kimi-k2-turbo-preview
```
