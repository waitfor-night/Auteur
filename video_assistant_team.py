#!/usr/bin/env python3
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional, List

from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIResponses
from agno.team import Team

from utils.trace_recorder import TraceRecorder
from utils.context_recorder import RunContext
from tools.submitPlanTools.sub_implement import get_multi_stage_plan


def make_model():
    return OpenAIResponses(
        id="doubao-seed-2-0-pro-260215",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ.get("ARK_API_KEY"),
    )


class TeamVideoAssistant:
    """
    Team 版本 VideoAssistant。
    在 team.py 中以 leader 协调 planner/actor 的方式实现完整流程。
    """

    _PROJECT_ROOT = Path(__file__).resolve().parent
    _CONTEXT_DIR = _PROJECT_ROOT / "workspace" / "context"
    _TRACE_DIR = _PROJECT_ROOT / "workspace" / "trace"

    def __init__(
        self,
        output_dir: str,
        time_length: int = 15,
        total_duration: Optional[int] = None,
        allow_interactive: bool = True,
        planner_only: bool = False,
        context_id: Optional[str] = None,
        username: Optional[str] = None,
        context_subdir: Optional[str] = None,
    ):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.time_length = time_length
        self.total_duration = total_duration
        self.allow_interactive = allow_interactive
        self.planner_only = planner_only
        self.username = username
        self.context_subdir = context_subdir

        if username:
            self._CONTEXT_DIR = self._PROJECT_ROOT / "workspace" / username / "context"
            self._TRACE_DIR = self._PROJECT_ROOT / "workspace" / username / "trace"
            if context_subdir:
                self._CONTEXT_DIR = self._CONTEXT_DIR / context_subdir
                self._TRACE_DIR = self._TRACE_DIR / context_subdir
            self._CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
            self._TRACE_DIR.mkdir(parents=True, exist_ok=True)

        RunContext.set_context_dir(self._CONTEXT_DIR)
        self.user_memory: Optional[str] = self._load_user_memory()
        self.planner = self._init_planner()
        self.actor = self._init_actor()
        self.context = self._init_context(context_id)
        self.team = self._init_team()

        self.user_input: Optional[str] = None
        self.video_path: str = ""
        self.image_paths: List[str] = []
        self.current_plan: Optional[dict] = None
        self.start_time: Optional[float] = None
        self.current_round: int = 0
        self.result: Optional[dict] = None

    def _load_user_memory(self) -> Optional[str]:
        """读取用户 memory 文件，不存在则返回 None。"""
        if not self.username:
            return None
        memory_path = self._PROJECT_ROOT / "workspace" / self.username / "memory" / "memory.md"
        if not memory_path.is_file():
            return None
        content = memory_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        print(f"[Memory] 已加载用户 memory: {memory_path}", flush=True)
        return content

    def _init_planner(self):
        """初始化 Planner Agent，若有用户 memory 则注入到 system prompt。"""
        from planner import planner_agent, build_planner_with_memory

        if self.user_memory:
            return build_planner_with_memory(self.user_memory)
        return planner_agent

    def _init_actor(self):
        """初始化 Actor Agent。"""
        from actor import actor_agent

        return actor_agent

    def _init_context(self, context_id: Optional[str] = None) -> RunContext:
        context_dir = str(self._CONTEXT_DIR)
        if context_id:
            return RunContext.load_from_file(
                context_id,
                context_dir=context_dir,
            )
        return RunContext(
            output_dir=str(self.output_dir),
            context_dir=context_dir,
            username=self.username,
        )

    def _init_team(self):
        db_dir = self._PROJECT_ROOT / "tmp"
        db_dir.mkdir(parents=True, exist_ok=True)

        return Team(
            name="video-assistant-plan-actor-team",
            model=make_model(),
            members=[self.planner, self.actor],
            db=SqliteDb(db_file=str(db_dir / "video_assistant_team.db")),
            session_id=self.context.context_id,
            markdown=True,
            description="你是团队领导者，先分配任务，再整合 Planner 与 Actor 的结果。",
            instructions=[
                "必须采用协调模式（非路由）。",
                "先让 Planner 产出 Multi-Stage Plan（submit_multi_stage_plan）。",
                "再让 Actor 执行 Multi-Stage Plan。",
                "若 planner_only=True，只调用 Planner，不调用 Actor。",
                "最终输出给出计划状态、执行状态、产物路径或失败原因。",
            ],
            add_history_to_context=True,
            num_history_runs=3,
            add_team_history_to_members=True,
            num_team_history_runs=3,
            respond_directly=False,
            determine_input_for_members=True,
            retries=3,
            delay_between_retries=10,
            exponential_backoff=True,
            store_member_responses=True,
            # 显示成员响应
            show_members_responses=False,
        )

    def _set_run_context_globals(self):
        import planner as planner_module
        from tools import constants

        planner_module.RUN_CONTEXT_IMAGE_PATHS = self.image_paths
        planner_module.RUN_CONTEXT_VIDEO_PATH = self.video_path if self.video_path else None
        constants.RUN_CONTEXT_IMAGE_PATHS = self.image_paths
        constants.RUN_CONTEXT_VIDEO_PATH = self.video_path if self.video_path else None

    def _build_user_message(self) -> str:
        base_message = (
            "### Runtime Inputs\n"
            f"- InitUserMessage: {self.user_input}\n"
            f"- time_length: {self.time_length}\n"
        )
        if self.total_duration:
            base_message += f"- total_duration: {self.total_duration}\n"
        else:
            base_message += "- total_duration: (无，使用默认每段 time_length 秒)\n"
        base_message += "- use_auto_split_pipeline: True\n"
        base_message += f"- video_path: {self.video_path or '(无)'}\n"
        base_message += f"- image_paths: {self.image_paths}\n"
        base_message += f"- output_dir: {self.output_dir}\n"

        # NOTE:
        # Manual history stitching is intentionally disabled.
        # Team-level session+db history management is used instead.
        # history_prompt = self.context.build_history_prompt()
        # if history_prompt and self.current_round > 1:
        #     return f"{history_prompt}\n\n---\n\n{base_message}"
        return base_message

    def _init_trace_recorder(self) -> TraceRecorder:
        recorder = TraceRecorder(base_dir=str(self._TRACE_DIR), username=self.username)
        recorder.set_meta_info(
            video_path=self.video_path,
            instruction=self.user_input[:500] + ("..." if len(self.user_input or "") > 500 else ""),
            image_paths=self.image_paths,
            time_length=self.time_length,
        )
        return recorder

    def _split_team_tools(self, run_response: Any) -> tuple[Optional[list], Optional[list]]:
        member_tools = {}
        planner_name = getattr(self.planner, "name", None)
        actor_name = getattr(self.actor, "name", None)

        for member_response in getattr(run_response, "member_responses", None) or []:
            key = (
                getattr(member_response, "agent_name", None)
                or getattr(member_response, "team_name", None)
                or getattr(member_response, "agent_id", None)
                or "unknown"
            )
            member_tools[key] = getattr(member_response, "tools", []) or []

        return member_tools.get(planner_name) or None, member_tools.get(actor_name) or None

    @staticmethod
    def _build_stage_summary(multi_stage_plan: dict) -> list[dict]:
        return [
            {
                "stage_id": stage.get("stage_id"),
                "stage_name": stage.get("stage_name"),
                "skill_type": stage.get("skill_type"),
                "status": stage.get("status"),
            }
            for stage in (multi_stage_plan.get("stages") or [])
        ]

    def _run_team_round(self):
        user_message = self._build_user_message()
        team_input = (
            "请按协调模式完成本轮任务。\n"
            f"planner_only={'True' if self.planner_only else 'False'}\n\n"
            f"{user_message}\n\n"
            f"[Path rule] cwd={self.output_dir}，使用相对路径。"
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(str(self.output_dir))
            run_response = self.team.run(team_input, stream=False, session_id=self.context.context_id)
        finally:
            os.chdir(original_cwd)

        output = run_response.content if run_response and run_response.content else ""
        planner_tools, actor_tools = self._split_team_tools(run_response)

        plan = None
        msp = get_multi_stage_plan()
        if msp:
            plan = msp.to_dict()
            plan["_is_multi_stage"] = True
        return plan, planner_tools, actor_tools, output

    def _get_user_feedback(self) -> Optional[str]:
        if not self.allow_interactive:
            return None
        try:
            prompt = "请输入反馈（回车=满意）: "
            try:
                with open("/dev/tty", "r") as tty:
                    print(prompt, end="", flush=True)
                    user_input = tty.readline()
            except OSError:
                user_input = input(prompt)
            user_input = (user_input or "").strip()
            return user_input if user_input else None
        except (EOFError, KeyboardInterrupt):
            print("\n用户中断，结束流程", flush=True)
            return None

    def _team_judge_need_next_round(self, user_feedback: str) -> bool:
        """
        使用 Team（leader 协调）判断是否进入下一轮。
        返回 True 表示继续，False 表示结束。
        """
        judge_input = (
            "这是用户对上一轮结果的反馈，请判断是否还需要继续迭代。\n"
            f"用户反馈：{user_feedback}\n\n"
            "只输出一个词：NEED_REVISION 或 SATISFIED。"
        )
        run_response = self.team.run(judge_input, stream=False, session_id=self.context.context_id)
        result = (run_response.content or "").strip().upper()
        return "NEED_REVISION" in result or "REVISION" in result

    def run_single_round(self) -> dict:
        round_start = time.time()
        self.current_round = self.context.start_new_round()
        self.context.set_current()

        trace_recorder = self._init_trace_recorder()
        trace_recorder.set_episode()

        plan, planner_tools, actor_tools, output = self._run_team_round()
        if plan is None:
            self.context.set_error("未能获取 Multi-Stage Plan")
            return {"success": False, "error": "未能获取 Multi-Stage Plan", "round": self.current_round}

        self.current_plan = plan
        self.context.set_round_plan(plan)
        self.context.set_round_planner_tools(planner_tools)
        self.context.set_round_actions([{"output": output}])
        self.context.set_round_actor_tools(actor_tools)

        timeline = plan.get("timeline", [])
        multi = deepcopy(plan) if plan.get("_is_multi_stage") and plan.get("stages") else None
        trace_recorder.log_plan(raw_segments=None, refined_segments=timeline, multi_stage_plan=multi)
        if multi:
            msp_after_team = get_multi_stage_plan()
            final_plan = msp_after_team.to_dict() if msp_after_team else multi
            trace_recorder.log_plan(
                raw_segments=None,
                refined_segments=timeline,
                multi_stage_plan=final_plan,
            )
            trace_recorder.set_plan_result(
                {
                    "status": final_plan.get("status"),
                    "stages": self._build_stage_summary(final_plan),
                }
            )
        trace_recorder.set_final_output(output)
        trace_path = os.path.join(trace_recorder.base_dir, f"{trace_recorder.episode_id}.json")

        return {
            "success": True,
            "plan": plan,
            "tools_execute_order": planner_tools,
            "actor_tools": actor_tools,
            "output": output,
            "round": self.current_round,
            "elapsed_sec": time.time() - round_start,
            "trace_path": trace_path,
        }

    def _run_multi_round_with_team(self, max_rounds: int = 10) -> dict:
        """
        递归执行多轮，避免 while 循环；轮次决策由 Team 完成。
        """
        if self.current_round >= max_rounds:
            return {
                "success": True,
                "output_path": "",
                "context_id": self.context.context_id,
                "total_rounds": self.current_round,
                "duration_sec": time.time() - (self.start_time or time.time()),
                "error": "达到最大轮次限制，已自动结束",
            }

        round_result = self.run_single_round()
        if not round_result["success"]:
            return {
                "success": False,
                "error": round_result.get("error"),
                "context_id": self.context.context_id,
                "total_rounds": self.current_round,
                "duration_sec": time.time() - (self.start_time or time.time()),
            }

        final_output = round_result.get("output") or ""

        if self.planner_only:
            self.context.set_result("planner_only")
            return {
                "success": True,
                "output_path": None,
                "plan": round_result.get("plan"),
                "context_id": self.context.context_id,
                "total_rounds": self.current_round,
                "duration_sec": time.time() - (self.start_time or time.time()),
                "error": None,
            }

        user_feedback = self._get_user_feedback()
        self.context.set_round_feedback(user_feedback if user_feedback else None)
        if not user_feedback:
            self.context.set_round_satisfied(True)
            return {
                "success": True,
                "output_path": final_output,
                "context_id": self.context.context_id,
                "total_rounds": self.current_round,
                "duration_sec": time.time() - (self.start_time or time.time()),
                "error": None,
            }

        need_next = self._team_judge_need_next_round(user_feedback)
        if not need_next:
            self.context.set_round_satisfied(True)
            return {
                "success": True,
                "output_path": final_output,
                "context_id": self.context.context_id,
                "total_rounds": self.current_round,
                "duration_sec": time.time() - (self.start_time or time.time()),
                "error": None,
            }

        # 将用户反馈拼接到下一轮输入，形成自然延续
        self.user_input = f"{self.user_input}\n\n[用户反馈]\n{user_feedback}"
        self.context.set_user_input(self.user_input)
        return self._run_multi_round_with_team(max_rounds=max_rounds)

    def run(
        self,
        user_input: Optional[str] = None,
        video_path: str = "",
        image_paths: Optional[List[str]] = None,
    ) -> dict:
        self.start_time = time.time()

        if user_input is not None:
            self.user_input = user_input
            self.context.set_user_input(user_input)
        elif self.context.get_user_input():
            self.user_input = self.context.get_user_input()
        else:
            return {"success": False, "error": "未提供 user_input 且 context 中无历史输入", "context_id": self.context.context_id}

        self.video_path = video_path
        self.image_paths = image_paths or []
        self._set_run_context_globals()

        self.result = self._run_multi_round_with_team(max_rounds=10)
        if self.result.get("success"):
            if self.planner_only:
                self.context.set_result("planner_only")
            else:
                self.context.set_result(self.result.get("output_path") or "")
        return self.result


if __name__ == "__main__":
    import argparse
    from skills.skill_loader import ALL_SKILLS, SKILL_DESCRIPTIONS
    from prompts import PLANNER_AGENT_PROMPT

    parser = argparse.ArgumentParser(description="VideoAssistant_team 测试")
    parser.add_argument("--user-input", type=str, required=False, help="用户输入")
    parser.add_argument("--video-path", type=str, default="", help="输入视频路径")
    parser.add_argument("--images", nargs="+", default=[], help="参考图路径")
    parser.add_argument("--output-dir", type=str, default="workspace/output/computer_team1", help="输出目录")
    parser.add_argument("--time-length", type=int, default=5, help="每段时长")
    parser.add_argument("--total-duration", type=int, default=None, help="总时长")
    parser.add_argument("--planner-only", action="store_true", help="只运行 Planner")
    parser.add_argument("--context-id", type=str, default=None, help="恢复会话")
    parser.add_argument("--print-planner-prompt", action="store_true", help="启动时打印初始化后的 PLANNER_AGENT_PROMPT（core + meta-skill）")
    parser.add_argument("--print-skills", action="store_true", help="启动时打印 ALL_SKILLS 与 SKILL_DESCRIPTIONS（调试用）")
    args = parser.parse_args()

    assistant = TeamVideoAssistant(
        output_dir=args.output_dir,
        time_length=args.time_length,
        total_duration=args.total_duration,
        planner_only=args.planner_only,
        context_id=args.context_id,
    )
    demo = "请生成一个15秒未来感科技宣传片，突出隐私计算与数据协同。"
    result = assistant.run(
        user_input=demo,
        video_path=args.video_path,
        image_paths=args.images,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
