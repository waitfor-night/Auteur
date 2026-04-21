#!/usr/bin/env python3
"""
五项指标计算：Target Tool Recall、Trace Constraint Violation Rate、Meta-skill 膨胀率、
工作流命中率（Trajectory Edit Distance）、参数隐式对齐度（Auto-Recall Rate）。
按用户读 workspace/<username>/ 与对应用户 meta-skill 文件，输出 CSV/JSON。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SANDBOX_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _SANDBOX_ROOT.parent


def _levenshtein_distance(a: List[str], b: List[str]) -> int:
    """编辑距离（Levenshtein）：将 a 变为 b 所需的最少单字符编辑次数。此处用于 tool 序列。"""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    dp: List[List[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[n][m]


def load_role_config(role_id: str) -> Dict[str, Any]:
    """加载角色配置。"""
    from sandbox.user_simulator import load_roles_config
    roles = load_roles_config()
    return roles.get(role_id) or {}


def get_tool_sequence_from_trace(trace_path: Path) -> List[str]:
    """从 ep_*.json 的 history[].tool_calls 提取按序的 tool_name 列表。"""
    if not trace_path.is_file():
        return []
    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    history = data.get("history") or []
    out = []
    for h in history:
        for tc in h.get("tool_calls") or []:
            name = tc.get("tool_name")
            if name:
                out.append(name)
    return out


def get_tool_sequence_from_plan(plan: Dict[str, Any]) -> List[str]:
    """从 Plan（timeline + 隐含）推导可能涉及的 tool 顺序（启发式）。"""
    # Plan 本身不直接记录 Planner 的 tool_calls，仅当无 trace 时用 timeline 推断
    tools = []
    if plan.get("task_metadata"):
        tools.append("load_skill_tool")  # 通常先加载 skill
    timeline = plan.get("timeline") or []
    has_generate = any(t.get("action_trigger") == "GENERATE" for t in timeline)
    if has_generate:
        tools.append("submit_video_execution_plan")
    return tools


def compute_recall(tool_sequence: List[str], preferred_tools: List[str]) -> float:
    """Preferred tools 在 tool_sequence 中出现的比例。"""
    if not preferred_tools:
        return 1.0
    seen = set(tool_sequence)
    hit = sum(1 for t in preferred_tools if t in seen)
    return hit / len(preferred_tools)


def compute_violation_rate(
    tool_sequence: List[str],
    constraint_rules: List[str],
    preferred_tools: List[str],
) -> float:
    """
    根据 constraint_rules 检查顺序/必选约束，违反条数 / 总规则数。
    简单实现：规则为「A 必须在 B 之前」时，检查 A 在 sequence 中的位置 < B 的位置。
    """
    if not constraint_rules:
        return 0.0
    violations = 0
    pos = {t: i for i, t in enumerate(tool_sequence)}
    for rule in constraint_rules:
        # 简单启发：若规则含 "image_understanding_tool 必须在 ... GENERATE 之前"
        if "image_understanding_tool" in rule and "GENERATE" in rule or "之前" in rule:
            if "image_understanding_tool" in preferred_tools or "image_understanding_tool" in rule:
                idx_img = pos.get("image_understanding_tool", -1)
                # GENERATE 不在 tool_calls 里，在 plan timeline；这里简化为「有 image_understanding 且 sequence 非空」则满足
                if idx_img < 0 and any("generate" in t.lower() or "submit" in t.lower() for t in tool_sequence):
                    violations += 1
        elif "merge_video_tool" in rule and "之后" in rule:
            idx_merge = pos.get("merge_video_tool", -1)
            if idx_merge >= 0:
                # 期望 merge 在最后：若 merge 后还有别的则违反
                if idx_merge < len(tool_sequence) - 1:
                    violations += 1
            # 若规则要求 merge 在生成之后，且 sequence 里有 merge 和 generate，检查顺序
            gen_tools = [t for t in tool_sequence if "generate" in t.lower() or "video" in t.lower()]
            if gen_tools and idx_merge >= 0:
                last_gen = max(pos.get(t, -1) for t in gen_tools)
                if last_gen > idx_merge:
                    violations += 1
    return min(1.0, violations / len(constraint_rules))


def compute_meta_skill_length(meta_skill_path: Path) -> int:
    """Meta-skill 文件字符数。"""
    if not meta_skill_path.is_file():
        return 0
    return len(meta_skill_path.read_text(encoding="utf-8"))


def get_golden_tool_sequence(role_config: Dict[str, Any]) -> List[str]:
    """从 role 配置取得黄金工具序列（用于流程编辑距离）。优先 golden_tool_sequence，否则 preferred_tools。"""
    golden = role_config.get("golden_tool_sequence") or []
    if golden:
        return list(golden)
    return list(role_config.get("preferred_tools") or [])


def compute_trajectory_edit_distance(actual: List[str], golden: List[str]) -> int:
    """实际工具序列与黄金序列的编辑距离。"""
    return _levenshtein_distance(actual, golden)


def compute_workflow_match_rate(actual: List[str], golden: List[str]) -> float:
    """工作流命中率：1 - normalized_distance。完全一致为 1.0。"""
    if not golden and not actual:
        return 1.0
    max_len = max(len(golden), len(actual), 1)
    dist = compute_trajectory_edit_distance(actual, golden)
    return max(0.0, 1.0 - (dist / max_len))


def get_params_from_trace(trace_path: Path, preferred_keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """从 trace 的 tool_calls[].inputs.kwargs 中提取与 preferred 相关的参数（用于参数对齐）。"""
    if not trace_path.is_file():
        return {}
    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, Any] = {}
    for h in data.get("history") or []:
        for tc in h.get("tool_calls") or []:
            inputs = tc.get("inputs") or {}
            kwargs = inputs.get("kwargs") or {}
            if preferred_keys:
                for k in preferred_keys:
                    if k in kwargs:
                        out[k] = kwargs[k]
            else:
                for k, v in kwargs.items():
                    if k in ("cfg_scale", "motion_bucket", "motion_score", "negative_prompt_suffix", "aspect_ratio"):
                        out[k] = v
    return out


def compute_auto_recall_rate(actual_params: Dict[str, Any], preferred_params: Dict[str, Any]) -> float:
    """参数隐式对齐度：偏好参数在 actual 中被自动带上的比例。数值型允许容差。"""
    if not preferred_params:
        return 1.0
    hits = 0
    for key, expected in preferred_params.items():
        if key not in actual_params:
            continue
        actual_val = actual_params[key]
        if actual_val is None:
            continue
        if isinstance(expected, (int, float)) and isinstance(actual_val, (int, float)):
            if abs(float(expected) - float(actual_val)) <= 1e-6:
                hits += 1
        elif str(actual_val).strip() == str(expected).strip():
            hits += 1
    return hits / len(preferred_params)


def eval_one_round(
    tool_sequence: List[str],
    role_config: Dict[str, Any],
    meta_skill_path: Optional[Path] = None,
    trace_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """单轮/单 trace 的五项指标：recall、violation_rate、meta_skill_length、workflow_match_rate、auto_recall_rate。"""
    preferred = role_config.get("preferred_tools") or []
    constraints = role_config.get("constraint_rules") or []
    recall = compute_recall(tool_sequence, preferred)
    violation = compute_violation_rate(tool_sequence, constraints, preferred)
    meta_len = compute_meta_skill_length(meta_skill_path) if meta_skill_path else 0
    golden = get_golden_tool_sequence(role_config)
    workflow_match = compute_workflow_match_rate(tool_sequence, golden)
    preferred_params = role_config.get("preferred_params") or {}
    auto_recall = 1.0
    if preferred_params and trace_path:
        actual_params = get_params_from_trace(trace_path, list(preferred_params.keys()))
        auto_recall = compute_auto_recall_rate(actual_params, preferred_params)
    return {
        "recall": round(recall, 4),
        "violation_rate": round(violation, 4),
        "meta_skill_length": meta_len,
        "workflow_match_rate": round(workflow_match, 4),
        "auto_recall_rate": round(auto_recall, 4),
        "tool_sequence": tool_sequence,
    }


def collect_traces_and_context(
    workspace_user_dir: Path,
) -> Tuple[List[Path], List[Dict[str, Any]], List[Path]]:
    """
    收集该用户目录下的 trace 文件（ep_*.json）与 context 文件（ctx_*.json），
    以及按时间排序的 round 对应的 plan（从 context 的 rounds 取）。
    返回 (trace_paths, plans_per_round, []) 其中 plans 从 ctx 的 rounds 来。
    """
    trace_dir = workspace_user_dir / "trace"
    context_dir = workspace_user_dir / "context"
    trace_paths = sorted(trace_dir.glob("ep_*.json"), key=lambda p: p.stat().st_mtime) if trace_dir.is_dir() else []
    ctx_paths = sorted(context_dir.glob("ctx_*.json"), key=lambda p: p.stat().st_mtime) if context_dir.is_dir() else []
    plans = []
    for ctx_path in ctx_paths:
        try:
            data = json.loads(ctx_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in data.get("rounds") or []:
            plan = r.get("plan")
            if plan:
                plans.append(plan)
    return trace_paths, plans, ctx_paths


def run_eval(
    username: str,
    role_id: Optional[str] = None,
    meta_skill_path: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    按用户评估：读 workspace/<username>/ 下 trace 与 context，对应用户 meta-skill 文件，输出每轮指标。
    """
    if role_id is None:
        role_id = username
    if workspace_root is None:
        workspace_root = _PROJECT_ROOT / "workspace"
    user_dir = workspace_root / username
    role_config = load_role_config(role_id)
    if meta_skill_path is None:
        meta_skill_path = _PROJECT_ROOT / "skills" / "meta-skill" / f"SKILL_opt_{username}.md"
        if not meta_skill_path.is_file():
            meta_skill_path = _PROJECT_ROOT / "skills" / "meta-skill" / f"SKILL_{username}.md"
        if not meta_skill_path.is_file():
            meta_skill_path = _PROJECT_ROOT / "skills" / "meta-skill" / "SKILL.md"

    trace_paths, plans, _ = collect_traces_and_context(user_dir)
    results = []
    # 优先用 trace 得到 tool 序列，并传入 trace_path 以计算 auto_recall_rate
    for i, tp in enumerate(trace_paths):
        seq = get_tool_sequence_from_trace(tp)
        meta_path = meta_skill_path if i == len(trace_paths) - 1 else None  # 最后一轮用当前 meta 长度
        results.append({
            "epoch": i + 1,
            "username": username,
            **eval_one_round(seq, role_config, meta_path, trace_path=tp),
        })
    if not results and plans:
        for i, plan in enumerate(plans):
            seq = get_tool_sequence_from_plan(plan)
            results.append({
                "epoch": i + 1,
                "username": username,
                **eval_one_round(seq, role_config, meta_skill_path, trace_path=None),
            })
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="沙箱五项指标：Recall、Violation、MetaSkillLength、WorkflowMatchRate、AutoRecallRate")
    parser.add_argument("--username", required=True, help="用户名")
    parser.add_argument("--role_id", default=None, help="Role ID，默认与 username 相同")
    parser.add_argument("--meta_skill", default=None, help="Meta-skill 文件路径（可选）")
    parser.add_argument("--output", default=None, help="输出 JSON/CSV 路径（默认 stdout JSON）")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    args = parser.parse_args()

    meta_path = Path(args.meta_skill) if args.meta_skill else None
    results = run_eval(username=args.username, role_id=args.role_id, meta_skill_path=meta_path)
    out = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Wrote {len(results)} records to {args.output}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(_PROJECT_ROOT))
    raise SystemExit(main())
