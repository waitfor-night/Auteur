from agno.tools import tool
from typing import Dict, Any, List, Optional
from utils.trace_recorder import recorder
import json
from .sub_implement import (
    set_plan,
    create_multi_stage_plan,
    get_multi_stage_plan,
    MultiStagePlan,
)


@tool
@recorder.record
def submit_video_execution_plan(plan_json: str) -> Dict[str, Any]:
    """
    （Legacy 单阶段）注册单阶段 Video Execution Plan（JSON 字符串）。

    新推荐流程：使用 submit_multi_stage_plan 提交多阶段计划。
    """
    try:
        plan = json.loads(plan_json)
        if (
            not isinstance(plan, dict)
            or "task_metadata" not in plan
            or "timeline" not in plan
        ):
            return {
                "success": False,
                "error": "Invalid plan: must contain task_metadata and timeline.",
            }
        set_plan(plan)
        return {"success": True, "message": "Plan registered."}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON parse error: {e}"}


@tool
@recorder.record
def submit_multi_stage_plan(
    global_instruction: str,
    stages: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    提交多阶段计划（Multi-Stage Plan）。

    参数：
      - global_instruction: 用户完整原始指令。
      - stages: 阶段列表，每项包含 stage_name、skill_type、description、plan、可选 reference_source。
      - metadata: 额外元信息（可选）。
    """
    if not isinstance(global_instruction, str) or not global_instruction.strip():
        return {"success": False, "error": "global_instruction must be a non-empty string."}
    if not isinstance(stages, list) or not stages:
        return {"success": False, "error": "stages must be a non-empty list."}

    try:
        msp: MultiStagePlan = create_multi_stage_plan(
            global_instruction=global_instruction,
            stages_data=stages,
            metadata=metadata,
        )
    except Exception as e:
        return {"success": False, "error": f"Failed to create multi-stage plan: {e}"}

    return {
        "success": True,
        "plan_id": msp.plan_id,
        "status": msp.status,
        "total_stages": len(msp.stages),
        "summary": msp.get_summary(),
    }


@tool
@recorder.record
def get_multi_stage_plan_summary_tool() -> Dict[str, Any]:
    """
    获取当前多阶段计划的整体概览（供 Actor 使用）。
    """
    msp = get_multi_stage_plan()
    if not msp:
        return {"success": False, "error": "No MultiStagePlan registered yet."}

    data = msp.to_dict()
    stages_brief = [
        {
            "stage_id": s.get("stage_id"),
            "stage_name": s.get("stage_name"),
            "skill_type": s.get("skill_type"),
            "status": s.get("status"),
        }
        for s in data.get("stages") or []
    ]
    return {
        "success": True,
        "plan_id": data.get("plan_id"),
        "status": data.get("status"),
        "total_stages": len(stages_brief),
        "stages": stages_brief,
        "global_instruction": data.get("global_instruction"),
    }


@tool
@recorder.record
def get_current_stage_plan_tool() -> Dict[str, Any]:
    """
    获取「下一个待执行阶段」的计划。

    - 若存在 status != \"completed\" 的阶段，返回该阶段信息。
    - 若所有阶段均已完成，返回 all_completed=True 及最终输出摘要。
    """
    msp = get_multi_stage_plan()
    if not msp:
        return {"success": False, "error": "No MultiStagePlan registered yet."}

    data = msp.to_dict()
    stages = data.get("stages") or []
    # 找到第一个未完成的阶段
    for s in stages:
        if s.get("status") != "completed":
            return {
                "success": True,
                "all_completed": False,
                "stage": s,
            }

    # 全部完成
    final_output = msp.get_final_output()
    return {
        "success": True,
        "all_completed": True,
        "final_output": final_output,
        "summary": msp.get_summary(),
    }


@tool
@recorder.record
def get_stage_outputs_tool(stage_id: int) -> Dict[str, Any]:
    """
    获取指定阶段的输出（供后续阶段 reference_source 使用）。
    """
    msp = get_multi_stage_plan()
    if not msp:
        return {"success": False, "error": "No MultiStagePlan registered yet."}
    if not isinstance(stage_id, int) or stage_id < 1:
        return {
            "success": False,
            "error": f"stage_id must be 1-based integer (1,2,3,...). Got: {stage_id}",
        }

    outputs = msp.get_stage_outputs(stage_id)
    if outputs is None:
        return {
            "success": False,
            "error": f"No outputs found for stage_id={stage_id}. The stage may not be completed yet.",
        }

    return {"success": True, "stage_id": stage_id, "outputs": outputs}


@tool
@recorder.record
def set_stage_outputs_tool(stage_id: int, outputs_json: str) -> Dict[str, Any]:
    """
    设置指定阶段的输出，并更新阶段状态为 completed。

    - stage_id: 阶段 ID，从 1 开始。
    - outputs_json: JSON 字符串，表示该阶段的输出（任意结构，建议为 dict）。
    """
    msp = get_multi_stage_plan()
    if not msp:
        return {"success": False, "error": "No MultiStagePlan registered yet."}
    if not isinstance(stage_id, int) or stage_id < 1:
        return {
            "success": False,
            "error": f"stage_id must be 1-based integer (1,2,3,...). Got: {stage_id}",
        }

    try:
        outputs = json.loads(outputs_json) if isinstance(outputs_json, str) else outputs_json
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"outputs_json JSON parse error: {e}"}

    ok = msp.set_stage_outputs(stage_id, outputs if isinstance(outputs, dict) else {"value": outputs})
    if not ok:
        return {"success": False, "error": f"Failed to set outputs for stage_id={stage_id}."}

    stage = msp.get_stage_by_id(stage_id)
    return {
        "success": True,
        "stage_id": stage_id,
        "plan_status": msp.status,
        "stage": stage,
    }


__all__ = [
    "submit_video_execution_plan",
    "submit_multi_stage_plan",
    "get_multi_stage_plan_summary_tool",
    "get_current_stage_plan_tool",
    "get_stage_outputs_tool",
    "set_stage_outputs_tool",
]