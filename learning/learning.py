#!/usr/bin/env python3
"""
Prism-Trace Learning：从 ep_*.json 经三视图分解，分别优化 meta-skill / memory / content_strategy。

用法：
    python -m learning.learning \\
        --meta_skill skills/SKILL_doc_rigorous.md \\
        --username doc_rigorous \\
        --ep_dir workspace/test/train_test \\
        --engine kimi-k2-turbo-preview

三视图对应关系：
    execution_view   → ExecutionLoss        → meta-skill（每条 ep 均参与）
    preference_view  → PreferenceLoss       → memory.md（仅沙盒模式 ep，有 feedback/satisfied）
    audience_view    → ContentStrategyLoss  → content_strategy.md（仅自动运营 ep，有社媒数据）

各优化器独立积累梯度；某视图在所有 ep 均为 None 时，跳过对应 step。
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

from learning.prism_trace import decompose, PrismTrace
from learning.tgd_engine import get_engine_from_builtin
from learning.optimization.execution_loss import ExecutionLoss
from learning.optimization.preference_loss import PreferenceLoss
from learning.optimization.content_strategy_loss import ContentStrategyLoss
from utils.user_workspace import ensure_user_memory, ensure_content_strategy


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


def _opt_path(src: Path) -> Path:
    """foo/BAR.md → foo/BAR_opt.md"""
    return src.with_stem(src.stem + "_opt")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Prism-Trace Learning：ep_*.json → 三视图 → 优化 meta-skill / memory / content_strategy。",
    )
    p.add_argument("--meta_skill", required=True,
                   help="meta-skill MD 文件路径（如 skills/SKILL_doc_rigorous.md）。")
    p.add_argument("--username", default=None,
                   help="用户名；memory / content_strategy 路径自动推导为 workspace/<username>/…。")
    p.add_argument("--memory", default=None,
                   help="显式指定 memory.md 路径，优先于 --username 推导。")
    p.add_argument("--content_strategy", default=None,
                   help="显式指定 content_strategy.md 路径，优先于 --username 推导。")
    p.add_argument("--ep_dir", default=None,
                   help="包含 ep_*.json 的目录（递归收集）。与 --ep 二选一。")
    p.add_argument("--ep", nargs="+", default=None, metavar="FILE",
                   help="显式指定 ep_*.json 文件列表。与 --ep_dir 二选一。")
    p.add_argument("--meta_skill_output", default=None,
                   help="优化后 meta-skill 写入路径；默认输出到同目录下 <stem>_opt.md。")
    p.add_argument("--memory_output", default=None,
                   help="优化后 memory 写入路径；默认输出到同目录下 <stem>_opt.md。")
    p.add_argument("--content_strategy_output", default=None,
                   help="优化后 content_strategy 写入路径；默认输出到同目录下 <stem>_opt.md。")
    p.add_argument("--engine", default="kimi-k2-turbo-preview",
                   help="LLM engine（内置：doubao-seed-2-0-pro-260215 / deepseek-chat / kimi-k2-turbo-preview）。")
    p.add_argument("--skip_meta_skill", action="store_true", help="跳过 meta-skill 优化。")
    p.add_argument("--skip_memory", action="store_true", help="跳过 memory 更新。")
    p.add_argument("--skip_content_strategy", action="store_true", help="跳过 content_strategy 更新。")
    return p.parse_args()


def _collect_ep_paths(ep_dir: str | None, ep: list | None) -> list[Path]:
    if ep_dir and ep:
        raise SystemExit("--ep_dir 和 --ep 不能同时使用。")
    if ep_dir:
        paths = sorted(Path(ep_dir).rglob("ep_*.json"))
    elif ep:
        paths = [Path(f) for f in ep]
    else:
        raise SystemExit("--ep_dir 或 --ep 至少提供一个。")
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise SystemExit(f"以下文件不存在：{missing}")
    return paths


def main() -> None:
    args = parse_args()

    # ── 路径校验 ──────────────────────────────────────────────────────────
    meta_path = Path(args.meta_skill)
    if not meta_path.is_file():
        raise SystemExit(f"Meta-skill file not found: {meta_path}")

    memory_path: Path | None = None
    if not args.skip_memory:
        if args.memory:
            memory_path = Path(args.memory)
        elif args.username:
            memory_path = ensure_user_memory(_ROOT, args.username)
        else:
            raise SystemExit("--memory 或 --username 至少提供一个（或使用 --skip_memory 跳过）。")

    cs_path: Path | None = None
    if not args.skip_content_strategy:
        if args.content_strategy:
            cs_path = Path(args.content_strategy)
        elif args.username:
            cs_path = ensure_content_strategy(_ROOT, args.username)
        else:
            raise SystemExit("--content_strategy 或 --username 至少提供一个（或使用 --skip_content_strategy 跳过）。")

    # ── 收集 ep_*.json ────────────────────────────────────────────────────
    ep_paths = _collect_ep_paths(args.ep_dir, args.ep)
    if not ep_paths:
        raise SystemExit("未找到任何 ep_*.json 文件，请检查 --ep_dir / --ep 路径。")

    # ── Prism-Trace 分解 ──────────────────────────────────────────────────
    # ep 原始数据保留，用于 username 过滤
    ep_records: list[tuple[dict, PrismTrace]] = []
    for p in ep_paths:
        ep = json.loads(p.read_text(encoding="utf-8"))
        ep_records.append((ep, decompose(ep)))

    all_traces   = [t for _, t in ep_records]
    # memory / content_strategy 只用 username 匹配的 ep（per-user 文档）
    # meta-skill 使用全部 ep（跨用户信号有益于通用编排规则优化）
    if args.username:
        user_traces = [t for ep, t in ep_records if ep.get("username") == args.username]
        filtered = len(all_traces) - len(user_traces)
        if filtered:
            print(f"[learning] username={args.username!r} 过滤：{filtered} 条 ep 不参与 memory/cs 优化", flush=True)
    else:
        user_traces = all_traces

    n_pref = sum(1 for t in user_traces if t.preference_view)
    n_aud  = sum(1 for t in user_traces if t.audience_view)
    print(
        f"[learning] 共 {len(all_traces)} ep（meta-skill 全用）| "
        f"username ep={len(user_traces)} preference={n_pref} audience={n_aud}",
        flush=True,
    )

    # ── 初始化 engine ─────────────────────────────────────────────────────
    engine = get_engine_from_builtin(args.engine)
    if engine is not None:
        tg.set_backward_engine(engine, override=True)
    else:
        tg.set_backward_engine(args.engine, override=True)

    # ── TGD Variables ─────────────────────────────────────────────────────
    meta_var = (
        tg.Variable(
            meta_path.read_text(encoding="utf-8"),
            requires_grad=True,
            role_description="meta-skill document for the Planner agent, guides video execution plan generation",
        )
        if not args.skip_meta_skill else None
    )
    mem_var = (
        tg.Variable(
            memory_path.read_text(encoding="utf-8"),
            requires_grad=True,
            role_description="user memory: preferences, task/video habits, and interaction correction summaries",
        )
        if (not args.skip_memory and memory_path) else None
    )
    cs_var = (
        tg.Variable(
            cs_path.read_text(encoding="utf-8"),
            requires_grad=True,
            role_description="content strategy: what kind of content performs well for this account on social media",
        )
        if (not args.skip_content_strategy and cs_path) else None
    )

    # ── Optimizers ────────────────────────────────────────────────────────
    eng_kwarg = {"engine": engine} if engine is not None else {}
    meta_opt = tg.TGD(parameters=[meta_var], **eng_kwarg) if meta_var else None
    mem_opt  = tg.TGD(parameters=[mem_var],  **eng_kwarg) if mem_var  else None
    cs_opt   = tg.TGD(parameters=[cs_var],   **eng_kwarg) if cs_var   else None

    # ── 三视图 backward ───────────────────────────────────────────────────
    # execution：全部 ep，跨用户信号均有益于 meta-skill 优化
    if meta_var:
        for trace in all_traces:
            ExecutionLoss(
                trajectory_text=trace.execution_view,
                num_rounds=trace.num_rounds,
            )(meta_var).backward()

    # preference / audience：只用 username 匹配的 ep（per-user 文档）
    for trace in user_traces:
        if mem_var and trace.preference_view:
            PreferenceLoss(
                trajectory_text=trace.preference_view,
                num_rounds=trace.num_rounds,
            )(mem_var).backward()

        if cs_var and trace.audience_view:
            ContentStrategyLoss(
                audience_view=trace.audience_view,
            )(cs_var).backward()

    # ── Step + 写出 ───────────────────────────────────────────────────────
    if meta_opt:
        meta_opt.step()
        meta_out = Path(args.meta_skill_output) if args.meta_skill_output else _opt_path(meta_path)
        meta_out.parent.mkdir(parents=True, exist_ok=True)
        meta_out.write_text(meta_var.value, encoding="utf-8")
        print(f"[learning] meta-skill → {meta_out}", flush=True)

    if mem_opt:
        if n_pref > 0:
            mem_opt.step()
            content = _strip_md_fence(mem_var.value)
            if not content:
                raise SystemExit("TGD 返回空 memory，已中止写入。")
            mem_out = Path(args.memory_output) if args.memory_output else _opt_path(memory_path)
            mem_out.parent.mkdir(parents=True, exist_ok=True)
            mem_out.write_text(content + "\n", encoding="utf-8")
            print(f"[learning] memory → {mem_out}", flush=True)
        else:
            print("[learning] 无 preference_view，跳过 memory step。", flush=True)

    if cs_opt:
        if n_aud > 0:
            cs_opt.step()
            content = _strip_md_fence(cs_var.value)
            if not content:
                raise SystemExit("TGD 返回空 content_strategy，已中止写入。")
            cs_out = Path(args.content_strategy_output) if args.content_strategy_output else _opt_path(cs_path)
            cs_out.parent.mkdir(parents=True, exist_ok=True)
            cs_out.write_text(content + "\n", encoding="utf-8")
            print(f"[learning] content_strategy → {cs_out}", flush=True)
        else:
            print("[learning] 无 audience_view，跳过 content_strategy step。", flush=True)


if __name__ == "__main__":
    main()
