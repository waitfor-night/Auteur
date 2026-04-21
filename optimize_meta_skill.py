#!/usr/bin/env python3
import argparse
import os
from pathlib import Path

from openai import OpenAI

import textgrad as tg
from textgrad.engine.local_model_openai_api import ChatExternalClient

from utils.trajectory_io import (
    load_trace_file_multi,
    load_trace_files,
    load_traces_from_json,
)
from optimization.execution_loss import ExecutionLoss


# 优化时 backward 与 TGD 的 LLM 输出可能很长，在 engine 初始化时指定 max_tokens
MAX_TOKENS = 100000


class ChatExternalClientWithMaxTokens(ChatExternalClient):
    """在初始化时注入 max_tokens，所有 generate 调用均使用该上限。"""

    def __init__(self, *, max_tokens: int = MAX_TOKENS, **kwargs):
        super().__init__(**kwargs)
        self._max_tokens = max_tokens

    def generate(self, content, system_prompt=None, **kwargs):
        kwargs.setdefault("max_tokens", self._max_tokens)
        return super().generate(content, system_prompt=system_prompt, **kwargs)


LLM_CONFIG = [
    {
        "model": "doubao-seed-2-0-pro-260215",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_env": "ARK_API_KEY",
    },
    {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    {
        "model": "kimi-k2-turbo-preview",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
    },
]


def get_engine_from_builtin(model_name: str):
    """从 LLM_CONFIG 按 model 名取配置，用 os.environ[api_key_env] 作为 api_key 构造 engine；未找到返回 None。"""
    for c in LLM_CONFIG:
        if c.get("model") == model_name:
            api_key = os.environ.get(c["api_key_env"])
            if not api_key:
                raise ValueError(
                    f"Environment variable {c['api_key_env']!r} is not set (required for model {model_name!r})."
                )
            client = OpenAI(base_url=c["base_url"], api_key=api_key)
            return ChatExternalClientWithMaxTokens(
                client=client, model_string=c["model"], max_tokens=MAX_TOKENS
            )
    return None


def resolve_trace_paths(paths: list) -> list:
    """
    将若干路径（文件或目录）解析为 trace 文件列表。
    - 若为文件：直接加入列表。
    - 若为目录：递归收集该目录及所有子目录下的 ctx*.json 文件（即 context 格式的轨迹）。
    返回去重且排序后的 Path 列表。
    """
    resolved = []
    seen = set()
    for p in paths:
        path = Path(p).resolve()
        if path.is_file():
            key = path
            if key not in seen:
                seen.add(key)
                resolved.append(path)
        elif path.is_dir():
            for f in sorted(path.rglob("ctx*.json")):
                f = f.resolve()
                if f not in seen:
                    seen.add(f)
                    resolved.append(f)
        else:
            raise FileNotFoundError(f"Not a file or directory: {path}")
    return sorted(resolved)


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
        # JSON 主路径：一文件一 trace，一条 trace 计算一次 loss
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
    # 使用同一 engine，使 TGD step 的生成也受 MAX_TOKENS 限制
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
