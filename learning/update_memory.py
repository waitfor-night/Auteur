#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import textgrad as tg

from utils.trajectory_io import load_traces_from_json
from utils.user_workspace import ensure_user_memory

from learning.common import resolve_trace_paths
from learning.optimization.preference_loss import PreferenceLoss
from learning.tgd_engine import get_engine_from_builtin


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


def parse_args():
    p = argparse.ArgumentParser(description="Update workspace/<username>/memory/memory.md from ctx*.json trajectories.")
    p.add_argument("--username", required=True, help="用户目录名，对应 workspace/<username>/")
    p.add_argument(
        "--trace",
        nargs="+",
        required=True,
        metavar="FILE_OR_DIR",
        help="与 optimize_meta_skill 相同：文件或目录；目录则递归收集 ctx*.json",
    )
    p.add_argument(
        "--engine",
        default="kimi-k2-turbo-preview",
        help="与 learning/optimize_meta_skill 内置模型名一致",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要读取的 ctx 路径数量，不写文件",
    )
    return p.parse_args()


def main():
    args = parse_args()
    try:
        trace_paths = resolve_trace_paths(args.trace)
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    if not trace_paths:
        raise SystemExit("No ctx*.json found under --trace paths.")

    if args.dry_run:
        print(f"[dry-run] would load {len(trace_paths)} ctx file(s), username={args.username}")
        for p in trace_paths[:20]:
            print(f"  {p}")
        if len(trace_paths) > 20:
            print(f"  ... and {len(trace_paths) - 20} more")
        return

    ensure_user_memory(_PROJECT_ROOT, args.username)
    memory_md = _PROJECT_ROOT / "workspace" / args.username.strip() / "memory" / "memory.md"
    current = memory_md.read_text(encoding="utf-8")

    trace_path_strs = [str(p) for p in trace_paths]
    trajectories = load_traces_from_json(trace_path_strs)
    if not trajectories:
        raise SystemExit("No trajectories loaded from ctx*.json.")

    engine = get_engine_from_builtin(args.engine)
    if engine is not None:
        tg.set_backward_engine(engine, override=True)
    else:
        tg.set_backward_engine(args.engine, override=True)

    user_memory = tg.Variable(
        current,
        requires_grad=True,
        role_description="user memory document: preferences, task/video habits, and interaction correction summaries (three top-level sections)",
    )
    optimizer = tg.TGD(parameters=[user_memory], engine=engine if engine is not None else None)

    for trajectory_text, num_rounds in trajectories:
        loss_fn = PreferenceLoss(
            trajectory_text=trajectory_text,
            num_rounds=num_rounds,
        )
        loss = loss_fn(user_memory)
        loss.backward()

    optimizer.step()

    out = _strip_md_fence(user_memory.value)
    if not out:
        raise SystemExit("TGD produced empty memory content")
    memory_md.write_text(out + "\n", encoding="utf-8")
    print(f"Updated memory written to: {memory_md}")


if __name__ == "__main__":
    main()
