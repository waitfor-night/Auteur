#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import textgrad as tg

from utils.trajectory_io import (
    load_trace_file_multi,
    load_trace_files,
    load_traces_from_json,
)
from learning.common import resolve_trace_paths
from learning.optimization.execution_loss import ExecutionLoss
from learning.tgd_engine import get_engine_from_builtin


def parse_args():
    p = argparse.ArgumentParser(
        description="Optimize meta-skill from one or more trace files (single TGD step; multiple trajectories aggregate gradients).",
    )
    p.add_argument(
        "--meta_skill",
        required=True,
        default="skills/meta-skill/SKILL.md",
        help="Path to the meta-skill MD file to optimize.",
    )
    p.add_argument(
        "--trace",
        nargs="+",
        required=True,
        metavar="FILE_OR_DIR",
        help="Path(s) to trace file(s) and/or directory(ies). If a directory is given, all ctx*.json under it are used. Each file = one trajectory, unless --multi_in_file.",
    )
    p.add_argument(
        "--multi_in_file",
        action="store_true",
        help="If set, each --trace file may contain multiple trajectories split by '## Episode N' or '## Trajectory N'.",
    )
    p.add_argument(
        "--output",
        default="skills/meta-skill/SKILL_opt.md",
        help="Path to write optimized meta-skill. Default: overwrite --meta_skill.",
    )
    p.add_argument(
        "--engine",
        default="kimi-k2-turbo-preview",
        help='Engine: built-in name in script (doubao-2.0, doubao 2.0, deepseek-chat, kimi-k2-turbo-preview, key from env) or TextGrad name (e.g. gpt-4o).',
    )
    p.add_argument(
        "--cache",
        action="store_true",
        help="Enable cache for LLM calls (only when engine starts with experimental:).",
    )
    return p.parse_args()


def main():
    args = parse_args()

    meta_path = Path(args.meta_skill)
    try:
        trace_paths = resolve_trace_paths(args.trace)
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    if not meta_path.is_file():
        raise SystemExit(f"Meta-skill file not found: {meta_path}")
    if not trace_paths:
        raise SystemExit(
            "No trace files found. Use --trace with file path(s) and/or directory(ies) containing ctx*.json."
        )

    meta_content = meta_path.read_text(encoding="utf-8")

    trace_path_strs = [str(p) for p in trace_paths]
    if all(p.suffix.lower() == ".json" for p in trace_paths):
        trajectories = load_traces_from_json(trace_path_strs)
    elif args.multi_in_file:
        trajectories = []
        for p in trace_paths:
            trajectories.extend(load_trace_file_multi(str(p)))
    else:
        trajectories = load_trace_files(trace_path_strs)

    if not trajectories:
        raise SystemExit("No trajectories loaded. Check --trace paths and (if used) --multi_in_file format.")

    engine = get_engine_from_builtin(args.engine)
    if engine is not None:
        tg.set_backward_engine(engine, override=True)
    else:
        kwargs = {}
        if args.engine.startswith("experimental:") and args.cache:
            kwargs["cache"] = True
        tg.set_backward_engine(args.engine, override=True, **kwargs)

    meta_skill = tg.Variable(
        meta_content,
        requires_grad=True,
        role_description="meta-skill document for the Planner agent, guide the agent to generate the video execution plan",
    )
    optimizer = tg.TGD(parameters=[meta_skill], engine=engine if engine is not None else None)
    for trajectory_text, num_rounds in trajectories:
        loss_fn = ExecutionLoss(
            trajectory_text=trajectory_text,
            num_rounds=num_rounds,
        )
        loss = loss_fn(meta_skill)
        loss.backward()
    optimizer.step()

    out_path = Path(args.output) if args.output else meta_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(meta_skill.value, encoding="utf-8")
    print(f"Optimized meta-skill written to: {out_path}")


if __name__ == "__main__":
    main()
