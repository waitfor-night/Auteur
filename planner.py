from typing import List
from pathlib import Path
from prompts import PLANNER_AGENT_PROMPT, get_planner_agent_prompt
from agno.agent import Agent
from agno.tools import tool
from agno.tools.function import UserInputField
from agno.models.openai import OpenAIResponses
from agno.db.sqlite import SqliteDb
from openai import OpenAI, PermissionDeniedError
from volcenginesdkarkruntime import Ark
import os

# 直接使用 tools/plannerTools（根目录勿保留 tools.py 以免冲突）
from tools.plannerTools import PLANNER_TOOLS

# SEGMENT ID NAME MAP
SEGMENT_ID_NAME_MAP = []

# Run context: set before agent.run/print_response so tools use user-provided paths (model may invent wrong paths).
RUN_CONTEXT_IMAGE_PATHS = None
RUN_CONTEXT_VIDEO_PATH = None

# 确保数据库目录存在，continue_run 需要 db
Path("tmp").mkdir(exist_ok=True)

planner_agent = Agent(
    name="multi-shot-multi-object-long-video-edit-planner",
    description=PLANNER_AGENT_PROMPT,
    tools=PLANNER_TOOLS,
    model=OpenAIResponses(
        id=os.environ.get("PLANNER_MODEL", "doubao-seed-2-0-pro-260215"),
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.environ.get("ARK_API_KEY"),
    ),
    db=SqliteDb(db_file="tmp/planner.db"),
)


def build_planner_agent(
    user_memory: str | None = None,
    content_strategy: str | None = None,
) -> Agent:
    """构造注入了可选 memory 和/或 content_strategy 的 Planner Agent。"""
    prompt = PLANNER_AGENT_PROMPT
    if user_memory:
        prompt += (
            "\n\n---\n\n"
            "## User Memory\n"
            "以下是该用户的历史偏好与风格记忆，请在制定计划时优先参考：\n\n"
            + user_memory
        )
    if content_strategy:
        prompt += (
            "\n\n---\n\n"
            "## Content Strategy\n"
            "以下是账号内容策略，制定计划时请遵循其中的内容方向与制作要求：\n\n"
            + content_strategy
        )
    return Agent(
        name=planner_agent.name,
        description=prompt,
        tools=planner_agent.tools,
        model=planner_agent.model,
        db=planner_agent.db,
    )


def build_planner_with_memory(user_memory: str) -> Agent:
    """向后兼容保留，内部委托给 build_planner_agent。"""
    return build_planner_agent(user_memory=user_memory)


def _get_message_from_run_response(run_response) -> str:
    """Extract message from the pending getUserMessageTool call in run_response, if any."""
    import json
    messages = getattr(run_response, "messages", None) or []
    for m in reversed(messages):
        for tc in getattr(m, "tool_calls", None) or []:
            name = None
            raw_args = None
            if hasattr(tc, "name"):
                name = tc.name
                raw_args = getattr(tc, "arguments", None) or getattr(tc, "args", "")
            elif isinstance(tc, dict):
                fn = tc.get("function", tc)
                name = fn.get("name", tc.get("name", ""))
                raw_args = fn.get("arguments", tc.get("arguments", ""))
            if name == "getUserMessageTool" and raw_args:
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    if isinstance(args, dict) and args.get("message"):
                        return str(args["message"]).strip()
                except Exception:
                    pass
    return ""


def _print_tool_calls_with_args(run_response):
    """按执行顺序完整打印工具调用及全部输入参数。"""
    import json
    calls = []
    messages = getattr(run_response, "messages", None) or []
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
            calls.append((name, args))
    if not calls:
        for t in getattr(run_response, "tools", None) or []:
            name = getattr(t, "name", getattr(t, "tool_name", None))
            args = getattr(t, "args", getattr(t, "arguments", None)) or {}
            if name:
                calls.append((name, args if isinstance(args, dict) else {}))
    if not calls:
        return
    sep = "─" * 72
    print("\n" + "┌" + sep + "┐")
    print("│ 工具调用顺序及输入参数（完整）" + " " * 38 + "│")
    print("├" + sep + "┤")
    for i, (name, args) in enumerate(calls, 1):
        print("│")
        print("│  【%d】 %s" % (i, name))
        print("│  " + "─" * 68)
        if args:
            if isinstance(args, dict):
                for k, v in args.items():
                    v_str = json.dumps(v, ensure_ascii=False, indent=2) if isinstance(v, (dict, list)) else str(v)
                    lines = v_str.split("\n")
                    print("│    %s: %s" % (k, lines[0]))
                    for line in lines[1:]:
                        print("│       " + line)
            else:
                for line in str(args).split("\n"):
                    print("│    " + line)
        else:
            print("│    (无参数)")
        print("│")
    print("└" + sep + "┘\n")


def _print_planner_run(run_response, title: str = "Planner 执行"):
    """先打印工具调用顺序及输入参数，再使用 agno 自带的带边框输出。"""
    from agno.utils import pprint as agno_pprint

    print(f"\n【{title}】\n")
    _print_tool_calls_with_args(run_response)
    agno_pprint.pprint_run_response(run_response)


def run_planner_with_user_input(
    user_message: str,
    stream: bool = False,
    use_print_response: bool = False,
    use_task_specific_prompt: bool = False,
    allow_interactive: bool = True,
    **kwargs,
):
    """
    运行 planner。

    - use_print_response=False（默认）：使用 run() + continue_run()，当 Agent 需要用户输入
      （如 getUserMessageTool 的 clarified_edit_prompt）时会暂停并提示输入，输入后继续执行；
      打印使用 pprint_run_response。
    - use_print_response=True：直接使用 agno 自带的 print_response()，带边框、工具调用等
      完整打印；但不支持“暂停等待用户输入”，若本次会触发 getUserMessageTool 请用 False。
    - use_task_specific_prompt=True（默认）：根据 kwargs 中的 InitUserMessage 做任务判断，只从 skills/ 导入
      该任务对应的 skill 组装 prompt，用该 prompt 创建临时 agent 执行本次 run（更精简）。
      如果 InitUserMessage 不可用，则使用默认的 PLANNER_AGENT_PROMPT（包含所有 skill）。
    - use_task_specific_prompt=False：使用默认的 PLANNER_AGENT_PROMPT（包含所有 task skill）。
    - allow_interactive: 是否允许交互（默认 True）。如果 False 且需要用户输入，会抛出 RuntimeError。
    """
    agent = planner_agent
    # 默认根据 InitUserMessage 动态注入对应的 task skill（运行时注入）
    if use_task_specific_prompt and kwargs.get("InitUserMessage"):
        from agno.agent import Agent
        prompt = get_planner_agent_prompt(kwargs["InitUserMessage"])
        agent = Agent(
            name=planner_agent.name,
            description=prompt,
            tools=planner_agent.tools,
            model=planner_agent.model,
            db=planner_agent.db,
        )
    if use_print_response:
        agent.print_response(user_message, stream=stream, **kwargs)
        return None

    run_response = agent.run(user_message, stream=stream, **kwargs)
    _print_planner_run(run_response, "Planner 首轮执行")

    while run_response.active_requirements:
        has_user_input = False
        # Get pending tool call args so we can show message for getUserMessageTool
        message_text = _get_message_from_run_response(run_response)
        for requirement in run_response.active_requirements:
            if requirement.needs_user_input:
                has_user_input = True
                
                # 非交互模式下，如果遇到需要用户输入的情况，直接抛出异常
                if not allow_interactive:
                    raise RuntimeError(
                        f"Planner requires user input (getUserMessageTool) but allow_interactive=False. "
                        f"Agent's prompt: {message_text or 'N/A'}. "
                        "This usually happens when video_path is missing or InitUserMessage needs clarification. "
                        "In non-interactive mode (e.g., benchmark), please ensure all required inputs are provided."
                    )
                
                input_schema: List[UserInputField] = requirement.user_input_schema  # type: ignore
                for field in input_schema:
                    ## 打印用户输入的提示
                    print(f"\n{'='*60}")
                    print(f"【需要用户输入】 {field.name}")
                    # For clarified_user_info, show agent's message if available
                    if field.name == "clarified_user_info" and message_text:
                        print(f"  说明 (Agent): {message_text}")
                    elif getattr(field, "description", None):
                        print(f"  说明: {field.description}")
                    print(f"  类型: {field.field_type}")
                    print(f"{'='*60}")
                    
                    if field.value is None:
                        user_value = input(f"  请输入 {field.name}: ").strip()
                    else:
                        user_value = field.value
                    field.value = user_value

        if not has_user_input:
            break
        run_response = agent.continue_run(
            run_response=run_response,
            stream=stream,
        )
        _print_planner_run(run_response, "Planner 继续执行（用户输入后）")

    return run_response

if __name__ == "__main__":
    import argparse

    # ---------- 可直接在下方修改默认值，不传命令行参数时使用 ----------
    # 默认视频和图片路径为空，因为很多任务（如 LONG_VIDEO_CREATE）不需要预设的参考资源
    # DEFAULT_VIDEO_PATH = None
    DEFAULT_IMAGE_PATHS = ["rawdata/90/参考图1.png","rawdata/90/参考图2.png"]
    # 如需测试特定场景，可取消注释以下内容：
    DEFAULT_VIDEO_PATH = "rawdata/90/8-1.mp4"
    # DEFAULT_IMAGE_PATHS = ["rawdata/01/1/参考图1.png", "rawdata/01/1/参考图2.jpg"]
    DEFAULT_INIT_USER_MESSAGE = "将参考视频里的男人换成参考图1中的女人（整个形象，包括衣着），将参考视频里的女人换成参考图2中的男人（整个形象，包括衣着），视频中的其他部分保持不变。"  # 必须通过命令行或文件提供
    DEFAULT_TIME_LENGTH = 15
    DEFAULT_AUTO_MERGE = True
    # -----------------------------------------------------------------

    def _find_video_and_images(base):
        base = Path(base)
        if not base.exists():
            return None, [], "目录不存在: " + str(base)
        videos = list(base.glob("*.mp4")) + list(base.glob("*.MP4")) + list(base.glob("*.mov"))
        images = list(base.glob("*.jpg")) + list(base.glob("*.jpeg")) + list(base.glob("*.png")) + list(base.glob("*.JPG")) + list(base.glob("*.PNG"))
        video_path = str(videos[0]) if videos else None
        image_paths = [str(p) for p in sorted(images)]
        return video_path, image_paths, None

    def _parse_image_paths(s):
        if not s or not s.strip():
            return []
        return [x.strip() for x in str(s).replace(";", ",").split(",") if x.strip()]

    parser = argparse.ArgumentParser(
        description="通用 Planner。仅 InitUserMessage 为必填（可用 --init_user_message、--init_user_message_file 或 --input_dir 下参考方式.txt），其余均为可选。"
    )
    parser.add_argument("--input_dir", type=str, default=None, help="可选。从该目录自动查找视频、参考图、参考方式.txt")
    parser.add_argument("--video_path", type=str, default=None, help="可选。直接传入视频路径，不传表示纯生成/无输入视频")
    parser.add_argument("--init_user_message", type=str, default=None, help="必填。编辑/生成提示（或通过 --init_user_message_file / --input_dir 下参考方式.txt 提供）")
    parser.add_argument("--init_user_message_file", type=str, default=None, help="可选。从文件读取编辑提示（与 --init_user_message 二选一）")
    parser.add_argument("--image_paths", type=str, default=None, help="可选。参考图路径，逗号分隔，如 img1.jpg,img2.png")
    parser.add_argument("--time_length", type=int, default=15, help="每段视频的最大时长（秒），默认15秒")
    parser.add_argument("--total_duration", type=int, default=None, help="可选。视频总时长（秒），用于 LONG_VIDEO_CREATE 任务。设置后会根据场景数量平均分配时长")
    parser.add_argument("--use_auto_split_pipeline", type=lambda x: x.lower() in ("1", "true", "yes"), default=True)
    parser.add_argument("--task_specific", action="store_true", default=False, help="按 InitUserMessage 推断任务类型只加载对应 skill（默认开启，运行时注入 skill）")
    parser.add_argument("--no_task_specific", action="store_false", dest="task_specific", help="禁用任务特定 skill，使用所有 skill")
    parser.add_argument("--use_print_response", action="store_true")
    args = parser.parse_args()

    video_path = args.video_path
    image_paths = args.image_paths
    init_user_message = args.init_user_message

    if args.input_dir:
        v, imgs, err = _find_video_and_images(args.input_dir)
        if err:
            print(err)
            raise SystemExit(1)
        if video_path is None:
            video_path = v
        if image_paths is None:
            image_paths = imgs
        else:
            image_paths = _parse_image_paths(image_paths) or imgs
        if init_user_message is None and args.init_user_message_file is None:
            ref_txt = Path(args.input_dir) / "参考方式.txt"
            if ref_txt.exists():
                init_user_message = ref_txt.read_text(encoding="utf-8").strip()
    else:
        image_paths = _parse_image_paths(image_paths) if image_paths else []

    if args.init_user_message_file:
        init_user_message = Path(args.init_user_message_file).read_text(encoding="utf-8").strip()

    # 未使用 --input_dir 时：未通过命令行提供的项用文件内默认值（上方 DEFAULT_*）
    if not args.input_dir:
        if video_path is None:
            video_path = DEFAULT_VIDEO_PATH
        if not image_paths:
            image_paths = list(DEFAULT_IMAGE_PATHS) if DEFAULT_IMAGE_PATHS else []
        if not init_user_message:
            init_user_message = DEFAULT_INIT_USER_MESSAGE
    
    # time_length 和 use_auto_split_pipeline：优先使用命令行参数，否则使用默认值
    time_length = args.time_length if args.time_length else DEFAULT_TIME_LENGTH
    use_auto_split_pipeline = args.use_auto_split_pipeline

    if not init_user_message:
        print("错误：未提供 InitUserMessage（--init_user_message、--init_user_message_file、--input_dir 下参考方式.txt，或在文件内设置 DEFAULT_INIT_USER_MESSAGE）")
        raise SystemExit(1)

    image_paths = image_paths or []

    # So tools use these paths instead of whatever the model passes.
    RUN_CONTEXT_IMAGE_PATHS = image_paths
    RUN_CONTEXT_VIDEO_PATH = video_path

    total_duration = args.total_duration
    
    user_message = (
        "### Runtime Inputs\n"
        + f"- InitUserMessage: {init_user_message}\n"
        + f"- time_length: {time_length}\n"
        + (f"- total_duration: {total_duration}\n" if total_duration else "- total_duration: (无，使用默认每段 time_length 秒)\n")
        + f"- use_auto_split_pipeline: {use_auto_split_pipeline}\n"
        + (f"- video_path: {video_path}\n" if video_path else "- video_path: (无)\n")
        + f"- image_paths: {image_paths}\n"
    )

    run_planner_with_user_input(
        user_message,
        stream=False,
        use_print_response=args.use_print_response,
        use_task_specific_prompt=args.task_specific,
        video_path=video_path,
        image_paths=image_paths,
        time_length=time_length,
        total_duration=total_duration,
        InitUserMessage=init_user_message,
        use_auto_split_pipeline=use_auto_split_pipeline,
    )

    # 与 run_planner_test_参考生成1.py 一致：打印最终登记的计划
    plan = None
    try:
        from tools.submitPlanTools import get_plan
        plan = get_plan()
    except Exception:
        pass
    if plan:
        import json
        print("\n【已登记的 Video Execution Plan】")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        timeline = plan.get("timeline") or []
        if len(timeline) == 1 and (timeline[0].get("action_trigger") == "GENERATE"):
            print("\n✓ 符合纯生成预期：1 条 GENERATE。")
        elif len(timeline) > 1:
            print(f"\n  timeline 条数: {len(timeline)}")
    else:
        print("\n未通过 submit_video_execution_plan 登记；请查看上方 Planner 最后一条回复中是否包含完整 JSON 计划。")
