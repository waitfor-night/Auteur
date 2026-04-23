#!/usr/bin/env python3
"""
VideoAssistant: 视频编辑/生成流程运行器。

支持多种任务场景：
- 剧本生成视频：从剧本文本生成长视频
- 视频编辑：对已有视频进行编辑
- 纯生成：无输入视频，纯 AI 生成
- 视频延长：对视频进行延长

支持多轮迭代：
- 用户反馈 → VLM 判断是否满意 → 继续下一轮
- Context 持久化，支持会话恢复

用法示例：
    from video_assistant import VideoAssistant

    # 剧本生成视频
    assistant = VideoAssistant(
        output_dir="output/my_video",
        time_length=10,
        total_duration=60,
    )
    result = assistant.run(
        user_input="剧本内容...",
        image_paths=["char_1.png", "scene_1.png"],
    )

    # 视频编辑
    assistant = VideoAssistant(output_dir="output/edited", time_length=15)
    result = assistant.run(
        user_input="将视频风格改为赛博朋克",
        video_path="input.mp4",
        image_paths=["ref.png"],
    )

    # 恢复会话
    assistant = VideoAssistant(
        output_dir="output/my_video",
        context_id="ctx_1772184523_ef788521",
    )
    result = assistant.run()
"""
import json
import os
import sys
import time
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
load_dotenv()
# 保证项目根在 path，后续 import utils / tools 
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 启用 readline 以在交互时支持退格、左右键等行编辑（仅 Unix、且 stdin 为 TTY 时生效）
try:
    import readline  # noqa: F401
except ImportError:
    pass

from utils.trace_recorder import TraceRecorder
from utils.context_recorder import RunContext
from tools.submitPlanTools.sub_implement import get_multi_stage_plan


class VideoAssistant:
    """
    视频编辑/生成流程运行器。
    
    封装完整的 Planner + Actor 流程，支持多轮迭代和 Context 持久化。
    """

    # 默认目录
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
        """
        初始化 VideoAssistant。

        Args:
            output_dir: 输出目录路径
            time_length: 每段视频最大时长（秒），默认 15
            total_duration: 视频总时长（秒），可选
            allow_interactive: 是否允许交互（用户反馈），默认 True
            planner_only: 是否只运行 Planner（调试用），默认 False
            context_id: 恢复已有会话的 Context ID，可选
            username: 按用户隔离时传入用户名；context 写 workspace/<username>/context/，trace 写 workspace/<username>/trace/
            context_subdir: 可选子目录，使 context 写 workspace/<username>/context/<context_subdir>/，实现「一 task 一 context」
        """
        # 基础配置
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.time_length = time_length
        self.total_duration = total_duration
        self.allow_interactive = allow_interactive
        self.planner_only = planner_only
        self.username = username
        self.context_subdir = context_subdir

        # 按用户隔离：context 与 trace 目录；可再加子目录（一 task 一 context）
        if username:
            self._CONTEXT_DIR = self._PROJECT_ROOT / "workspace" / username / "context"
            self._TRACE_DIR = self._PROJECT_ROOT / "workspace" / username / "trace"
            if context_subdir:
                self._CONTEXT_DIR = self._CONTEXT_DIR / context_subdir
                self._TRACE_DIR = self._TRACE_DIR / context_subdir
            self._CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
            self._TRACE_DIR.mkdir(parents=True, exist_ok=True)

        # 设置 RunContext 全局目录
        RunContext.set_context_dir(self._CONTEXT_DIR)

        # 加载用户 memory（workspace/<username>/memory/memory.md）
        self.user_memory: Optional[str] = self._load_user_memory()
        # 加载账号内容策略（workspace/<username>/content_strategy.md）
        self.content_strategy: Optional[str] = self._load_content_strategy()

        # 初始化 Agents
        self.planner = self._init_planner()
        self.actor = self._init_actor()

        # 初始化或恢复 Context
        self.context = self._init_context(context_id)

        # 运行时状态
        self.user_input: Optional[str] = None
        self.video_path: str = ""
        self.image_paths: List[str] = []
        self.ref_videos: List[str] = []
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
        print(f"[Memory] 已加载用户 memory：{memory_path}", flush=True)
        return content

    def _load_content_strategy(self) -> Optional[str]:
        """读取账号内容策略文件，不存在则返回 None。"""
        if not self.username:
            return None
        strategy_path = self._PROJECT_ROOT / "workspace" / self.username / "content_strategy.md"
        if not strategy_path.is_file():
            return None
        content = strategy_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        print(f"[Strategy] 已加载内容策略：{strategy_path}", flush=True)
        return content

    def _init_planner(self):
        """初始化 Planner Agent，按需注入 memory 和/或 content_strategy。"""
        from planner import planner_agent, build_planner_agent
        if self.user_memory or self.content_strategy:
            return build_planner_agent(
                user_memory=self.user_memory,
                content_strategy=self.content_strategy,
            )
        return planner_agent

    def _init_actor(self):
        """初始化 Actor Agent。"""
        from actor import actor_agent
        return actor_agent

    def _init_context(self, context_id: Optional[str] = None) -> RunContext:
        """初始化或恢复 Context。"""
        context_dir = str(self._CONTEXT_DIR)
        if context_id:
            return RunContext.load_from_file(context_id, context_dir=context_dir)
        return RunContext(
            output_dir=str(self.output_dir),
            context_dir=context_dir,
            username=self.username,
        )

    def _init_trace_recorder(self) -> TraceRecorder:
        """初始化 TraceRecorder。"""
        recorder = TraceRecorder(base_dir=str(self._TRACE_DIR), username=self.username)
        recorder.set_meta_info(
            video_path=self.video_path,
            instruction=self.user_input[:500] + ("..." if len(self.user_input or "") > 500 else ""),
            image_paths=self.image_paths,
            time_length=self.time_length,
        )
        return recorder

    def _set_run_context_globals(self):
        """设置运行上下文全局变量（供 Planner 和 Tools 使用）。"""
        import planner as planner_module
        import utils.tools_implement as tools_impl
        from tools import constants as tools_constants

        planner_module.RUN_CONTEXT_IMAGE_PATHS = self.image_paths
        planner_module.RUN_CONTEXT_VIDEO_PATH = self.video_path if self.video_path else None
        tools_impl.RUN_CONTEXT_IMAGE_PATHS = self.image_paths
        tools_impl.RUN_CONTEXT_VIDEO_PATH = self.video_path if self.video_path else None
        tools_constants.RUN_CONTEXT_REF_VIDEOS = self.ref_videos or None

    def _build_user_message(self) -> str:
        """构建当前轮次的 user_message（含历史记忆）。"""
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
        if self.video_path:
            base_message += f"- video_path: {self.video_path}\n"
        else:
            base_message += "- video_path: (无)\n"
        base_message += f"- image_paths: {self.image_paths}\n"
        if self.ref_videos:
            base_message += f"- ref_videos: {self.ref_videos}\n"
        else:
            base_message += "- ref_videos: (无)\n"
        base_message += f"- output_dir: {self.output_dir}\n"

        # 添加历史记忆
        history_prompt = self.context.build_history_prompt()
        if history_prompt and self.current_round > 1:
            return f"{history_prompt}\n\n---\n\n{base_message}"
        return base_message

    def _run_planner(self) -> tuple[Optional[dict], Optional[list]]:
        """
        运行 Planner，返回 Plan 和工具执行列表。
        
        Returns:
            (plan, tools) 元组:
                - plan: Plan 字典，如果失败返回 None
                - tools_execute_order: run_response.tools 工具执行列表
        """
        from planner import run_planner_with_user_input

        user_message = self._build_user_message()
        
        print("\n[Step 1] 运行 Planner 生成执行计划...", flush=True)
        planner_start = time.time()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(self.output_dir))
            run_response = run_planner_with_user_input(
                user_message,
                stream=False,
                use_print_response=False,
                use_task_specific_prompt=False,  # False: Agent 通过 load_skill_tool 动态加载 skill
                allow_interactive=self.allow_interactive,
                video_path=self.video_path,
                image_paths=self.image_paths,
                ref_videos=self.ref_videos,
                time_length=self.time_length,
                total_duration=self.total_duration,
                InitUserMessage=self.user_input,
                use_auto_split_pipeline=True,
            )
        finally:
            os.chdir(original_cwd)

        planner_elapsed = time.time() - planner_start
        print(f"\n[Step 1 完成] Planner 耗时: {planner_elapsed:.1f} 秒", flush=True)

        # 获取 Multi-Stage Plan（不再支持单阶段 get_plan / submit_video_execution_plan）
        plan = None
        multi_stage_plan = get_multi_stage_plan()
        if multi_stage_plan:
            plan = multi_stage_plan.to_dict()
            plan["_is_multi_stage"] = True
            print("\n[Info] 检测到多阶段 Plan，将按阶段顺序执行", flush=True)
            print(f"  - Plan ID: {multi_stage_plan.plan_id}", flush=True)
            print(f"  - 阶段数: {len(multi_stage_plan.stages)}", flush=True)

        # 获取工具执行列表
        tools_execute_order = run_response.tools if run_response else None

        if plan is None:
            print("\n错误: 未能获取 Multi-Stage Plan", flush=True)
            print("请检查 Planner 是否调用了 submit_multi_stage_plan（当前流程不再支持 submit_video_execution_plan 单阶段计划）。", flush=True)
            return None, tools_execute_order

        # 打印 Plan
        print("\n[已生成的 Multi-Stage Plan]", flush=True)
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)

        # 统计信息（多阶段）
        stages = plan.get("stages", [])
        print(f"\n  - 多阶段 Plan 阶段数: {len(stages)}", flush=True)
        for stage in stages:
            stage_plan = stage.get("plan", {})
            stage_timeline = stage_plan.get("timeline", []) if stage_plan else []
            print(
                f"    - Stage {stage['stage_id']} ({stage['stage_name']}): {len(stage_timeline)} 个 timeline 项",
                flush=True,
            )
        if tools_execute_order:
            print(f"  - Planner 工具调用数: {len(tools_execute_order)}", flush=True)

        return plan, tools_execute_order

    def _run_actor(self, plan: dict) -> str:
        """
        运行 Actor 执行计划（仅多阶段 Plan）。

        Args:
            plan: Multi-Stage Plan 字典（包含 stages）

        Returns:
            Actor 输出内容（通常是最终视频路径）
        """
        is_multi_stage = plan.get("_is_multi_stage", False)
        if not is_multi_stage:
            raise ValueError("当前流程仅支持多阶段计划（_is_multi_stage=True）。请使用 submit_multi_stage_plan 产出 stages。")

        print("\n[Step 2] 运行 Actor 执行多阶段计划...", flush=True)
        print(f"  - 阶段数: {len(plan.get('stages', []))}", flush=True)
        print("  - Actor 将自主管理各阶段的执行和中间输出", flush=True)
        actor_start = time.time()

        plan_to_send = {k: v for k, v in plan.items() if not k.startswith("_")}
        plan_json = json.dumps(plan_to_send, ensure_ascii=False)

        actor_input = (
            "Multi-Stage Plan (execute in order, self-check completion, then output tool-call sequence):\n"
            + plan_json
            + f"\n\n[Path rule] Current working directory is the output root: {self.output_dir}. "
            "Use **relative paths only** for save_path (merge_video_tool) and output_dir (generate_image_tool), "
            "e.g. 'long_video_2/merged.mp4', 'script_lens_1'. Do NOT use 'workspace/output/...' to avoid nested paths."
        )

        original_cwd = os.getcwd()
        try:
            os.chdir(str(self.output_dir))
            run_response = self.actor.run(actor_input, stream=False)
            result = run_response.content if run_response.content else ""
        finally:
            os.chdir(original_cwd)

        msp = get_multi_stage_plan()
        if msp:
            print(f"\n[多阶段执行完成] 状态: {msp.status}", flush=True)
            for stage in msp.stages:
                icon = "✅" if stage.get("status") == "completed" else "⏳"
                print(f"  {icon} Stage {stage.get('stage_id')}: {stage.get('stage_name')} - {stage.get('status')}", flush=True)

        actor_elapsed = time.time() - actor_start
        print(f"\n[Step 2 完成] Actor 耗时: {actor_elapsed:.1f} 秒", flush=True)

        actor_tools = getattr(run_response, "tools", None)
        return result, actor_tools

    def _get_user_feedback(self) -> Optional[str]:
        """
        从终端获取用户反馈。
        
        Returns:
            用户输入的反馈字符串，如果用户中断或不允许交互则返回 None
        """
        if not self.allow_interactive:
            return None

        print("\n" + "=" * 60, flush=True)
        print("[用户反馈] 视频生成完成，请查看结果并提供反馈：", flush=True)
        print("  - 输入 '满意' 或 'ok' 或直接回车：结束流程", flush=True)
        print("  - 输入修改意见：将根据反馈进行下一轮迭代", flush=True)
        print("=" * 60, flush=True)

        try:
            # 优先从控制终端 /dev/tty 读取，保证退格、左右键等行编辑正常（避免在管道/IDE 下 stdin 非 TTY 导致编辑异常）
            prompt = "请输入反馈: "
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

    def _vlm_judge_need_next_round(self, user_feedback: str) -> bool:
        """
        使用 VLM 判断用户反馈是否需要进行下一轮迭代。
        
        Args:
            user_feedback: 用户的反馈内容
            
        Returns:
            True 表示需要进行下一轮，False 表示用户满意不需要继续
        """
        from openai import OpenAI

        client = OpenAI(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=os.environ.get("ARK_API_KEY"),
        )

        prompt = f"""你是一个智能助手，需要判断用户是否还要对当前视频生成结果进行修改，以及是否还要在此基础上继续创作。

用户反馈："{user_feedback}"

请判断用户是否满意当前的视频生成结果：
- 如果你还有未完成的任务，或者用户提出了新的需求、修改意见等还需要进一步创作的反馈，返回 "NEED_REVISION"
- 如果用户表示满意、认可、没有修改意见、没有额外需求、没有进一步要求等，返回 "SATISFIED"

只返回 "SATISFIED" 或 "NEED_REVISION"，不要返回其他内容。"""

        try:
            response = client.chat.completions.create(
                model="doubao-1-5-pro-32k-250115",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
                temperature=0,
            )

            result = response.choices[0].message.content.strip().upper()
            print(f"[VLM 判断] 用户反馈分析结果: {result}", flush=True)

            return "NEED_REVISION" in result or "REVISION" in result

        except Exception as e:
            print(f"[VLM 判断] 调用失败: {e}，默认判断为需要修改", flush=True)
            return True

    def _generate_xhs_meta(self, task_description: str, result_video: str) -> tuple[str, list[str]]:
        """根据任务描述自动生成小红书标题与话题标签。

        Returns:
            (title, tags) — title 为字符串，tags 为列表（不含 #）
        """
        from openai import OpenAI

        client = OpenAI(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=os.environ.get("ARK_API_KEY"),
        )
        prompt = f"""你是小红书运营专家，帮我根据以下视频任务描述生成发布内容。

任务描述：{task_description}
视频文件名：{result_video or '（未知）'}

请返回 JSON，格式如下（只返回 JSON，不要有多余文字）：
{{
  "title": "吸引人的小红书标题（15-25 字，带表情符号，口语化）",
  "tags": ["话题1", "话题2", "话题3", "话题4", "话题5"]
}}

要求：
- 标题突出视觉亮点或情感共鸣，适合目标受众
- 话题标签贴合内容，3-6 个，不含 # 号
- 语言为中文"""

        try:
            response = client.chat.completions.create(
                model="doubao-1-5-pro-32k-250115",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.7,
            )
            import json as _json
            text = response.choices[0].message.content.strip()
            # 去掉可能的 markdown 代码块
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = _json.loads(text.strip())
            title = data.get("title", "")
            tags = data.get("tags", [])
            print(f"[XHS Meta] 自动生成标题: {title}", flush=True)
            print(f"[XHS Meta] 自动生成标签: {tags}", flush=True)
            return title, tags
        except Exception as e:
            print(f"[XHS Meta] 生成失败: {e}，返回空值", flush=True)
            return "", []

    def _extract_actor_actions(self, actor_output, plan: dict) -> List[dict]:
        """从 Actor 输出中提取执行的动作。"""
        actions = []
        messages = getattr(actor_output, "messages", None) or []

        for m in messages:
            for tc in getattr(m, "tool_calls", None) or []:
                if hasattr(tc, "name"):
                    name = tc.name
                    raw_args = getattr(tc, "arguments", None) or getattr(tc, "args", "")
                elif isinstance(tc, dict):
                    fn = tc.get("function", tc)
                    name = fn.get("name", tc.get("name", "?"))
                    raw_args = fn.get("arguments", tc.get("arguments", ""))
                else:
                    name = str(tc)
                    raw_args = ""

                if isinstance(raw_args, str) and raw_args.strip():
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = raw_args
                else:
                    args = raw_args

                actions.append({"tool": name, "args": args})

        return actions

    def run_single_round(self) -> dict:
        """
        运行单轮 Planner + Actor。
        
        Returns:
            包含 plan, output, elapsed 等信息的字典
        """
        round_start = time.time()
        self.current_round = self.context.start_new_round()

        print(f"\n{'=' * 60}", flush=True)
        print(f"[Round {self.current_round}] 开始第 {self.current_round} 轮迭代", flush=True)
        print(f"{'=' * 60}", flush=True)

        if self.current_round > 1:
            print(f"[Info] 已加载 {self.current_round - 1} 轮历史记录作为短期记忆", flush=True)

        # Step 1: Planner
        self.context.set_current()

        trace_recorder = self._init_trace_recorder()
        trace_recorder.set_episode()
        plan, tools_execute_order = self._run_planner()
        if plan is None:
            self.context.set_error("未能获取 Video Execution Plan")
            return {
                "success": False,
                "error": "未能获取 Video Execution Plan",
                "round": self.current_round,
                "tools_execute_order": tools_execute_order,
            }

        self.current_plan = plan
        self.context.set_round_plan(plan)
        self.context.set_round_planner_tools(tools_execute_order)

        # 立即将「最初的 plan」写入 trace，便于在 Actor 执行前/执行中查看
        timeline = plan.get("timeline", [])
        multi = plan if plan.get("_is_multi_stage") and plan.get("stages") else None
        trace_recorder.log_plan(
            raw_segments=None,
            refined_segments=timeline,
            multi_stage_plan=multi,
        )

        # Planner Only 模式
        if self.planner_only:
            print("\n[Planner Only 模式] 跳过 Actor 执行", flush=True)
            return {
                "success": True,
                "plan": plan,
                "tools_execute_order": tools_execute_order,
                "output": None,
                "round": self.current_round,
                "elapsed_sec": time.time() - round_start,
            }
        print(f"actor初始获得的plan: {plan}", flush=True)

        # Step 2: Actor
        output, actor_tools = self._run_actor(plan)

        self.context.set_round_actions([{"output": output}])
        self.context.set_round_actor_tools(actor_tools)

        # 记录 Trace（第二处）：多阶段时写入「执行完成后」的 plan 状态到 final_multi_stage_plan（与第一处「Planner 刚结束」的 original_multi_stage_plan 区分）
        timeline = plan.get("timeline", [])
        msp_after_actor = None
        if multi:
            from tools.submitPlanTools.sub_implement import get_multi_stage_plan as _get_msp
            msp_after_actor = _get_msp()
            final_plan = msp_after_actor.to_dict() if msp_after_actor else multi
            trace_recorder.log_plan(
                raw_segments=None,
                refined_segments=timeline,
                multi_stage_plan=final_plan,
            )
        else:
            trace_recorder.log_plan(
                raw_segments=None,
                refined_segments=timeline,
                multi_stage_plan=None,
            )

        # 多阶段执行结果 summary：写入 plan.execution_result，便于在 trace 中看到各阶段最终状态
        if multi and msp_after_actor:
            stages_summary = [
                {
                    "stage_id": s.get("stage_id"),
                    "stage_name": s.get("stage_name"),
                    "skill_type": s.get("skill_type"),
                    "status": s.get("status"),
                }
                for s in (msp_after_actor.to_dict().get("stages") or [])
            ]
            trace_recorder.set_plan_result(
                {
                    "status": msp_after_actor.status,
                    "stages": stages_summary,
                }
            )

        trace_recorder.set_final_output(output)
        trace_path = os.path.join(trace_recorder.base_dir, f"{trace_recorder.episode_id}.json")

        round_elapsed = time.time() - round_start
        print(f"\n[Round {self.current_round} 完成]", flush=True)
        print(f"  - 本轮耗时: {round_elapsed:.1f} 秒", flush=True)
        print(f"  - Trace 文件: {trace_path}", flush=True)

        return {
            "success": True,
            "plan": plan,
            "tools_execute_order": tools_execute_order,
            "output": output,
            "round": self.current_round,
            "elapsed_sec": round_elapsed,
            "trace_path": trace_path,
        }

    def run(
        self,
        user_input: Optional[str] = None,
        video_path: str = "",
        image_paths: Optional[List[str]] = None,
        ref_videos: Optional[List[str]] = None,
    ) -> dict:
        """
        运行完整流程（可能多轮迭代）。

        Args:
            user_input: 用户原始输入（剧本/编辑指令）
            video_path: 输入视频路径（可选，用于编辑/续写）
            image_paths: 参考图路径列表（可选）
            ref_videos: 参考视频路径列表（可选），来自热点媒体抓取，供 Planner 理解内容风格

        Returns:
            结果字典，包含 success, output_path, context_id, total_rounds, duration_sec, error
        """
        self.start_time = time.time()

        # 设置运行参数
        if user_input is not None:
            self.user_input = user_input
            self.context.set_user_input(user_input)
        elif self.context.get_user_input():
            self.user_input = self.context.get_user_input()
        else:
            return {
                "success": False,
                "error": "未提供 user_input 且 context 中无历史输入",
                "context_id": self.context.context_id,
            }

        self.video_path = video_path
        self.image_paths = image_paths or []
        self.ref_videos = ref_videos or []

        # 设置全局上下文
        self._set_run_context_globals()

        print("\n" + "=" * 60, flush=True)
        print("开始运行视频生成流程", flush=True)
        print(f"  - 每段时长: {self.time_length} 秒", flush=True)
        if self.total_duration:
            print(f"  - 目标总时长: {self.total_duration} 秒", flush=True)
        print(f"  - 参考图数量: {len(self.image_paths)}", flush=True)
        print(f"  - 输入视频: {self.video_path or '(无)'}", flush=True)
        print(f"  - Planner Only: {self.planner_only}", flush=True)
        print(f"  - Context ID: {self.context.context_id}", flush=True)
        print("=" * 60 + "\n", flush=True)

        final_output = None

        # 多轮迭代循环
        while True:
            # 运行单轮
            round_result = self.run_single_round()

            if not round_result["success"]:
                return {
                    "success": False,
                    "error": round_result.get("error"),
                    "context_id": self.context.context_id,
                    "total_rounds": self.current_round,
                    "duration_sec": time.time() - self.start_time,
                }

            final_output = round_result.get("output")

            # Planner Only 模式，直接结束
            if self.planner_only:
                self.context.set_result("planner_only")
                return {
                    "success": True,
                    "output_path": None,
                    "plan": round_result.get("plan"),
                    "context_id": self.context.context_id,
                    "total_rounds": self.current_round,
                    "duration_sec": time.time() - self.start_time,
                }

            # Step 3: 获取用户反馈
            user_feedback = self._get_user_feedback()

            if user_feedback:
                self.context.set_round_feedback(user_feedback)
                print(f"\n[用户反馈已记录] {user_feedback}", flush=True)
            else:
                self.context.set_round_feedback(None)

            # 判断是否继续下一轮
            if user_feedback is None or user_feedback.strip() == "":
                self.context.set_round_satisfied(True)
                break

            # 使用 VLM 判断
            need_next_round = self._vlm_judge_need_next_round(user_feedback)
            if not need_next_round:
                self.context.set_round_satisfied(True)
                break

            print(f"\n[Info] 根据用户反馈，将进行第 {self.current_round + 1} 轮迭代...", flush=True)

        # 流程结束
        total_elapsed = time.time() - self.start_time
        self.context.set_result(final_output or "")

        print("\n" + "=" * 60, flush=True)
        print("流程结束!", flush=True)
        print(f"  - 总轮次: {self.current_round}", flush=True)
        print(f"  - 总耗时: {total_elapsed:.1f} 秒", flush=True)
        print(f"  - 输出: {final_output}", flush=True)
        print(f"  - Context 文件: {self.context.get_file_path()}", flush=True)
        print("=" * 60, flush=True)

        # 从最后一轮 trace 提取 trace_id 和 result_video
        last_trace_path = round_result.get("trace_path", "")
        last_trace_id = Path(last_trace_path).stem if last_trace_path else ""
        result_video = ""
        if last_trace_path and Path(last_trace_path).exists():
            _trace_data = json.loads(Path(last_trace_path).read_text(encoding="utf-8"))
            _fallback_video = ""  # 来自 video_generate_tool 的候选
            for _item in _trace_data.get("history", []):
                for _call in _item.get("tool_calls", []):
                    if _call.get("status") != "success":
                        continue
                    if _call.get("tool_name") == "merge_video_tool":
                        result_video = _call.get("inputs", {}).get("kwargs", {}).get("save_path", "")
                    elif _call.get("tool_name") == "video_generate_tool" and not _fallback_video:
                        # outputs 是文件名列表，取第一个 .mp4
                        _outs = _call.get("outputs", [])
                        if isinstance(_outs, list):
                            for _f in _outs:
                                if isinstance(_f, str) and _f.endswith(".mp4"):
                                    # 构造相对于项目根的路径
                                    _fallback_video = str(Path(self.output_dir) / _f)
                                    break
            # merge_video_tool 优先，无则用 video_generate_tool 结果
            if not result_video:
                result_video = _fallback_video

        # 生成 XHS 发布元数据（title + tags）
        xhs_title, xhs_tags = self._generate_xhs_meta(self.user_input, result_video)

        self.result = {
            "success": True,
            "output_path": final_output,
            "trace_id": last_trace_id,
            "trace_path": last_trace_path,
            "result_video": result_video,
            "xhs_title": xhs_title,
            "xhs_tags": xhs_tags,
            "context_id": self.context.context_id,
            "total_rounds": self.current_round,
            "duration_sec": total_elapsed,
            "error": None,
        }
        return self.result

    def get_context_id(self) -> str:
        """获取当前 Context ID。"""
        return self.context.context_id

    def get_result(self) -> Optional[dict]:
        """获取运行结果。"""
        return self.result


if __name__ == "__main__":
    # 简单测试
    import argparse
    from skills.skill_loader import ALL_SKILLS, SKILL_DESCRIPTIONS
    from prompts import PLANNER_AGENT_PROMPT

    parser = argparse.ArgumentParser(description="VideoAssistant 测试")
    parser.add_argument("--user-input", type=str, required=False, help="用户输入")
    parser.add_argument("--video-path", type=str, default="", help="输入视频路径")
    parser.add_argument("--images", nargs="+", default=[], help="参考图路径")
    parser.add_argument("--output-dir", type=str, default="workspace/output/dream_story_2", help="输出目录")
    parser.add_argument("--time-length", type=int, default=10, help="每段时长")
    parser.add_argument("--total-duration", type=int, default=None, help="总时长")
    parser.add_argument("--planner-only", action="store_true", help="只运行 Planner")
    parser.add_argument("--context-id", type=str, default=None, help="恢复会话")
    parser.add_argument("--print-planner-prompt", action="store_true", help="启动时打印初始化后的 PLANNER_AGENT_PROMPT（core + meta-skill）")
    parser.add_argument("--print-skills", action="store_true", help="启动时打印 ALL_SKILLS 与 SKILL_DESCRIPTIONS（调试用）")
    args = parser.parse_args()

    assistant = VideoAssistant(
        output_dir=args.output_dir,
        time_length=args.time_length,
        total_duration=args.total_duration,
        planner_only=args.planner_only,
        context_id=args.context_id,
    )
    # SCRIPT_PATH = "/root/work/temp/cp5_storyboards.json"
    # def load_script(script_path: str) -> str:
    #     return json.load(open(script_path, "r", encoding="utf-8"))
    # script = str(load_script(SCRIPT_PATH))
#     script = """
#     曾经在大陆上生活着一群没有影子的人。

# 他们过着素朴的生活，对栖居地以外的世界一无所知。

# 直到某一天，迷途的冒险家发现了他们。无影人惊奇地发现这名冒险家有一个亦步亦趋的追随者，寡言且忠实。冒险家同样感到惊奇，大陆的一隅竟有这样确实存在但又不因日光而留下投影的族群。

# 「我做梦也没有想到会有这样的发现。」冒险家说。

# 「梦？我们的人已经很久不会做梦。」无影人中的一人说，「老人说过，所有的梦已经被梦过了。」

# 「影子里藏着灵魂的秘密。你没有影子，所以也没有梦。」冒险家说，「也许你们曾经有影子，就像你们曾经做梦。」

# 「既然如此，我该去哪里寻找我所失去的东西？」

# 「到密林里去吧，那里有很多梦，捕梦者或许有多余的梦分给你。」

# 年轻的无影人将故土抛在身后，长途跋涉来到了冒险家所说的密林。密林深处有着层层叠叠的影子。云的影子，树冠的影子，甚至不足道的飞鸟也能在松软的土地上留下一大片投影。

# 日复一日，他在层层叠叠的影子之间穿梭。影子里藏着灵魂的秘密，他想，在这许许多多的秘密之中，唯有他是没有秘密的人。于是某一天他发现，所有的梦境都向他敞开，他没有自己的梦，却因此得以进入他者的梦。

# 在他经历的许多梦境中，鸟的梦色彩斑斓，虎的梦气息芬芳，但他并没有见到捕梦者，也没有找到所谓多余的梦。梦与影子与此在的实存一一对应，他想，或许冒险家欺骗了他，或许根本没有无主的梦，就像不会有无主的影子。

# 在他几乎要承认自己的失败时，捕梦者找到了他。邂逅发生在海螺的梦中。他闯入了尾声的时刻，试图在其中寻找白浪与盐风，但在略显伤感的余韵中，他一无所获。

# 「你同这枚海螺一样，不属于这片密林。」

# 说话的是一个女人。他很快意识到，她就是冒险家所说的捕梦者，因为女人的影子像缀满宝石的帷幔，有着奇异的斑驳质感。

# 「我一直在找你。」他说，「或许你有多余的梦……」

# 「那是如朝露般易逝的……」捕梦者的话语中并没有悲哀，「无主的梦无法长久保存。我尝试过很多方法，它们最终都消散了。」

# 「……你瞧，就像这枚海螺……我们该离开了。」捕梦者拉起他的手，带他离开了这个已经没有白浪与盐风的将逝的梦。

# 在潺潺的溪流边，女人给他讲了许多故事，并传授他入梦的诀窍。之后，女人又再三警告他，关于捕梦者的禁忌，诸如不可回看他者的梦，因为他者的隐秘就像无底的深井。

# 「梦魇比你所想象的更狡猾。当它们发现你的所为，就会蜂拥而起，将你拖入无光之境。在那里没有影子的边界，你无法离开。如果待得够久，你将能够从它们的窸窣声中分辨出有意义的语词，那是已不存在于任何一处，只在渐淡的回忆中萦留的旧名。你知道，不可提起死者的名讳，否则他们会找上你……」

# 「我曾以为你们都没有影子。」他诚实地发问，「我曾以为捕梦者也没有自己的梦，所以才要去收集他者的梦。」

# 女人没有回答，她斑驳的影子如草叶般随晚风摇曳。

# 可是年轻的无影人太想知道答案，尽管捕梦者将影子保护得很好，他还是找到了机会。不像在密林中漫游的生灵，其梦境之门大开，通向捕梦者的梦境的是一条崎岖的小路。

# 显然，她将自己的秘密藏在他者的梦中，他想，可她的秘密是什么？这又是何人的梦？

# 捕梦者的梦也如密林一般层层叠叠，他很快迷失了方向，不知不觉间，梦魇已经要缠上他。

# 「我触犯了捕梦者的禁忌，但即便凝视无底的深井，也没能找到答案。」他想，「她说过，如果待得够久，就能从它们的声响中分辨出名字，只要这样，或许至少能知道这是谁的梦。」

# 于是他放任梦魇将他带入至深处，那里一如女人所告诫的，是没有边界的无光之境。他谛听一切细微声响，期望从中寻出代表名称的语词。

# 不知道过去了多久，他终于从零碎的音节中拼凑出一个名字。这个名字似乎具有某种特别的引力，让他不由诵念出来。

# 然后他睁开了双眼。

# 「我看到了奇怪的景象。」他说，「我看到一个女人进到了我的梦里，她偷走了我的梦，偷走了我不曾知晓的灵魂的秘密，从此我便没有了影子。我听到了她这样称呼我，她说……」

# 「你知道，」女人打断了他，「不可提起死者的名讳，否则他们会找上你……」

# 捕梦者坐在潺潺的溪流边，斑驳的影子如草叶般随晚风摇曳。

# 「那只是一个关于死者的故事。这样的故事我为你讲述了许多，但仍有更多未被讲述的。」

# 于是捕梦者继续为年轻的无影人讲述未曾被人听过的故事…………
#     """
    # 可选：打印技能索引与 Planner prompt，便于验证初始化是否成功
    if args.print_skills:
        print("ALL_SKILLS:")
        for k, v in ALL_SKILLS.items():
            print(f"  {k} -> {v}")
        print("\nSKILL_DESCRIPTIONS:")
        for k, v in SKILL_DESCRIPTIONS.items():
            short = v if len(v) <= 120 else v[:117] + "..."
            print(f"  {k}: {short}")
        print("\n" + "=" * 60 + "\n", flush=True)

    if args.print_planner_prompt:
        print("=== PLANNER_AGENT_PROMPT (initialized) ===\n")
        print(PLANNER_AGENT_PROMPT)
        print("\n" + "=" * 60 + "\n", flush=True)
    result = assistant.run(
        # user_input="创作一个二次元动画微电影，细节逼真，人物保持高度一致，视频语音使用中文。小说："+script,
        user_input=args.user_input,
        video_path=args.video_path,
        image_paths=args.images,
    )

    print("\n最终结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))