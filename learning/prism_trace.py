"""
learning/prism_trace.py — 将单条 ep_*.json 文件折射为三视图 PrismTrace。

Prism-Trace：同一条执行轨迹经棱镜分解为三束独立优化信号：
    execution_view   → ExecutionLoss        → 优化 meta-skill
    preference_view  → PreferenceLoss       → 优化 memory（沙盒模式；自动运营为 None）
    audience_view    → ContentStrategyLoss  → 优化 content_strategy（自动运营；沙盒为 None）

两种实验场景下的 PrismTrace 状态：
    沙盒实验      execution ✅  preference ✅  audience ❌
    自动运营实验  execution ✅  preference ❌  audience ✅
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Planner 工具名集合
# tool_calls[] 里不在此集合内的全部归为 Actor 工具
# ─────────────────────────────────────────────────────────────────────────────

PLANNER_TOOLS: frozenset[str] = frozenset({
    "load_skill_tool",
    "submit_multi_stage_plan",
    "get_multi_stage_plan_summary_tool",
    "get_current_stage_plan_tool",
    "get_stage_outputs_tool",
    "set_stage_outputs_tool",
})


# ─────────────────────────────────────────────────────────────────────────────
# PrismTrace
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PrismTrace:
    episode_id: str
    num_rounds: int
    execution_view: str            # 永远有值
    preference_view: Optional[str] # None → 自动运营模式，跳过 PreferenceLoss
    audience_view: Optional[str]   # None → 沙盒模式，跳过 ContentStrategyLoss


# ─────────────────────────────────────────────────────────────────────────────
# ExecutionView：提取"怎么做"
# 来源：plan 结构 + Planner/Actor 工具序列 + 错误详情
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_tool_line(tc: dict) -> str:
    name = tc.get("tool_name", "?")
    dur = tc.get("duration", 0.0)
    status = tc.get("status", "?")
    err = tc.get("error_msg")
    line = f"  {name} | {dur:.1f}s | {status}"
    if err:
        line += f"\n    ERROR: {err}"
    return line


def refract_execution(ep: dict) -> str:
    lines: list[str] = []
    meta = ep.get("meta_info") or {}

    instruction = (meta.get("user_instruction") or "")[:300]
    lines.append(f"Instruction: {instruction}")
    lines.append(f"Status: {meta.get('status', '')}")
    lines.append("")

    for hist in ep.get("history") or []:
        i = hist.get("iteration_id", "?")
        lines.append(f"=== Round {i} ===")

        # Plan 结构
        plan = hist.get("plan") or {}
        msp = plan.get("original_multi_stage_plan") or {}
        stages = msp.get("stages") or []
        if stages:
            lines.append(f"Plan ({len(stages)} stages):")
            for s in stages:
                lines.append(
                    f"  [{s.get('stage_id', '?')}] {s.get('stage_name', '')} "
                    f"skill={s.get('skill_type', '')} status={s.get('status', '')}"
                )
        lines.append("")

        # 工具序列按 Planner / Actor 分组
        tool_calls = hist.get("tool_calls") or []
        planner_calls = [t for t in tool_calls if t.get("tool_name") in PLANNER_TOOLS]
        actor_calls   = [t for t in tool_calls if t.get("tool_name") not in PLANNER_TOOLS]

        lines.append("[Planner]")
        lines.extend(_fmt_tool_line(t) for t in planner_calls) if planner_calls else lines.append("  (none)")
        lines.append("")

        lines.append("[Actor]")
        lines.extend(_fmt_tool_line(t) for t in actor_calls) if actor_calls else lines.append("  (none)")
        lines.append("")

        # 错误汇总：便于 TGD 快速定位失败点
        errors = [t for t in tool_calls if t.get("status") == "error"]
        if errors:
            lines.append(f"Errors ({len(errors)}):")
            for e in errors:
                lines.append(f"  - {e.get('tool_name')}: {e.get('error_msg', '')}")
            lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# PreferenceView：提取"制作者说了什么"
# 来源：plan 结构 + 工具序列（仅名称）+ feedback + satisfied
# 包含执行上下文，使 feedback 文本具备可解释性
# 自动运营模式下 feedback/satisfied 均为 null → 返回 None
# ─────────────────────────────────────────────────────────────────────────────

def refract_preference(ep: dict) -> Optional[str]:
    history = ep.get("history") or []
    rounds_with_feedback = [
        h for h in history
        if h.get("feedback") is not None or h.get("satisfied") is not None
    ]
    if not rounds_with_feedback:
        return None

    lines: list[str] = []
    meta = ep.get("meta_info") or {}
    instruction = (meta.get("user_instruction") or "")[:300]
    lines.append(f"Instruction: {instruction}")
    lines.append("")

    for h in rounds_with_feedback:
        i = h.get("iteration_id", "?")
        lines.append(f"=== Round {i} ===")

        # Plan 结构（与 ExecutionView 一致，提供 feedback 的参照背景）
        plan = h.get("plan") or {}
        msp = plan.get("original_multi_stage_plan") or {}
        stages = msp.get("stages") or []
        if stages:
            lines.append(f"Plan ({len(stages)} stages):")
            for s in stages:
                lines.append(
                    f"  [{s.get('stage_id', '?')}] {s.get('stage_name', '')} "
                    f"skill={s.get('skill_type', '')} status={s.get('status', '')}"
                )
        lines.append("")

        # 工具序列：仅名称，不带入参/返回值
        tool_calls = h.get("tool_calls") or []
        planner_calls = [t for t in tool_calls if t.get("tool_name") in PLANNER_TOOLS]
        actor_calls   = [t for t in tool_calls if t.get("tool_name") not in PLANNER_TOOLS]

        if planner_calls:
            lines.append("[Planner]")
            lines.append("  " + " → ".join(t.get("tool_name", "?") for t in planner_calls))
            lines.append("")
        if actor_calls:
            lines.append("[Actor]")
            lines.append("  " + " → ".join(t.get("tool_name", "?") for t in actor_calls))
            lines.append("")

        # 错误简报（feedback 可能在指正这些错误）
        errors = [t for t in tool_calls if t.get("status") == "error"]
        if errors:
            lines.append(f"Errors: {', '.join(t.get('tool_name', '?') for t in errors)}")
            lines.append("")

        # 制作者反馈（核心信号）
        feedback = h.get("feedback")
        satisfied = h.get("satisfied")
        if feedback is not None:
            lines.append(f"Feedback: {feedback}")
        if satisfied is not None:
            lines.append(f"Satisfied: {satisfied}")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AudienceView：提取"受众怎么反应"
# 来源：social-media-feedback（多平台并排）
# 沙盒模式下无社媒数据 → 返回 None
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_stats_history(history: list[dict]) -> list[str]:
    """将 stats_history 格式化为增长趋势摘要。"""
    if not history:
        return []
    if len(history) == 1:
        return ["  Snapshots: 1 (no growth data)"]

    first, last = history[0], history[-1]
    ts0, ts1 = first.get("ts", ""), last.get("ts", "")
    metrics = [k for k in last if k != "ts"]
    lines = [f"  Growth ({len(history)} snapshots, {ts0} → {ts1}):"]
    for m in metrics:
        v0 = first.get(m) or 0
        v1 = last.get(m) or 0
        try:
            delta = int(v1) - int(v0)
            lines.append(f"    {m}: {v0} → {v1} (Δ{delta:+d})")
        except (TypeError, ValueError):
            lines.append(f"    {m}: {v0} → {v1}")
    return lines


def refract_audience(ep: dict) -> Optional[str]:
    smf = ep.get("social-media-feedback")
    if not smf:
        return None

    platforms = smf.get("publish_platform") or []
    if not platforms:
        return None

    meta = ep.get("meta_info") or {}
    instruction = (meta.get("user_instruction") or "")[:200]

    lines: list[str] = []
    lines.append(f"Topic: {instruction}")
    lines.append(f"Platforms: {', '.join(platforms)}")
    lines.append("")

    for platform in platforms:
        pdata = smf.get(platform) or {}
        lines.append(f"=== {platform} ===")
        if not pdata:
            lines.append("  (no data)")
            lines.append("")
            continue

        lines.append(f"Title: {pdata.get('title', '')}")
        tags = pdata.get("tags") or []
        if tags:
            lines.append(f"Tags: {', '.join(tags)}")
        lines.append(f"Publish Time: {pdata.get('publish_time', '')}")

        stats = pdata.get("stats") or {}
        if stats:
            lines.append("Stats (latest):")
            for k, v in stats.items():
                lines.append(f"  {k}: {v}")

        lines.extend(_fmt_stats_history(pdata.get("stats_history") or []))
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 入口函数
# ─────────────────────────────────────────────────────────────────────────────

def decompose(ep: dict) -> PrismTrace:
    """从已解析的 ep dict 构建 PrismTrace。"""
    return PrismTrace(
        episode_id=ep.get("episode_id", ""),
        num_rounds=len(ep.get("history") or []),
        execution_view=refract_execution(ep),
        preference_view=refract_preference(ep),
        audience_view=refract_audience(ep),
    )


def load_prism_trace(ep_path: str | Path) -> PrismTrace:
    """从 ep_*.json 文件路径加载并折射为 PrismTrace。"""
    path = Path(ep_path)
    ep = json.loads(path.read_text(encoding="utf-8"))
    return decompose(ep)


def load_prism_traces(trace_dir: str | Path) -> list[PrismTrace]:
    """递归收集目录下所有 ep_*.json 并折射为 PrismTrace 列表。"""
    paths = sorted(Path(trace_dir).rglob("ep_*.json"))
    return [load_prism_trace(p) for p in paths]
