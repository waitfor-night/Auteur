#!/usr/bin/env python3
"""
读 eval_metrics 输出的 JSON（或按用户读 workspace 再算），画 Recall、Violation Rate、Meta-skill 长度 随 Epoch 变化。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SANDBOX_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _SANDBOX_ROOT.parent


def load_results(input_path: Path) -> list:
    """加载 eval 输出的 JSON。"""
    if not input_path.is_file():
        return []
    return json.loads(input_path.read_text(encoding="utf-8"))


def plot(results: list, output_path: Optional[Path] = None) -> None:
    """画三张子图：Recall、Violation Rate、MetaSkillLength vs Epoch。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib 未安装，跳过绘图。pip install matplotlib", file=sys.stderr)
        return

    if not results:
        print("无数据，跳过绘图", file=sys.stderr)
        return

    epochs = [r["epoch"] for r in results]
    recall = [r["recall"] for r in results]
    violation = [r["violation_rate"] for r in results]
    meta_len = [r["meta_skill_length"] for r in results]

    fig, axes = plt.subplots(1, 3, figsize=(12, 4))

    axes[0].plot(epochs, recall, "o-", color="C0")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Target Tool Recall")
    axes[0].set_title("Recall")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(epochs, violation, "s-", color="C1")
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("Violation Rate")
    axes[1].set_title("Trace Constraint Violation Rate")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(epochs, meta_len, "^-", color="C2")
    axes[2].set_xlabel("Epoch")
    axes[2].set_ylabel("Chars")
    axes[2].set_title("Meta-skill Length")
    axes[2].grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved {output_path}")
    else:
        plt.savefig(_SANDBOX_ROOT / "metrics_plot.png", dpi=150, bbox_inches="tight")
        print(f"Saved {_SANDBOX_ROOT / 'metrics_plot.png'}")
    plt.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="画 Recall / Violation / MetaSkillLength 随 Epoch 变化")
    parser.add_argument("--input", default=None, help="eval_metrics 输出的 JSON 路径（可选）")
    parser.add_argument("--username", default=None, help="若未给 --input，则对 username 跑 eval 再画图")
    parser.add_argument("--output", default=None, help="输出图片路径（默认 sandbox/metrics_plot.png）")
    args = parser.parse_args()

    sys.path.insert(0, str(_PROJECT_ROOT))

    if args.input:
        results = load_results(Path(args.input))
    elif args.username:
        from sandbox.eval_metrics import run_eval
        results = run_eval(username=args.username)
    else:
        print("请指定 --input <json> 或 --username <name>", file=sys.stderr)
        return 1

    out_path = Path(args.output) if args.output else None
    plot(results, output_path=out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
