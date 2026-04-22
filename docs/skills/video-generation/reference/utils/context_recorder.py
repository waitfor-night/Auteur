"""
RunContext: 运行上下文记录器，支持多轮迭代的短期记忆。

用于记录 Agent 多轮迭代过程中的 Plan、Act、Feedback，
并可以构建历史 prompt 用于下一轮迭代。

用法示例：
    from utils.run_context import RunContext
    
    # 创建新的 Context
    ctx = RunContext(script_name="my_script", output_dir="/path/to/output")
    
    # 开始新的一轮
    round_num = ctx.start_new_round()
    
    # 记录 Plan
    ctx.set_round_plan(plan_dict)
    
    # 记录执行动作
    ctx.set_round_actions(actions_list)
    
    # 记录用户反馈
    ctx.set_round_feedback("用户的修改意见")
    
    # 构建历史 prompt 用于下一轮
    history_prompt = ctx.build_history_prompt()
    
    # 从文件恢复
    ctx = RunContext.load_from_file("ctx_1234567890_abcd1234")
"""
import datetime
import json
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Optional, Union

# 当前活跃的 RunContext 实例，由 VideoAssistant 在每轮开始前注入
_run_context_var: ContextVar["RunContext | None"] = ContextVar(
    "run_context_instance", default=None
)


class RunContext:
    """
    运行上下文记录器：支持多轮迭代的短期记忆。
    
    结构：
    - rounds: 多轮迭代记录
      - Round N:
        - plan: 当前轮次的 Plan（全文或摘要）
        - act: 执行的操作及结果
        - feedback: 用户反馈或系统评估
    """
    
    # 默认 context 目录（可通过 set_context_dir 修改）
    _context_dir: Optional[Path] = None

    def __init__(
        self,
        output_dir: str = "",
        context_dir: Optional[Union[str, Path]] = None,
        username: Optional[str] = None,
    ):
        """
        初始化 RunContext。

        Args:
            output_dir: 输出目录路径
            context_dir: Context 保存目录（可选，默认使用类级别设置或 ./context）
            username: 产生该 context 的用户名，写入文件内容以支持跨目录过滤
        """
        self.context_id = f"ctx_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        self.start_time = datetime.datetime.now()
        self.current_round = 0
        self.data = {
            "context_id": self.context_id,
            "username": username,
            "output_dir": output_dir,
            "start_time": self.start_time.isoformat(),
            "end_time": None,
            "status": "running",
            "user_input": None,  # 用户原始输入（剧本内容、任务描述等）
            "total_rounds": 0,
            "rounds": [],
            "result_video": None,
            "error": None,
        }
        
        # 设置 context 目录
        if context_dir:
            self._instance_context_dir = Path(context_dir)
        else:
            self._instance_context_dir = None
        
        # 确保目录存在并保存
        self._get_context_dir().mkdir(parents=True, exist_ok=True)
        self._save()

    @classmethod
    def set_context_dir(cls, context_dir: Union[str, Path]) -> None:
        """设置全局 context 目录。"""
        cls._context_dir = Path(context_dir)

    def set_current(self) -> None:
        """将自身注入 ContextVar，供 tools 在运行时直接写入当前 round。"""
        _run_context_var.set(self)

    @staticmethod
    def get_current() -> "RunContext | None":
        """返回当前 ContextVar 中的 RunContext 实例，供 tools 调用。"""
        return _run_context_var.get()

    def _get_context_dir(self) -> Path:
        """获取 context 目录路径。"""
        if self._instance_context_dir:
            return self._instance_context_dir
        if RunContext._context_dir:
            return RunContext._context_dir
        # 默认使用当前工作目录下的 context 目录
        return Path("./context")

    def set_user_input(self, user_input: str) -> None:
        """
        设置用户的原始输入。
        
        Args:
            user_input: 用户原始输入内容（如剧本内容、任务描述等）
        """
        self.data["user_input"] = user_input
        self._save()

    def get_user_input(self) -> Optional[str]:
        """
        获取用户的原始输入。
        
        Returns:
            用户输入的内容字符串，如果没有设置则返回 None
        """
        return self.data.get("user_input")

    def start_new_round(self) -> int:
        """开始新的一轮迭代，返回轮次编号（从 1 开始）。"""
        self.current_round += 1
        self.data["total_rounds"] = self.current_round
        round_data = {
            "round": self.current_round,
            "timestamp": datetime.datetime.now().isoformat(),
            "plan": None,
            "act": [],
            "feedback": None,
        }
        self.data["rounds"].append(round_data)
        self._save()
        return self.current_round

    def set_round_plan(self, plan: dict, summary: Optional[str] = None) -> None:
        """记录当前轮次的 Plan。"""
        if not self.data["rounds"]:
            self.start_new_round()
        
        current = self.data["rounds"][-1]
        current["plan"] = plan
        if summary:
            current["plan_summary"] = summary
        self._save()

    def add_round_action(self, action: dict) -> None:
        """记录当前轮次的一个执行动作。"""
        if not self.data["rounds"]:
            self.start_new_round()
        
        current = self.data["rounds"][-1]
        if current["act"] is None:
            current["act"] = []
        current["act"].append(action)
        self._save()

    def set_round_actions(self, actions: list) -> None:
        """批量设置当前轮次的所有执行动作。"""
        if not self.data["rounds"]:
            self.start_new_round()
        
        current = self.data["rounds"][-1]
        current["act"] = actions
        self._save()

    def set_round_planner_tools(self, tools: Optional[list]) -> None:
        """记录当前轮次 Planner 执行的工具列表 (run_response.tools)。"""
        if not self.data["rounds"]:
            self.start_new_round()
        
        current = self.data["rounds"][-1]
        current["planner_tools"] = tools
        self._save()

    def set_round_feedback(self, user_feedback: Optional[str] = None) -> None:
        """记录当前轮次的用户反馈。"""
        if not self.data["rounds"]:
            return

        current = self.data["rounds"][-1]
        current["feedback"] = user_feedback
        self._save()

    def add_round_skill(self, name: str, content: str) -> None:
        """追加一条 skill 记录到当前 round（每次 load_skill_tool 调用时直接写入）。"""
        if not self.data["rounds"]:
            return
        current = self.data["rounds"][-1]
        if "skill_loaded" not in current:
            current["skill_loaded"] = []
        current["skill_loaded"].append({"name": name, "content": content})
        self._save()

    def set_round_actor_tools(self, tools) -> None:
        """记录 Actor 本轮实际调用的 tool 序列。仅供学习用，不进 build_history_prompt。"""
        if not self.data["rounds"]:
            return
        self.data["rounds"][-1]["actor_tools"] = tools
        self._save()

    def set_round_satisfied(self, satisfied: bool) -> None:
        """标记当前轮次是否以用户满意结束。仅最后一轮写入。"""
        if not self.data["rounds"]:
            return
        self.data["rounds"][-1]["satisfied"] = satisfied
        self._save()

    def set_result(self, result_video: str) -> None:
        """设置最终结果视频路径。"""
        self.data["result_video"] = result_video
        self.data["status"] = "completed"
        self.data["end_time"] = datetime.datetime.now().isoformat()
        self._save()

    def set_error(self, error_msg: str) -> None:
        """记录错误信息。"""
        self.data["error"] = error_msg
        self.data["status"] = "failed"
        self.data["end_time"] = datetime.datetime.now().isoformat()
        self._save()

    def get_current_round(self) -> int:
        """获取当前轮次编号。"""
        return self.current_round

    def get_round_data(self, round_num: int) -> Optional[dict]:
        """获取指定轮次的数据。"""
        if 1 <= round_num <= len(self.data["rounds"]):
            return self.data["rounds"][round_num - 1]
        return None

    def get_all_rounds_summary(self) -> list:
        """获取所有轮次的摘要信息。"""
        summaries = []
        for r in self.data["rounds"]:
            plan = r.get("plan") or {}
            act = r.get("act") or []
            summaries.append({
                "round": r["round"],
                "timeline_count": len(plan.get("timeline", [])) if isinstance(plan, dict) else 0,
                "action_count": len(act) if isinstance(act, list) else 0,
                "has_feedback": r.get("feedback") is not None,
            })
        return summaries

    def _save(self) -> None:
        """保存到 context 目录。"""
        try:
            context_dir = self._get_context_dir()
            context_dir.mkdir(parents=True, exist_ok=True)
            file_path = context_dir / f"{self.context_id}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[RunContext] 写入失败: {e}")

    def get_file_path(self) -> str:
        """返回 context 文件路径。"""
        return str(self._get_context_dir() / f"{self.context_id}.json")

    def build_history_prompt(self, exclude_current: bool = True) -> str:
        """
        构建历史轮次的 prompt，作为 Planner 的短期记忆。
        直接返回每轮的全量 plan（JSON）和全量 actor 结果（JSON），不组织格式。

        Args:
            exclude_current: 是否排除当前轮次（默认 True，只包含已完成的轮次）
        """
        if not self.data["rounds"]:
            return ""

        # 确定要包含的轮次
        rounds_to_include = self.data["rounds"]
        if exclude_current and len(rounds_to_include) > 0:
            last_round = rounds_to_include[-1]
            if last_round.get("plan") is None:
                rounds_to_include = rounds_to_include[:-1]

        if not rounds_to_include:
            return ""

        lines = [
            "## Previous Rounds History (Short-term Memory)",
            "",
        ]

        for r in rounds_to_include:
            round_num = r.get("round", 0)
            lines.append(f"### Round {round_num}")
            lines.append("#### Plan")
            plan = r.get("plan")
            if plan and isinstance(plan, dict):
                lines.append(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
            else:
                lines.append("(No plan recorded)")
            lines.append("")
            lines.append("#### Act")
            act = r.get("act") or []
            if act and isinstance(act, list):
                lines.append(json.dumps(act, ensure_ascii=False, indent=2, default=str))
            else:
                lines.append("(No actions recorded)")
            lines.append("")
            lines.append("#### User Feedback")
            feedback = r.get("feedback")
            lines.append(feedback if feedback else "(No feedback provided)")
            lines.append("")

        return "\n".join(lines)

    @classmethod
    def load_from_file(
        cls, 
        context_id: str, 
        context_dir: Optional[Union[str, Path]] = None
    ) -> "RunContext":
        """
        从文件加载已有的 Context。
        
        Args:
            context_id: Context ID（如 ctx_1234567890_abcd1234）
            context_dir: Context 目录路径（可选，默认使用类级别设置或 ./context）
        """
        # 确定 context 目录
        if context_dir:
            ctx_dir = Path(context_dir)
        elif cls._context_dir:
            ctx_dir = cls._context_dir
        else:
            ctx_dir = Path("./context")
        
        file_path = ctx_dir / f"{context_id}.json"
        if not file_path.exists():
            raise FileNotFoundError(f"Context file not found: {file_path}")
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 创建实例并恢复状态
        instance = cls.__new__(cls)
        instance.context_id = data.get("context_id", context_id)
        instance.data = data
        instance.current_round = data.get("total_rounds", 0)
        instance.start_time = datetime.datetime.fromisoformat(
            data.get("start_time", datetime.datetime.now().isoformat())
        )
        instance._instance_context_dir = ctx_dir
        return instance

    # 兼容旧接口
    def set_plan(self, plan: dict) -> None:
        """兼容旧接口：记录 Plan（自动开始新轮次）。"""
        if not self.data["rounds"]:
            self.start_new_round()
        self.set_round_plan(plan)

    def add_actor_action(self, action: dict) -> None:
        """兼容旧接口：记录 Actor 动作。"""
        self.add_round_action(action)

    def to_dict(self) -> dict:
        """返回完整的 context 数据字典。"""
        return self.data.copy()

    def __repr__(self) -> str:
        return (
            f"RunContext(id={self.context_id}, "
            f"rounds={self.current_round}, "
            f"status={self.data.get('status', 'unknown')})"
        )
