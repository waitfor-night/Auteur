#!/usr/bin/env python3
"""
optimize_meta_skill — 只优化 meta-skill 的兼容入口（Prism-Trace 路径）。

用法：
    python -m learning.optimize_meta_skill \\
        --meta_skill skills/SKILL_doc_rigorous.md \\
        --ep_dir workspace/test/train_test \\
        --engine kimi-k2-turbo-preview

等价于：
    python -m learning.learning --meta_skill ... --ep_dir ... --skip_memory --skip_content_strategy
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

from learning.prism_trace import decompose
from learning.tgd_engine import get_engine_from_builtin
from learning.optimization.execution_loss import ExecutionLoss


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Prism-Trace：只优化 meta-skill（ExecutionLoss）。",
    )
    p.add_argument("--meta_skill", required=True,
                   help="meta-skill MD 文件路径。")
    p.add_argument("--ep_dir", default=None,
                   help="包含 ep_*.json 的目录（递归收集）。与 --ep 二选一。")
    p.add_argument("--ep", nargs="+", default=None, metavar="FILE",
                   help="显式指定 ep_*.json 文件列表。与 --ep_dir 二选一。")
    p.add_argument("--output", default=None,
                   help="优化后写入路径；默认覆盖 --meta_skill。")
    p.add_argument("--engine", default="kimi-k2-turbo-preview",
                   help="LLM engine 名称。")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    meta_path = Path(args.meta_skill)
    if not meta_path.is_file():
        raise SystemExit(f"Meta-skill file not found: {meta_path}")

    if args.ep_dir and args.ep:
        raise SystemExit("--ep_dir 和 --ep 不能同时使用。")
    if args.ep_dir:
        ep_paths = sorted(Path(args.ep_dir).rglob("ep_*.json"))
    elif args.ep:
        ep_paths = [Path(f) for f in args.ep]
    else:
        raise SystemExit("--ep_dir 或 --ep 至少提供一个。")

    if not ep_paths:
        raise SystemExit("未找到任何 ep_*.json 文件。")

    traces = [decompose(json.loads(p.read_text(encoding="utf-8"))) for p in ep_paths]
    print(f"[optimize_meta_skill] {len(traces)} ep 已加载", flush=True)

    engine = get_engine_from_builtin(args.engine)
    if engine is not None:
        tg.set_backward_engine(engine, override=True)
    else:
        tg.set_backward_engine(args.engine, override=True)

    meta_var = tg.Variable(
        meta_path.read_text(encoding="utf-8"),
        requires_grad=True,
        role_description="meta-skill document for the Planner agent, guides video execution plan generation",
    )
    eng_kwarg = {"engine": engine} if engine is not None else {}
    optimizer = tg.TGD(parameters=[meta_var], **eng_kwarg)

    for trace in traces:
        ExecutionLoss(
            trajectory_text=trace.execution_view,
            num_rounds=trace.num_rounds,
        )(meta_var).backward()

    optimizer.step()

    out_path = Path(args.output) if args.output else meta_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(meta_var.value, encoding="utf-8")
    print(f"[optimize_meta_skill] meta-skill → {out_path}", flush=True)


if __name__ == "__main__":
    main()
