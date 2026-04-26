#!/usr/bin/env python3
"""
update_memory — 只更新 memory.md 的兼容入口（Prism-Trace 路径）。

用法：
    python -m learning.update_memory \\
        --username doc_rigorous \\
        --ep_dir workspace/test/train_test \\
        --engine kimi-k2-turbo-preview

等价于：
    python -m learning.learning --username ... --ep_dir ... --skip_meta_skill --skip_content_strategy

仅处理 preference_view 非 None 的 ep（有用户 feedback/satisfied 字段）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import textgrad as tg

from learning.prism_trace import decompose
from learning.tgd_engine import get_engine_from_builtin
from learning.optimization.preference_loss import PreferenceLoss
from utils.user_workspace import ensure_user_memory


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
        description="Prism-Trace：只更新 memory.md（PreferenceLoss）。",
    )
    p.add_argument("--username", required=True,
                   help="用户名；memory 路径自动推导为 workspace/<username>/memory/memory.md。")
    p.add_argument("--memory", default=None,
                   help="显式指定 memory.md 路径，优先于 --username 推导。")
    p.add_argument("--ep_dir", default=None,
                   help="包含 ep_*.json 的目录（递归收集）。与 --ep 二选一。")
    p.add_argument("--ep", nargs="+", default=None, metavar="FILE",
                   help="显式指定 ep_*.json 文件列表。与 --ep_dir 二选一。")
    p.add_argument("--output", default=None,
                   help="优化后写入路径；默认覆盖原 memory.md。")
    p.add_argument("--engine", default="kimi-k2-turbo-preview",
                   help="LLM engine 名称。")
    p.add_argument("--dry-run", action="store_true",
                   help="只统计可用 ep 数量，不写文件。")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    memory_path = Path(args.memory) if args.memory else ensure_user_memory(_ROOT, args.username)

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
    pref_traces = [t for t in traces if t.preference_view]

    if args.dry_run:
        print(f"[dry-run] {len(traces)} ep 总计，{len(pref_traces)} 条含 preference_view")
        return

    if not pref_traces:
        raise SystemExit("所有 ep 均无 preference_view（无 feedback/satisfied 字段），跳过 memory 更新。")
    print(f"[update_memory] {len(pref_traces)}/{len(traces)} ep 含 preference_view", flush=True)

    engine = get_engine_from_builtin(args.engine)
    if engine is not None:
        tg.set_backward_engine(engine, override=True)
    else:
        tg.set_backward_engine(args.engine, override=True)

    mem_var = tg.Variable(
        memory_path.read_text(encoding="utf-8"),
        requires_grad=True,
        role_description="user memory: preferences, task/video habits, and interaction correction summaries",
    )
    eng_kwarg = {"engine": engine} if engine is not None else {}
    optimizer = tg.TGD(parameters=[mem_var], **eng_kwarg)

    for trace in pref_traces:
        PreferenceLoss(
            trajectory_text=trace.preference_view,
            num_rounds=trace.num_rounds,
        )(mem_var).backward()

    optimizer.step()

    content = _strip_md_fence(mem_var.value)
    if not content:
        raise SystemExit("TGD 返回空 memory，已中止写入。")
    out_path = Path(args.output) if args.output else memory_path
    out_path.write_text(content + "\n", encoding="utf-8")
    print(f"[update_memory] memory → {out_path}", flush=True)


if __name__ == "__main__":
    main()
