#!/usr/bin/env python3
"""
utils/pipeline_runner.py — 热点话题视频发布持续流水线

循环执行：取下一个热点话题 → VideoAssistant 生成视频 → MCP 发布到小红书
         → 写入 publish_log → 回填 social-media-feedback 到 trace → 标记话题 done

用法：
    python3 utils/pipeline_runner.py --username <username> [--max 5]

环境变量（均有默认值，通常无需设置）：
    XHS_MCP_URL          MCP JSON-RPC 地址（默认 http://localhost:18060/mcp）
    XHS_MCP_BASE_URL     HTTP REST 地址（默认 http://localhost:18060/api/v1）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# 优先用项目 venv 的 Python 解释器（含所有依赖）
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python"
_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable


# ──────────────────────────────────────────────────────────────
# 话题管理
# ──────────────────────────────────────────────────────────────

def _state_path(username: str) -> Path:
    return _ROOT / "workspace" / username / "hot_topics_state.json"


def refresh_topics(username: str) -> None:
    """调用 hot_topics_cron.py 刷新话题列表（不满 2 天会自动跳过）。"""
    subprocess.run(
        [_PYTHON, str(_ROOT / "utils" / "hot_topics_cron.py"), "--username", username],
        capture_output=True,
    )


def pick_next_topic(username: str) -> dict | None:
    """取下一个 unused 话题，标记为 in_progress，返回 topic dict。"""
    p = _state_path(username)
    if not p.exists():
        print(f"[pipeline] 状态文件不存在，请先运行 hot_topics_cron.py --username {username} --force")
        return None

    state = json.loads(p.read_text(encoding="utf-8"))
    topics = state.get("topics", [])
    unused = [t for t in topics if t["status"] == "unused"]

    if not unused:
        for t in topics:
            t["status"] = "unused"
            t["use_count"] = t.get("use_count", 0) + 1
        unused = topics
        print(f"[pipeline] 所有话题已轮完，开始第 {unused[0].get('use_count', 1)} 轮")

    topic = unused[0]
    topic["status"] = "in_progress"
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return topic


def mark_topic(username: str, title: str, status: str) -> None:
    """将话题设置为指定状态（done / unused / in_progress）。"""
    p = _state_path(username)
    if not p.exists():
        return
    state = json.loads(p.read_text(encoding="utf-8"))
    for t in state.get("topics", []):
        if t["title"] == title:
            t["status"] = status
            break
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────
# 视频生成
# ──────────────────────────────────────────────────────────────

def generate_video(username: str, topic: dict) -> dict:
    """调用 VideoAssistant 生成视频（venv 子进程），返回 result dict。"""
    title = topic["title"]
    platform = topic.get("platform", "")
    url = topic.get("url", "")
    user_input = (
        f"根据热点话题「{title}」制作一个适合小红书的短视频，"
        f"来源平台：{platform}，参考链接：{url}"
    )
    output_dir = f"workspace/output/hot_topics_{title[:10]}"
    print(f"[pipeline] 开始生成视频，output_dir={output_dir}", flush=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        result_tmp = f.name

    script = (
        "import json\n"
        "from video_assistant import VideoAssistant\n"
        f"a = VideoAssistant(output_dir={output_dir!r}, username={username!r}, allow_interactive=False)\n"
        f"r = a.run(user_input={user_input!r})\n"
        f"open({result_tmp!r}, 'w', encoding='utf-8').write(json.dumps(r, ensure_ascii=False))\n"
    )
    r = subprocess.run(
        [_PYTHON, "-c", script],
        cwd=str(_ROOT),
        # stdout/stderr 直接继承（日志流到终端）
    )
    try:
        result = json.loads(Path(result_tmp).read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError(f"generate_video 结果文件解析失败，exit={r.returncode}")
    finally:
        Path(result_tmp).unlink(missing_ok=True)
    return result


# ──────────────────────────────────────────────────────────────
# 发布
# ──────────────────────────────────────────────────────────────

def auto_publish(username: str, va_result: dict) -> dict:
    """调用 xhs_publish.py auto-publish，返回含 post_id/xsec_token 的 publish_params dict。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(va_result, f, ensure_ascii=False)
        tmp = f.name

    r = subprocess.run(
        [_PYTHON, str(_ROOT / "utils" / "xhs_publish.py"),
         "auto-publish", "--username", username, "--result-json-str", f"@{tmp}"],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    Path(tmp).unlink(missing_ok=True)

    if r.returncode != 0:
        raise RuntimeError(f"auto-publish 失败（exit {r.returncode}）：{r.stderr.strip()}")

    # stdout 最后一行是 JSON
    stdout = r.stdout.strip()
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"auto-publish 无法解析输出：{stdout[:200]}")


# ──────────────────────────────────────────────────────────────
# 记录 publish_log
# ──────────────────────────────────────────────────────────────

def record_publish(username: str, params: dict) -> None:
    """调用 xhs_publish.py record 将发布结果写入 publish_log。"""
    cmd = [
        _PYTHON, str(_ROOT / "utils" / "xhs_publish.py"), "record",
        "--username", username,
        "--trace-id", params["trace_id"],
        "--result-video", params.get("result_video", ""),
        "--title", params["title"],
    ]
    if params.get("trace_path"):
        cmd += ["--trace-path", params["trace_path"]]
    if params.get("post_id"):
        cmd += ["--post-id", params["post_id"]]
    if params.get("xsec_token"):
        cmd += ["--xsec-token", params["xsec_token"]]
    if params.get("tags"):
        cmd += ["--tags"] + params["tags"]

    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT))
    if r.returncode != 0:
        # 已存在记录时只警告，不中断
        if "已有记录" in r.stdout or "已有记录" in r.stderr:
            print(f"[pipeline] record: 已存在，跳过写入")
        else:
            raise RuntimeError(f"record 失败：{r.stderr.strip()}")
    else:
        print(r.stdout.strip())


# ──────────────────────────────────────────────────────────────
# 回填 social-media-feedback 到 trace
# ──────────────────────────────────────────────────────────────

def backfill_feedback(username: str, post_id: str) -> None:
    """将 publish_log 中该帖子的社交指标回填到对应 trace 文件。"""
    if not post_id:
        return
    try:
        from utils.backfill_social_feedback import backfill_by_post_ids
        backfill_by_post_ids(username, [post_id])
    except Exception as e:
        print(f"[pipeline] backfill 异常（不影响主流程）：{e}", file=sys.stderr)


# ──────────────────────────────────────────────────────────────
# 单条话题完整流程
# ──────────────────────────────────────────────────────────────

def run_one(username: str, topic_failures: dict) -> bool:
    """处理下一个话题，成功返回 True，失败返回 False。

    topic_failures: {title -> fail_count}，累计失败 2 次则跳过该话题。
    """
    refresh_topics(username)
    topic = pick_next_topic(username)
    if not topic:
        return False

    title = topic["title"]
    print(f"\n{'='*60}")
    print(f"[pipeline] 话题：{title}")
    print(f"[pipeline] 平台：{topic.get('platform','')}  use_count={topic.get('use_count',0)}")
    print(f"{'='*60}", flush=True)

    try:
        # Step 1: 生成视频
        va_result = generate_video(username, topic)
        if not va_result.get("success"):
            raise RuntimeError(f"视频生成失败：{va_result.get('error')}")
        print(f"[pipeline] 视频生成完成，trace_id={va_result.get('trace_id','')}")

        # Step 2: 发布
        params = auto_publish(username, va_result)
        post_id = params.get("post_id", "")
        print(f"[pipeline] 发布完成，post_id={post_id or '(待补全)'}")

        # Step 3: 写入 publish_log
        record_publish(username, params)
        print("[pipeline] publish_log 已更新")

        # Step 4: 回填 social-media-feedback 到 trace
        backfill_feedback(username, post_id)
        if post_id:
            print("[pipeline] trace 已回填 social-media-feedback")

        # Step 5: 标记话题 done，清除失败计数
        mark_topic(username, title, "done")
        topic_failures.pop(title, None)
        print(f"[pipeline] ✅ 完成：{title[:30]}")
        return True

    except Exception as e:
        print(f"[pipeline] ❌ 失败：{e}", file=sys.stderr)
        fail_count = topic_failures.get(title, 0) + 1
        topic_failures[title] = fail_count
        if fail_count >= 2:
            # 连续失败 2 次，跳过该话题
            mark_topic(username, title, "done")
            print(f"[pipeline] ⏭ 话题「{title[:20]}」失败 {fail_count} 次，已跳过", file=sys.stderr)
        else:
            # 第一次失败，回退为 unused 等下轮重试
            mark_topic(username, title, "unused")
            print(f"[pipeline] 话题失败第 {fail_count} 次，下次可重试", file=sys.stderr)
        return False


# ──────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="热点话题视频发布持续流水线")
    parser.add_argument("--username", required=True, help="workspace 用户名")
    parser.add_argument("--max", type=int, default=5, help="本轮最多发布条数（默认 5）")
    args = parser.parse_args()

    completed = 0
    failed_streak = 0   # 连续失败次数（同一 or 不同话题均计入）
    topic_failures: dict = {}  # {title -> fail_count}，用于跳过反复失败的话题

    print(f"[pipeline] 启动，username={args.username}，max={args.max}")

    while completed < args.max:
        ok = run_one(args.username, topic_failures)
        if ok:
            completed += 1
            failed_streak = 0
            print(f"[pipeline] 进度 {completed}/{args.max}\n")
        else:
            failed_streak += 1
            if failed_streak >= 5:
                print("[pipeline] 连续失败 5 次，终止。", file=sys.stderr)
                sys.exit(1)
            wait = 30 if failed_streak <= 2 else 60
            print(f"[pipeline] 失败，{wait}s 后重试（连续失败 {failed_streak}/5）")
            time.sleep(wait)

    print(f"\n[pipeline] 本轮结束，共完成 {completed}/{args.max} 个话题。")


if __name__ == "__main__":
    main()
