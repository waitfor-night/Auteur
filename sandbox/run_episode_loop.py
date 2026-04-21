#!/usr/bin/env python3
"""
回合制主循环（修订）：
- 单 task：Planner → Plan → UserSimulator 反馈 → 写 context → 若不满意则 RePlan（不更新 meta-skill），直到满意。
- 所有 task 都满意之后，再更新 meta-skill 一次；更新完成后重跑之前所有 task，对比新旧 context。
"""
from __future__ import annotations

import argparse
import json
from dotenv import load_dotenv
load_dotenv()
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 项目根目录
_SANDBOX_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _SANDBOX_ROOT.parent


def _ensure_user_meta_skill(project_root: Path, username: str) -> Path:
    """确保用户有 meta-skill 文件；若无则从 META_SKILL.md 拷贝为 SKILL_<username>.md。"""
    meta_dir = project_root / "skills"
    default_skill = meta_dir / "META_SKILL.md"
    user_skill = meta_dir / f"SKILL_{username}.md"
    if not user_skill.is_file() and default_skill.is_file():
        shutil.copy2(default_skill, user_skill)
    return user_skill


def _get_meta_skill_path(project_root: Path, username: str, use_opt: bool) -> Path:
    """当前使用的 meta-skill 文件：use_opt 时优先 SKILL_opt_<username>.md。"""
    meta_dir = project_root / "skills"
    opt_path = meta_dir / f"SKILL_opt_{username}.md"
    base_path = meta_dir / f"SKILL_{username}.md"
    if use_opt and opt_path.is_file():
        return opt_path
    return base_path if base_path.is_file() else meta_dir / "META_SKILL.md"


def run_single_task_until_satisfied(
    assistant: Any,
    task: str,
    simulator: Any,
    max_rounds: int,
    meta_path: Path,
) -> Tuple[bool, int, Optional[dict]]:
    """
    单 task 内只 RePlan 直到满意（不调用 optimize_meta_skill）。
    整个过程中复用同一个 assistant（同一个 RunContext），故多轮 Plan–Feedback 都会
    追加到同一个 ctx_*.json 的 rounds 数组中，即「一个 task、一个 ctx 文件、多轮同文件」。
    返回 (accepted, rounds_used, last_plan)。
    """
    from skills.skill_loader import set_meta_skill_path

    set_meta_skill_path(meta_path)
    assistant.user_input = task
    assistant.context.set_user_input(task)

    accepted = False
    last_plan = None
    rounds_used = 0

    for r in range(1, max_rounds + 1):
        print(f"\n[Sandbox] Task RePlan round {r}/{max_rounds} (meta: {meta_path.name})", flush=True)
        round_result = assistant.run_single_round()
        tools_execute_order = round_result.get("tools_execute_order")
        if not round_result.get("success"):
            return False, r, round_result.get("plan")

        last_plan = round_result.get("plan")
        plan_summary = json.dumps(last_plan, ensure_ascii=False, indent=2) if last_plan else ""
        feedback, accepted = simulator.review(
            task,
            plan_summary,
            tools_execute_order,
            actor_output=round_result.get("output"),
            assistant_output_dir=getattr(assistant, "output_dir", None),
        )
        assistant.context.set_round_feedback(feedback)
        rounds_used = r

        print(f"[Sandbox] Role 反馈: accepted={accepted}", flush=True)
        if accepted:
            break

    return accepted, rounds_used, last_plan


def _load_context_from_dir(context_subdir_path: Path) -> Optional[Dict[str, Any]]:
    """从 context 子目录（如 task_0/phase1）加载唯一的 ctx_*.json。"""
    if not context_subdir_path.is_dir():
        return None
    files = list(context_subdir_path.glob("ctx_*.json"))
    if not files:
        return None
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)


def _build_task_comparison(
    project_root: Path,
    username: str,
    task_index: int,
    task_preview: str,
    result_phase1: Dict[str, Any],
    result_phase3: Dict[str, Any],
) -> Dict[str, Any]:
    """同一 task 的 phase1 与 phase3 context 对比，作为分析实验数据。"""
    context_dir = project_root / "workspace" / username / "context"
    phase1_dir = context_dir / f"task_{task_index}" / "phase1"
    phase3_dir = context_dir / f"task_{task_index}" / "phase3"
    ctx1 = _load_context_from_dir(phase1_dir)
    ctx3 = _load_context_from_dir(phase3_dir)
    rounds1 = (ctx1 or {}).get("rounds") or []
    rounds3 = (ctx3 or {}).get("rounds") or []
    return {
        "task_index": task_index,
        "task_preview": task_preview,
        "phase1_context_path": str(phase1_dir),
        "phase3_context_path": str(phase3_dir),
        "phase1_rounds": len(rounds1),
        "phase3_rounds": len(rounds3),
        "phase1_accepted": result_phase1.get("accepted", False),
        "phase3_accepted": result_phase3.get("accepted", False),
        "phase1_rounds_used": result_phase1.get("rounds_used", 0),
        "phase3_rounds_used": result_phase3.get("rounds_used", 0),
        "phase1_rounds_detail": [
            {"round": r.get("round"), "feedback": (r.get("feedback") or "")[:200]}
            for r in rounds1
        ],
        "phase3_rounds_detail": [
            {"round": r.get("round"), "feedback": (r.get("feedback") or "")[:200]}
            for r in rounds3
        ],
    }


def run_sandbox_full(
    username: str,
    tasks: List[str],
    role_id: Optional[str] = None,
    max_rounds_per_task: int = 5,
    time_length: int = 15,
    total_duration: Optional[int] = None,
    run_optimizer: bool = True,
    run_compare_phase: bool = True,
    comparison_output_path: Optional[Path] = None,
    tasks_file_gen: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    一 task 一个 ctx 文件；同一 task 在直到满意前的多轮交互中，所有 round 的 Plan–Feedback
    都记录在同一个 ctx_*.json 里（RunContext 的 rounds 数组）。全部 task 跑完后更新 meta-skill，
    再跑泛化阶段，对比并输出 MTTA。
    1) 学习阶段（Phase1）：每个 task 独立目录 task_<i>/phase1/，RePlan 直到满意。
    2) Phase2：全部满意后更新 meta-skill 一次。
    3) 泛化阶段：若 tasks_file_gen 提供则用该文件中的相似域新任务，写入 task_<i>/phase2_gen/；
       否则用同一批 task 重跑，写入 task_<i>/phase3/。对比输出含 mtta_learning、mtta_generalization。
    """
    if role_id is None:
        role_id = username

    sys.path.insert(0, str(_PROJECT_ROOT))
    from skills.skill_loader import set_meta_skill_path
    from sandbox.user_simulator import UserSimulator
    from video_assistant import VideoAssistant

    project_root = _PROJECT_ROOT
    workspace_dir = project_root / "workspace" / username
    context_dir = workspace_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    output_dir = workspace_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    from utils.user_workspace import ensure_user_memory

    ensure_user_memory(project_root, username)

    _ensure_user_meta_skill(project_root, username)
    meta_path = _get_meta_skill_path(project_root, username, use_opt=False)
    simulator = UserSimulator(role_id=role_id)

    # ---------- Phase 1: 每个 task 单独 context（task_i/phase1），RePlan 直到满意 ----------
    task_results_phase1: List[Dict[str, Any]] = []
    all_satisfied = True
    for task_idx, task in enumerate(tasks):
        subdir = f"task_{task_idx}/phase1"
        assistant = VideoAssistant(
            output_dir=str(output_dir),
            time_length=time_length,
            total_duration=total_duration,
            allow_interactive=False,
            planner_only=False,
            username=username,
            context_subdir=subdir,
        )
        assistant._set_run_context_globals()
        accepted, rounds_used, last_plan = run_single_task_until_satisfied(
            assistant, task, simulator, max_rounds_per_task, meta_path
        )
        task_results_phase1.append({
            "task_index": task_idx,
            "task": task[:80] + ("..." if len(task) > 80 else ""),
            "accepted": accepted,
            "rounds_used": rounds_used,
            "context_subdir": subdir,
        })
        if not accepted:
            all_satisfied = False
        print(f"[Sandbox] Phase1 task {task_idx}: accepted={accepted}, rounds={rounds_used}, context={subdir}", flush=True)

    # ---------- Phase 2: 全部满意后，更新 meta-skill 一次（读 context 下所有 phase1 的 ctx_*.json） ----------
    meta_output = project_root / "skills" / f"SKILL_opt_{username}.md"
    if run_optimizer and all_satisfied and task_results_phase1:
        cmd = [
            sys.executable,
            "-m", "learning.optimize_meta_skill",
            "--meta_skill", str(meta_path),
            "--trace", str(context_dir),
            "--output", str(meta_output),
        ]
        print(f"[Sandbox] Phase2 更新 meta-skill: {' '.join(cmd)}", flush=True)
        subprocess.run(cmd, cwd=str(project_root), timeout=300)
    else:
        if not all_satisfied:
            print("[Sandbox] Phase2 跳过：并非所有 task 已满意", flush=True)

    # ---------- 泛化阶段：用新 meta-skill 跑任务（同批 task→phase3；或 tasks_file_gen→phase2_gen） ----------
    tasks_gen: List[str] = []
    if tasks_file_gen and tasks_file_gen.is_file():
        raw = [ln.strip() for ln in tasks_file_gen.read_text(encoding="utf-8").splitlines() if ln.strip()]
        tasks_gen = [t for t in raw if not t.startswith("#")]
    use_generalization_tasks = bool(tasks_gen)
    gen_subdir_name = "phase2_gen" if use_generalization_tasks else "phase3"
    tasks_for_gen = tasks_gen if use_generalization_tasks else tasks

    task_results_phase3: List[Dict[str, Any]] = []
    meta_path_opt = _get_meta_skill_path(project_root, username, use_opt=True)
    comparison: Optional[Dict[str, Any]] = None

    if run_compare_phase and meta_path_opt.is_file() and tasks_for_gen:
        print(f"\n[Sandbox] 泛化阶段 使用新 meta-skill（{gen_subdir_name}），共 {len(tasks_for_gen)} 个 task", flush=True)
        for task_idx, task in enumerate(tasks_for_gen):
            subdir = f"task_{task_idx}/{gen_subdir_name}"
            assistant = VideoAssistant(
                output_dir=str(output_dir),
                time_length=time_length,
                total_duration=total_duration,
                allow_interactive=False,
                planner_only=False,
                username=username,
                context_subdir=subdir,
            )
            assistant._set_run_context_globals()
            accepted, rounds_used, _ = run_single_task_until_satisfied(
                assistant, task, simulator, max_rounds_per_task, meta_path_opt
            )
            task_results_phase3.append({
                "task_index": task_idx,
                "task": task[:80] + ("..." if len(task) > 80 else ""),
                "accepted": accepted,
                "rounds_used": rounds_used,
                "context_subdir": subdir,
            })
            print(f"[Sandbox] {gen_subdir_name} task {task_idx}: accepted={accepted}, rounds={rounds_used}, context={subdir}", flush=True)

        # MTTA：学习阶段与泛化阶段平均修正轮次
        rounds_phase1 = [r["rounds_used"] for r in task_results_phase1]
        rounds_gen = [r["rounds_used"] for r in task_results_phase3]
        mtta_learning = sum(rounds_phase1) / len(rounds_phase1) if rounds_phase1 else 0.0
        mtta_generalization = sum(rounds_gen) / len(rounds_gen) if rounds_gen else 0.0

        if use_generalization_tasks:
            comparison = {
                "phase": "generalization",
                "mtta_learning": round(mtta_learning, 4),
                "mtta_generalization": round(mtta_generalization, 4),
                "per_task_learning": [{"task_index": r["task_index"], "rounds_used": r["rounds_used"], "accepted": r["accepted"]} for r in task_results_phase1],
                "per_task_generalization": [{"task_index": r["task_index"], "task_preview": r.get("task", ""), "rounds_used": r["rounds_used"], "accepted": r["accepted"]} for r in task_results_phase3],
            }
        else:
            per_task_comparison = [
                _build_task_comparison(
                    project_root,
                    username,
                    task_idx,
                    task_results_phase1[task_idx].get("task", ""),
                    task_results_phase1[task_idx],
                    task_results_phase3[task_idx],
                )
                for task_idx in range(len(tasks))
            ]
            comparison = {
                "phase": "same_tasks",
                "mtta_learning": round(mtta_learning, 4),
                "mtta_generalization": round(mtta_generalization, 4),
                "per_task": per_task_comparison,
                "rounds_comparison": [
                    {
                        "task_index": p1["task_index"],
                        "phase1_rounds": p1["rounds_used"],
                        "phase3_rounds": p3["rounds_used"],
                        "phase1_accepted": p1["accepted"],
                        "phase3_accepted": p3["accepted"],
                    }
                    for p1, p3 in zip(task_results_phase1, task_results_phase3)
                ],
            }
        print("\n[Sandbox] MTTA: 学习阶段={}, 泛化阶段={}".format(mtta_learning, mtta_generalization), flush=True)
        if not use_generalization_tasks:
            print("[Sandbox] 同一任务前后两次 context 对比:", json.dumps(comparison["rounds_comparison"], ensure_ascii=False, indent=2), flush=True)

        if comparison_output_path is not None:
            comparison_output_path = Path(comparison_output_path)
            comparison_output_path.parent.mkdir(parents=True, exist_ok=True)
            comparison_output_path.write_text(
                json.dumps(comparison, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"[Sandbox] 分析数据已写入: {comparison_output_path}", flush=True)
    else:
        if not run_compare_phase:
            print("[Sandbox] 泛化阶段未启用（未传 --run_compare）", flush=True)
        elif not meta_path_opt.is_file():
            print("[Sandbox] 泛化阶段跳过：无 SKILL_opt 文件", flush=True)
        elif not tasks_for_gen:
            print("[Sandbox] 泛化阶段跳过：无任务（未提供 --tasks_file_gen 且无学习任务）", flush=True)

    return {
        "success": True,
        "username": username,
        "role_id": role_id,
        "all_satisfied_phase1": all_satisfied,
        "task_results_phase1": task_results_phase1,
        "task_results_phase3": task_results_phase3,
        "comparison": comparison,
    }


def run_episode_loop(
    username: str,
    task: str,
    role_id: Optional[str] = None,
    max_rounds: int = 5,
    time_length: int = 15,
    total_duration: Optional[int] = None,
    run_optimizer: bool = True,
) -> dict:
    """
    单 task 的回合制循环（兼容旧接口）：只 RePlan 直到满意，不在此函数内更新 meta-skill。
    若需「多 task → 更新 meta-skill → 重跑对比」，请用 run_sandbox_full 或 CLI --tasks/--tasks_file。
    """
    result = run_sandbox_full(
        username=username,
        tasks=[task],
        role_id=role_id,
        max_rounds_per_task=max_rounds,
        time_length=time_length,
        total_duration=total_duration,
        run_optimizer=False,
        run_compare_phase=False,
    )
    r1 = (result.get("task_results_phase1") or [{}])[0]
    return {
        "success": result.get("success", True),
        "accepted": r1.get("accepted", False),
        "username": username,
        "role_id": role_id,
        "rounds": r1.get("rounds_used", 0),
        "plan": None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="沙箱：单 task 仅 RePlan 直到满意；多 task 全部满意后更新 meta-skill，再重跑并对比新旧 context"
    )
    parser.add_argument("--username", required=True, help="用户名（context 与 meta-skill 按此隔离）")
    parser.add_argument("--task", action="append", dest="tasks", help="任务描述，可多次传入；与 --tasks_file 二选一")
    parser.add_argument("--tasks_file", default=None, help="每行一个 task 的文本文件（与 --task 二选一）")
    parser.add_argument("--role_id", default=None, help="Role 配置 ID，默认与 username 相同")
    parser.add_argument("--max_rounds_per_task", type=int, default=5, help="单 task 最大 RePlan 回合数")
    parser.add_argument("--time_length", type=int, default=15, help="每段时长（秒）")
    parser.add_argument("--total_duration", type=int, default=None, help="总时长（秒）")
    parser.add_argument("--no_optimizer", action="store_true", help="不调用 optimize_meta_skill（Phase2）")
    parser.add_argument("--no_compare", action="store_true", help="不跑泛化阶段（不重跑 task、不对比新旧 context）")
    parser.add_argument("--comparison_output", default=None, help="对比结果（含 MTTA）写入该 JSON 路径")
    parser.add_argument("--tasks_file_gen", default=None, help="泛化阶段任务文件（每行一个相似域新任务）；不传则用学习阶段同一批 task 重跑（phase3）")
    args = parser.parse_args()

    tasks: List[str] = []
    if args.tasks:
        tasks = args.tasks
    elif args.tasks_file:
        path = Path(args.tasks_file)
        if path.is_file():
            raw = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            tasks = [t for t in raw if not t.startswith("#")]
    if not tasks:
        parser.error("请指定 --task <描述>（可多次）或 --tasks_file <路径>")

    tasks_file_gen: Optional[Path] = Path(args.tasks_file_gen) if args.tasks_file_gen else None

    result = run_sandbox_full(
        username=args.username,
        tasks=tasks,
        role_id=args.role_id,
        max_rounds_per_task=args.max_rounds_per_task,
        time_length=args.time_length,
        total_duration=args.total_duration,
        run_optimizer=not args.no_optimizer,
        run_compare_phase=not args.no_compare,
        comparison_output_path=Path(args.comparison_output) if args.comparison_output else None,
        tasks_file_gen=tasks_file_gen,
    )
    print("\n[Sandbox] 结果:", json.dumps(result, ensure_ascii=False, indent=2, default=str))
    all_ok = result.get("all_satisfied_phase1", False)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
