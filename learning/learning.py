#!/usr/bin/env python3
"""
同时优化 meta-skill 和用户 memory。

用法示例：
    python -m learning.learning \\
        --meta_skill skills/SKILL_doc_rigorous.md \\
        --username doc_rigorous \\
        --trace workspace/doc_rigorous/context \\
        --engine kimi-k2-turbo-preview

meta-skill 输出默认覆盖 --meta_skill；memory 路径默认从 --username 推导为
workspace/<username>/memory/memory.md。均可通过 --meta_skill_output /
--memory_output 显式指定。

用 --skip_meta_skill 或 --skip_memory 可跳过其中一个优化步骤。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json

import textgrad as tg

from utils.trajectory_io import load_traces_from_json, enrich_with_publish_feedback
from utils.xhs_log_io import get_entry_by_timestamp
from learning.common import resolve_trace_paths
from learning.tgd_engine import get_engine_from_builtin
from learning.optimization.execution_loss import ExecutionLoss
from learning.optimization.preference_loss import PreferenceLoss


_MEMORY_TEMPLATE = """\
# 用户偏好

# 任务与成片习惯

# 交互纠偏摘要
"""


def _ensure_memory(memory_path: Path) -> None:
    """若 memory.md 不存在则用三章节模板初始化。"""
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    if not memory_path.is_file():
        memory_path.write_text(_MEMORY_TEMPLATE, encoding="utf-8")


def _filter_paths_by_username(paths: list, username: str) -> list:
    """保留 ctx*.json 中 username 字段匹配的文件。"""
    kept = []
    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if data.get("username") == username:
                kept.append(p)
        except Exception:
            pass
    return kept


def _strip_md_fence(text: str) -> str:
    out = str(text).strip()
    if out.startswith("```"):
        lines = out.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        out = "\n".join(lines).strip()
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="同时优化 meta-skill 和用户 memory（单次 TGD step，多条轨迹聚合梯度）。",
    )
    p.add_argument(
        "--meta_skill",
        required=True,
        help="meta-skill MD 文件路径（如 skills/SKILL_doc_rigorous.md）。",
    )
    p.add_argument(
        "--username",
        default=None,
        help="用户名；memory 路径自动推导为 workspace/<username>/memory/memory.md，"
             "同时过滤 ctx*.json 中 username 字段不匹配的文件。与 --memory 二选一，--memory 优先。",
    )
    p.add_argument(
        "--memory",
        default=None,
        help="显式指定 memory.md 路径，优先于 --username 推导。",
    )
    p.add_argument(
        "--trace",
        nargs="+",
        required=True,
        metavar="FILE_OR_DIR",
        help="ctx*.json 文件或目录（目录则递归收集）。",
    )
    p.add_argument(
        "--meta_skill_output",
        default=None,
        help="优化后 meta-skill 写入路径。默认覆盖 --meta_skill。",
    )
    p.add_argument(
        "--memory_output",
        default=None,
        help="优化后 memory 写入路径。默认覆盖读取的 memory.md。",
    )
    p.add_argument(
        "--engine",
        default="kimi-k2-turbo-preview",
        help="LLM engine 名称（内置：doubao-seed-2-0-pro-260215 / deepseek-chat / kimi-k2-turbo-preview）。",
    )
    p.add_argument(
        "--skip_meta_skill",
        action="store_true",
        help="跳过 meta-skill 优化，只更新 memory。",
    )
    p.add_argument(
        "--skip_memory",
        action="store_true",
        help="跳过 memory 更新，只优化 meta-skill。",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # ── 路径校验 ──────────────────────────────────────────────────
    meta_path = Path(args.meta_skill)
    if not meta_path.is_file():
        raise SystemExit(f"Meta-skill file not found: {meta_path}")

    memory_path: Path | None = None
    if not args.skip_memory:
        if args.memory:
            memory_path = Path(args.memory)
        elif args.username:
            memory_path = _ROOT / "workspace" / args.username.strip() / "memory" / "memory.md"
        else:
            raise SystemExit("--memory 或 --username 至少提供一个（或使用 --skip_memory 跳过）。")
        _ensure_memory(memory_path)

    try:
        trace_paths = resolve_trace_paths(args.trace)
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    if not trace_paths:
        raise SystemExit("未找到任何 ctx*.json 文件，请检查 --trace 路径。")

    # ── 按 username 过滤轨迹文件（文件内容中的 username 字段） ────────
    if args.username:
        before = len(trace_paths)
        trace_paths = _filter_paths_by_username(trace_paths, args.username)
        print(f"[learning] username 过滤: {before} → {len(trace_paths)} 条 ctx 文件", flush=True)
        if not trace_paths:
            raise SystemExit(f"过滤后无匹配 username={args.username!r} 的轨迹文件。")

    # ── 加载轨迹（两个优化步骤共用） ────────────────────────────────
    trajectories = load_traces_from_json([str(p) for p in trace_paths])
    if not trajectories:
        raise SystemExit("轨迹加载失败，请检查 ctx*.json 内容。")
    print(f"[learning] 已加载 {len(trajectories)} 条轨迹", flush=True)

    # ── 注入 publish_log 真实用户反馈（若有） ──────────────────────
    if args.username:
        enriched = 0
        new_trajectories = []
        for (traj_text, num_rounds), path in zip(trajectories, trace_paths):
            # ctx_<timestamp>_<hash>.json → 提取 timestamp
            ts = Path(path).stem.split("_")[1] if "_" in Path(path).stem else ""
            entry = get_entry_by_timestamp(args.username, ts) if ts else None
            if entry:
                traj_text = enrich_with_publish_feedback(traj_text, entry)
                enriched += 1
            new_trajectories.append((traj_text, num_rounds))
        #新列表
        trajectories = new_trajectories
        if enriched:
            print(f"[learning] 已注入 {enriched} 条 publish_log 真实反馈", flush=True)

    # ── 初始化 engine ─────────────────────────────────────────────
    engine = get_engine_from_builtin(args.engine)
    if engine is not None:
        tg.set_backward_engine(engine, override=True)
    else:
        tg.set_backward_engine(args.engine, override=True)

    # ── Step 1：优化 meta-skill ───────────────────────────────────
    if not args.skip_meta_skill:
        print("[learning] 开始优化 meta-skill ...", flush=True)
        meta_skill = tg.Variable(
            meta_path.read_text(encoding="utf-8"),
            requires_grad=True,
            role_description="meta-skill document for the Planner agent, guide the agent to generate the video execution plan",
        )
        meta_optimizer = tg.TGD(parameters=[meta_skill], engine=engine if engine is not None else None)
        for trajectory_text, num_rounds in trajectories:
            loss = ExecutionLoss(trajectory_text=trajectory_text, num_rounds=num_rounds)(meta_skill)
            loss.backward()
        meta_optimizer.step()

        meta_out = Path(args.meta_skill_output) if args.meta_skill_output else meta_path
        meta_out.parent.mkdir(parents=True, exist_ok=True)
        meta_out.write_text(meta_skill.value, encoding="utf-8")
        print(f"[learning] meta-skill 已写入: {meta_out}", flush=True)

    # ── Step 2：更新 memory ──────────────────────────────────────
    if not args.skip_memory:
        print("[learning] 开始更新 memory ...", flush=True)
        user_memory = tg.Variable(
            memory_path.read_text(encoding="utf-8"),
            requires_grad=True,
            role_description="user memory document: preferences, task/video habits, and interaction correction summaries (three top-level sections)",
        )
        mem_optimizer = tg.TGD(parameters=[user_memory], engine=engine if engine is not None else None)
        for trajectory_text, num_rounds in trajectories:
            loss = PreferenceLoss(trajectory_text=trajectory_text, num_rounds=num_rounds)(user_memory)
            loss.backward()
        mem_optimizer.step()

        mem_content = _strip_md_fence(user_memory.value)
        if not mem_content:
            raise SystemExit("TGD 返回空 memory，已中止写入。")
        mem_out = Path(args.memory_output) if args.memory_output else memory_path
        mem_out.parent.mkdir(parents=True, exist_ok=True)
        mem_out.write_text(mem_content + "\n", encoding="utf-8")
        print(f"[learning] memory 已写入: {mem_out}", flush=True)


if __name__ == "__main__":
    main()
