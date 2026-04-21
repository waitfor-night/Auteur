# Trace 加载与格式转换。主格式为 JSON（rounds 格式，如 ctx_xxx.json）。
# 约定：一条 trace = 一次完整用户交互；多 round 仅作为该 trace 内部结构，不拆成多条轨迹。

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple


# ---------- 文本格式解析（用于 .txt 及 JSON 转出后的轮次统计） ----------

ROUND_HEADER_PATTERN = re.compile(r"^##\s+Round\s+(\d+)", re.IGNORECASE | re.MULTILINE)
TRAJECTORY_BLOCK_HEADER = re.compile(r"^##\s+(?:Episode|Trajectory)\s+\d+", re.IGNORECASE | re.MULTILINE)


def _count_rounds(trajectory_text: str) -> int:
    """从一段轨迹文本中解析轮次数。"""
    if not trajectory_text.strip():
        return 0
    matches = list(ROUND_HEADER_PATTERN.finditer(trajectory_text))
    if not matches:
        return 0
    num_by_count = len(matches)
    num_by_max = max(int(m.group(1)) for m in matches)
    return max(num_by_count, num_by_max)


# ---------- JSON 加载（主路径：rounds 格式，一文件一 trace） ----------


def _format_plan_rounds(plan: dict) -> str:
    if not plan:
        return "（无计划内容）"
    return json.dumps(plan, ensure_ascii=False, indent=2)


def _format_actor_tools(tools) -> str:
    if not tools:
        return "（无 Actor 工具调用记录）"
    parts = []
    for t in tools:
        if isinstance(t, dict):
            name = t.get("name") or t.get("tool_name") or "?"
            args = t.get("arguments") or t.get("args") or t.get("input") or {}
            parts.append(f"- {name}({json.dumps(args, ensure_ascii=False) if args else ''})")
        else:
            parts.append(f"- {t}")
    return "\n".join(parts)


def _format_feedback_rounds(fb) -> str:
    if fb is None or (isinstance(fb, str) and not fb.strip()):
        return "（本轮无用户反馈）"
    return str(fb).strip()


def _rounds_json_to_text(data: dict) -> str:
    """将 rounds 格式的 JSON 转为一条轨迹文本（整条 trace，含多轮）。"""
    rounds = data.get("rounds") or []
    if not rounds:
        return ""
    lines = []
    for r in rounds:
        round_num = r.get("round", len(lines) // 5 + 1)
        lines.append(f"## Round {round_num}")

        skills = r.get("skill_loaded") or []
        if skills:
            lines.append("### Skills Loaded")
            for skill in skills:
                lines.append(f"#### {skill.get('name', '?')}")
                lines.append(skill.get("content", ""))

        lines.append("### Plan")
        lines.append(_format_plan_rounds(r.get("plan") or {}))

        lines.append("### Actor Tool Calls")
        lines.append(_format_actor_tools(r.get("actor_tools")))

        lines.append("### User Feedback")
        lines.append(_format_feedback_rounds(r.get("feedback")))

        satisfied = r.get("satisfied")
        if satisfied is not None:
            lines.append("### Satisfied")
            lines.append("True" if satisfied else "False")

        lines.append("")
    return "\n".join(lines).strip()


def _format_plan_history(hist: dict) -> str:
    plan = hist.get("plan") or {}
    final = plan.get("final_execution_plan") or plan.get("raw_model_output")
    if not final:
        return "（无计划内容）"
    if isinstance(final, list):
        return json.dumps(final, ensure_ascii=False, indent=2)
    if isinstance(final, dict):
        return json.dumps(final, ensure_ascii=False, indent=2)
    return str(final)


def _format_act_history(hist: dict) -> str:
    parts = []
    for a in hist.get("actions") or []:
        seg_id = a.get("segment_id", "?")
        rel = a.get("relevance_analysis") or {}
        exec_ = a.get("execution") or {}
        parts.append(
            f"- segment_id={seg_id} editing_required={rel.get('editing_required')} "
            f"editing_prompt={rel.get('editing_prompt', '')[:200]}... "
            f"status={exec_.get('status')} edited_file={exec_.get('edited_file_path')}"
        )
    for tc in hist.get("tool_calls") or []:
        parts.append(
            f"- tool={tc.get('tool_name')} duration={tc.get('duration')}s status={tc.get('status')}"
        )
    return "\n".join(parts) if parts else "（无执行记录）"


def _format_feedback_history(hist: dict) -> str:
    v = hist.get("verification")
    if v is None:
        return "（本轮无验证反馈）"
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return str(v)


def _history_json_to_text(data: dict) -> str:
    """将 history 格式的 JSON 转为一条轨迹文本。"""
    history = data.get("history") or []
    if not history:
        return ""
    lines = []
    for i, hist in enumerate(history):
        round_num = i + 1
        lines.append(f"## Round {round_num}")
        lines.append("### Plan")
        lines.append(_format_plan_history(hist))
        lines.append("### Act")
        lines.append(_format_act_history(hist))
        lines.append("### Feedback")
        lines.append(_format_feedback_history(hist))
        lines.append("")
    return "\n".join(lines).strip()


def _json_to_text(data: dict) -> str:
    """根据顶层字段判断 rounds / history 并转换为轨迹文本。"""
    if "rounds" in data and isinstance(data["rounds"], list):
        return _rounds_json_to_text(data)
    if "history" in data and isinstance(data["history"], list):
        return _history_json_to_text(data)
    return ""


def _json_to_num_rounds(data: dict) -> int:
    """从 JSON 得到总轮次数（用于 rounds 或 history）。"""
    if "rounds" in data and isinstance(data["rounds"], list):
        return data.get("total_rounds") or len(data["rounds"])
    if "history" in data and isinstance(data["history"], list):
        return len(data["history"])
    return 0


def load_trace_from_json(path: str) -> Tuple[str, int]:
    """
    从单个 JSON 文件加载**一条** trace（一次完整用户交互）。
    不按 round 拆成多条；返回 (整条 trace 的文本, 总轮次数)。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Trace file not found: {path}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        # 单文件内多条 trace 的 JSON：取第一条（或约定只支持单条）
        if not raw or not isinstance(raw[0], dict):
            return "", 0
        data = raw[0]
    else:
        data = raw
    text = _json_to_text(data)
    num_rounds = _json_to_num_rounds(data) or _count_rounds(text)
    return text, num_rounds


def load_traces_from_json(paths: List[str]) -> List[Tuple[str, int]]:
    """
    从多个 JSON 文件分别加载轨迹；每个文件 = 一条 trace = 一个 (text, num_rounds)。
    """
    out = []
    for p in paths:
        out.append(load_trace_from_json(p))
    return out


# ---------- 文本文件加载（兼容 .txt） ----------


def load_trace_file(trace_path: str) -> Tuple[str, int]:
    """
    从单个 trace 文本文件加载**一条**轨迹。
    约定格式：每轮以 "## Round N" 开头，内含 ### Plan / ### Act / ### Feedback。
    """
    path = Path(trace_path)
    if not path.is_file():
        raise FileNotFoundError(f"Trace file not found: {trace_path}")
    trajectory_text = path.read_text(encoding="utf-8").strip()
    if not trajectory_text:
        return "", 0
    num_rounds = _count_rounds(trajectory_text)
    return trajectory_text, num_rounds


def load_trace_files(trace_paths: List[str]) -> List[Tuple[str, int]]:
    """从多个 trace 文本文件分别加载轨迹，每个文件视为一条轨迹。"""
    return [load_trace_file(p) for p in trace_paths]


def load_trace_file_multi(trace_path: str) -> List[Tuple[str, int]]:
    """
    从单个 trace 文本文件中解析出多条轨迹（按 ## Episode N / ## Trajectory N 分段）。
    若无该标题则整份文件视为一条轨迹。
    """
    path = Path(trace_path)
    if not path.is_file():
        raise FileNotFoundError(f"Trace file not found: {trace_path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    parts = TRAJECTORY_BLOCK_HEADER.split(content)
    if len(parts) == 1 and not TRAJECTORY_BLOCK_HEADER.search(content):
        text = parts[0].strip()
        return [(text, _count_rounds(text))] if text else []
    result = []
    for block in parts:
        block = block.strip()
        if not block:
            continue
        num_rounds = _count_rounds(block)
        if num_rounds == 0:
            continue
        result.append((block, num_rounds))
    return result


# ---------- publish_log 真实反馈注入 ----------


def enrich_with_publish_feedback(trajectory_text: str, publish_entry: dict) -> str:
    """将 publish_log 里的真实小红书反馈追加到 trajectory_text 末尾。

    追加格式为一个独立章节，供 ExecutionLoss / PreferenceLoss 的 LLM 读取。
    publish_entry 为 publish_log.json 中的单条记录（dict）。
    """
    if not publish_entry:
        return trajectory_text

    stats = publish_entry.get("stats") or {}
    comments = stats.get("comments") or []
    status = publish_entry.get("status", "published")

    lines = [
        "",
        "## Real User Feedback (小红书发布后真实数据)",
        f"- 帖子标题：{publish_entry.get('title', '')}",
        f"- 发布状态：{status}",
        f"- 点赞数：{stats.get('liked_count', 0)}",
        f"- 收藏数：{stats.get('collected_count', 0)}",
        f"- 评论数：{stats.get('comment_count', 0)}",
        f"- 分享数：{stats.get('share_count', 0)}",
    ]

    if comments:
        lines.append("- 用户评论：")
        for c in comments:
            lines.append(f"  - [{c.get('create_time', '')}] {c.get('author', '')}: {c.get('content', '')}")
            for sub in c.get("sub_comments") or []:
                lines.append(f"    - [{sub.get('create_time', '')}] {sub.get('author', '')}: {sub.get('content', '')}")
    else:
        lines.append("- 用户评论：（暂无）")

    return trajectory_text.rstrip() + "\n" + "\n".join(lines)


# ---------- CLI：JSON 转 .txt ----------


def _cli_main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert trace JSON file(s) to optimizer text format (one file = one trace)."
    )
    ap.add_argument("json_files", nargs="+", metavar="JSON", help="Path(s) to trace JSON file(s).")
    ap.add_argument("-o", "--output", required=True, help="Output text file path.")
    ap.add_argument(
        "--multi",
        action="store_true",
        help="Write multiple trajectories with '## Episode N' separator (for --multi_in_file).",
    )
    args = ap.parse_args()
    all_parts = []
    for jpath in args.json_files:
        try:
            text, _ = load_trace_from_json(jpath)
            if text:
                all_parts.append(text)
        except Exception as e:
            print(f"Warning: skip {jpath}: {e}", file=sys.stderr)
    if not all_parts:
        raise SystemExit("No trajectory content produced from any file.")
    if args.multi and len(all_parts) > 1:
        out_lines = []
        for i, part in enumerate(all_parts, 1):
            out_lines.append(f"## Episode {i}")
            out_lines.append(part)
            out_lines.append("")
        body = "\n".join(out_lines).strip()
    else:
        body = "\n\n".join(all_parts)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    print(f"Wrote {len(all_parts)} trajectory(ies) to {out_path}")


if __name__ == "__main__":
    _cli_main()
