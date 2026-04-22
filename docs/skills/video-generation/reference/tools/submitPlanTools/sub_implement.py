# 存储 Planner 提交的 Video Execution Plan，供 Runner/Actor 在规划结束后读取。

LAST_VIDEO_EXECUTION_PLAN = None


def set_plan(plan: dict) -> None:
    global LAST_VIDEO_EXECUTION_PLAN
    LAST_VIDEO_EXECUTION_PLAN = plan


def get_plan():
    return LAST_VIDEO_EXECUTION_PLAN


def clear_plan() -> None:
    global LAST_VIDEO_EXECUTION_PLAN
    LAST_VIDEO_EXECUTION_PLAN = None

from typing import Any, Optional, List, Dict
import time
import uuid


class MultiStagePlan:
    """
    多阶段执行计划。
    """

    def __init__(
        self,
        plan_id: str,
        global_instruction: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.plan_id = plan_id
        self.global_instruction = global_instruction
        self.stages: List[Dict[str, Any]] = []
        self.current_stage_index: int = 0
        self.status: str = "pending"  # pending, in_progress, completed, failed
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self.metadata: Dict[str, Any] = metadata or {}

    def add_stage(
        self,
        stage_name: str,
        skill_type: str,
        description: str = "",
        plan: Optional[Dict[str, Any]] = None,
        reference_source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """添加新阶段"""
        stage = {
            "stage_id": len(self.stages) + 1,
            "stage_name": stage_name,
            "skill_type": skill_type,
            "description": description,
            "plan": plan,
            "reference_source": reference_source,
            "outputs": None,
            "status": "pending",
        }
        self.stages.append(stage)
        self.updated_at = time.time()
        return stage

    def get_stage_by_id(self, stage_id: int) -> Optional[Dict[str, Any]]:
        """按 ID 获取阶段"""
        for stage in self.stages:
            if stage["stage_id"] == stage_id:
                return stage
        return None

    def get_current_stage(self) -> Optional[Dict[str, Any]]:
        """获取当前阶段"""
        if 0 <= self.current_stage_index < len(self.stages):
            return self.stages[self.current_stage_index]
        return None

    def set_stage_outputs(self, stage_id: int, outputs: Dict[str, Any]) -> bool:
        """设置阶段输出"""
        stage = self.get_stage_by_id(stage_id)
        if stage:
            stage["outputs"] = outputs
            stage["status"] = "completed"
            self.updated_at = time.time()

            if stage_id == self.current_stage_index + 1:
                self.current_stage_index = stage_id

            if all(s["status"] == "completed" for s in self.stages):
                self.status = "completed"
            else:
                self.status = "in_progress"

            return True
        return False

    def get_stage_outputs(self, stage_id: int) -> Optional[Dict[str, Any]]:
        """获取阶段输出"""
        stage = self.get_stage_by_id(stage_id)
        if stage:
            return stage.get("outputs")
        return None

    def get_next_stage(self, current_stage_id: int) -> Optional[Dict[str, Any]]:
        """获取下一个阶段"""
        return self.get_stage_by_id(current_stage_id + 1)

    def get_final_output(self) -> Optional[Dict[str, Any]]:
        """获取最终输出（最后一个完成阶段的输出）"""
        for stage in reversed(self.stages):
            if stage["status"] == "completed" and stage.get("outputs"):
                return stage["outputs"]
        return None

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        return {
            "plan_id": self.plan_id,
            "global_instruction": self.global_instruction,
            "stages": self.stages,
            "current_stage_index": self.current_stage_index,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MultiStagePlan":
        """从字典恢复"""
        plan = cls(
            plan_id=data["plan_id"],
            global_instruction=data["global_instruction"],
            metadata=data.get("metadata", {}),
        )
        plan.stages = data.get("stages", [])
        plan.current_stage_index = data.get("current_stage_index", 0)
        plan.status = data.get("status", "pending")
        plan.created_at = data.get("created_at", time.time())
        plan.updated_at = data.get("updated_at", time.time())
        return plan

    def get_summary(self) -> str:
        """获取计划摘要"""
        lines = [
            f"MultiStagePlan: {self.plan_id}",
            f"Status: {self.status}",
            f"Stages ({len(self.stages)} total):",
        ]
        for stage in self.stages:
            status_icon = {
                "pending": "⏳",
                "in_progress": "🔄",
                "completed": "✅",
                "failed": "❌",
            }.get(stage["status"], "❓")
            lines.append(
                f"  {status_icon} Stage {stage['stage_id']}: {stage['stage_name']} [{stage['skill_type']}]"
            )
        return "\n".join(lines)


MULTI_STAGE_PLAN: Optional[MultiStagePlan] = None


def create_multi_stage_plan(
    global_instruction: str,
    stages_data: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> MultiStagePlan:
    """创建新的多阶段计划。"""
    global MULTI_STAGE_PLAN

    plan_id = f"msp_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    MULTI_STAGE_PLAN = MultiStagePlan(
        plan_id=plan_id,
        global_instruction=global_instruction,
        metadata=metadata,
    )

    for stage_data in stages_data:
        MULTI_STAGE_PLAN.add_stage(
            stage_name=stage_data.get("stage_name", f"Stage_{len(MULTI_STAGE_PLAN.stages) + 1}"),
            skill_type=stage_data.get("skill_type", "VFX_EDIT"),
            description=stage_data.get("description", ""),
            plan=stage_data.get("plan"),
            reference_source=stage_data.get("reference_source"),
        )

    return MULTI_STAGE_PLAN


def get_multi_stage_plan() -> Optional[MultiStagePlan]:
    """获取当前多阶段计划"""
    return MULTI_STAGE_PLAN


def set_multi_stage_plan(plan: MultiStagePlan) -> None:
    """设置多阶段计划（用于恢复）"""
    global MULTI_STAGE_PLAN
    MULTI_STAGE_PLAN = plan


def clear_multi_stage_plan() -> None:
    """清除多阶段计划"""
    global MULTI_STAGE_PLAN
    MULTI_STAGE_PLAN = None


def is_multi_stage_mode() -> bool:
    """检查是否处于多阶段模式"""
    return MULTI_STAGE_PLAN is not None and len(MULTI_STAGE_PLAN.stages) > 0


def is_multi_stage_plan(plan: Any) -> bool:
    """判断一个 plan 是否是多阶段 Plan。"""
    if not isinstance(plan, dict):
        return False
    return "stages" in plan and isinstance(plan.get("stages"), list)



