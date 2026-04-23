import re
import json
from typing import List, Optional, Dict, Any
from pathlib import Path

from actor import actor_agent
import utils.tools_implement as tools_implement
from utils.trace_recorder import TraceRecorder

# 纯生成单段时长上限，与 Runtime time_length 一致，超过则自动拆成多段
DEFAULT_TIME_LENGTH = 15


def _time_str_to_seconds(s: str) -> float:
    """将 time_range 的 "mm:ss" 或 "m:ss" 转为秒数。"""
    if not s or not isinstance(s, str):
        return 0.0
    s = s.strip()
    parts = s.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 1:
        try:
            return float(parts[0])
        except ValueError:
            return 0.0
    return 0.0


def _parse_duration_from_item(item: Dict[str, Any]) -> Optional[float]:
    """从 timeline 项的 technical_params.duration（如 "20s"）或 time_range 解析出时长（秒）。"""
    tp = item.get("technical_params") or {}
    dur = tp.get("duration")
    if dur is not None:
        if isinstance(dur, (int, float)):
            return float(dur)
        if isinstance(dur, str):
            dur = dur.strip().rstrip("sS").strip()
            try:
                return float(dur)
            except ValueError:
                pass
    tr = item.get("time_range") or {}
    start_s = _time_str_to_seconds(tr.get("start") or "00:00")
    end_s = _time_str_to_seconds(tr.get("end") or "00:00")
    if end_s > start_s:
        return end_s - start_s
    return None


def _seconds_to_mmss(sec: float) -> str:
    """将秒数转为 "mm:ss" 或 "m:ss"。"""
    m = int(sec) // 60
    s = int(sec) % 60
    return f"{m:02d}:{s:02d}"


def _split_duration(total_s: float, max_seg: int) -> List[int]:
    """将总时长按 max_seg 为一段上限拆成多段（秒数列表）。"""
    if total_s <= 0 or max_seg <= 0:
        return [int(total_s)] if total_s > 0 else []
    out: List[int] = []
    t = total_s
    while t > 0:
        out.append(min(max_seg, int(t)))
        t -= out[-1]
    return out


def normalize_plan_pure_generation_split(plan: Dict[str, Any], time_length: int = DEFAULT_TIME_LENGTH) -> Dict[str, Any]:
    """
    纯生成任务时，若某条 GENERATE 的时长 > time_length，则自动拆成多段，每段 ≤ time_length。
    避免 Planner 未分段导致 API 报 InvalidParameter(duration)。
    """
    if not plan or plan.get("task_metadata", {}).get("task_type") != "PLOT_EXTENSION":
        return plan
    timeline = plan.get("timeline")
    if not timeline or not isinstance(timeline, list):
        return plan

    new_timeline: List[Dict[str, Any]] = []
    for item in timeline:
        if item.get("action_trigger") != "GENERATE":
            new_timeline.append(item)
            continue
        sp = item.get("segment_path")
        if sp is not None and str(sp).strip():
            # 有参考视频/路径，不自动拆
            new_timeline.append(item)
            continue
        duration = _parse_duration_from_item(item)
        if duration is None or duration <= time_length:
            new_timeline.append(item)
            continue
        # 纯生成且单段时长 > time_length：拆成多段，并为每段加上接续说明以减轻段间不连贯
        segs = _split_duration(duration, time_length)
        base_prompt = (item.get("execution_prompt") or "").strip()
        refs = item.get("reference_resources") or []
        start_sec = 0.0
        for i, seg_len in enumerate(segs):
            end_sec = start_sec + seg_len
            if i == 0:
                continuity = f" 本段为第 {i+1}/{len(segs)} 段，时长 {seg_len} 秒。结尾画面与状态需便于下一段自然接续（风格、光线、构图一致）。"
            else:
                continuity = f" 本段为第 {i+1}/{len(segs)} 段，时长 {seg_len} 秒。需从上一段结尾的画面与状态自然接续，保持风格、光线、构图与动作连贯，避免跳变。"
            new_item = {
                "segment_id": 0,  # 下面统一重排
                "time_range": {"start": _seconds_to_mmss(start_sec), "end": _seconds_to_mmss(end_sec)},
                "segment_path": "",
                "action_trigger": "GENERATE",
                "execution_prompt": base_prompt + continuity,
                "reference_resources": list(refs),
                "technical_params": {"duration": f"{seg_len}s"},
            }
            new_timeline.append(new_item)
            start_sec = end_sec
    # 重排 segment_id
    for idx, item in enumerate(new_timeline):
        item = dict(item)
        item["segment_id"] = idx + 1
        new_timeline[idx] = item
    plan = dict(plan)
    plan["timeline"] = new_timeline
    return plan


def extract_plan_json(content: str) -> str:
    """从 Planner 输出中提取 Plan JSON（支持新格式 Video Execution Plan 对象和旧格式 segment 数组，支持被 ```json ... ``` 包裹）。"""
    if not (content or "").strip():
        return "[]"
    text = content.strip()
    
    # 先尝试提取代码块中的 JSON
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_block:
        extracted = code_block.group(1).strip()
        # 检查是新格式（对象）还是旧格式（数组）
        if extracted.startswith("{") and ("task_metadata" in extracted or "timeline" in extracted):
            return extracted
        elif extracted.startswith("["):
            return extracted
        return extracted
    
    # 尝试匹配新格式：Video Execution Plan 对象
    object_match = re.search(r"\{[\s\S]*\"(?:task_metadata|timeline)\"[\s\S]*\}", text)
    if object_match:
        return object_match.group(0)
    
    # 尝试匹配旧格式：segment 数组
    array_match = re.search(r"\[[\s\S]*\]", text)
    if array_match:
        return array_match.group(0)
    
    return text


def get_plan_for_actor(
    plan: Optional[Dict[str, Any]] = None,
    plan_content: Optional[str] = None,
    time_length: int = DEFAULT_TIME_LENGTH,
) -> str:
    """
    获取 Plan 并转换为 JSON 字符串，供 Actor 执行（仅支持新格式 Video Execution Plan）。
    若为纯生成且单段时长 > time_length，会自动拆成多段。

    优先级：
    1. 如果提供了 plan (dict)，直接使用
    2. 如果提供了 plan_content (str)，直接使用
    3. 尝试从 execution_plan_store.get_plan() 读取
    4. 若仍为空，抛出异常

    Args:
        plan: Plan 字典对象（新格式 Video Execution Plan）
        plan_content: Plan 的 JSON 字符串
        time_length: 单段最大时长（秒），用于纯生成自动分段，默认 15

    Returns:
        Plan 的 JSON 字符串，可直接传给 actor_agent.run()

    Raises:
        RuntimeError: 当所有来源都没有可用的 Plan 时
    """
    def _to_plan_obj(p: Dict[str, Any]) -> Dict[str, Any]:
        return normalize_plan_pure_generation_split(p, time_length)

    # 优先级1: 直接传入的 plan 字典
    if plan is not None:
        return json.dumps(_to_plan_obj(plan), ensure_ascii=False)

    # 优先级2: 直接传入的 plan_content 字符串
    if plan_content:
        if plan_content.strip().startswith("{"):
            try:
                parsed = json.loads(plan_content)
                return json.dumps(_to_plan_obj(parsed), ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        return plan_content

    # 优先级3: 从 execution_plan_store 读取
    try:
        from utils.execution_plan_store import get_plan
        stored_plan = get_plan()
        if stored_plan:
            return json.dumps(_to_plan_obj(stored_plan), ensure_ascii=False)
    except ImportError:
        pass
    except Exception:
        pass

    # 没有找到可用的 Plan，抛出异常
    raise RuntimeError(
        "No Video Execution Plan found for actor. "
        "Please ensure plan/plan_content is provided or planner has registered plan via submit_video_execution_plan."
    )


def execute_plan_with_actor(plan: Optional[Dict[str, Any]] = None, plan_content: Optional[str] = None, stream: bool = False) -> str:
    """
    直接将 Video Execution Plan 传给 Actor 执行。
    
    这是最简单的方式：从 planner_copy 生成 Plan 后，直接调用此函数执行。
    Actor 仅支持新格式 Video Execution Plan（包含 task_metadata 和 timeline）。
    
    Args:
        plan: Plan 字典对象（新格式 Video Execution Plan），优先级最高
        plan_content: Plan 的 JSON 字符串，优先级次之
        stream: 是否流式输出
    
    Returns:
        Actor 执行后的输出内容
    
    Raises:
        RuntimeError: 当没有可用的 Plan 时
    """
    plan_json = get_plan_for_actor(plan=plan, plan_content=plan_content)
    actor_output = actor_agent.run(plan_json, stream=stream)
    return actor_output.content if actor_output.content else ""


def run_long_video_edit_workflow(
    video_path: str,
    InitUserMessage: str,
    image_paths: List[str],
    time_length: int = 15,
    use_auto_split_pipeline: bool = True,
    allow_interactive: bool = True,
) -> str:
    """
    运行长视频编辑工作流：先由 planner_copy 生成新格式 Video Execution Plan，再由 actor 执行编辑并合并。

    Args:
        video_path: 输入视频的本地路径（空字符串表示无视频）。
        InitUserMessage: 编辑指令描述。
        image_paths: 参考图片路径列表（对应 参考图1、参考图2...）。
        time_length: 单片段最大时长（秒），超长片段会被拆分，默认 15。
        use_auto_split_pipeline: 是否使用 auto_video_splite 分镜管道，默认 True。
        allow_interactive: 是否允许交互式用户输入（默认 True，允许交互；设置为 False 时，遇到需要用户输入会抛出异常）。

    Returns:
        合并后的最终视频保存路径。
    
    Raises:
        ValueError: 当 video_path 无效（存在但为目录，或不存在）时。
        RuntimeError: 当 allow_interactive=False 且 Planner 需要用户输入时。
    """
    # 清理和验证 video_path
    video_path = video_path.strip() if video_path else ""
    
    # 如果 video_path 非空，验证是否为有效文件
    if video_path:
        video_file = Path(video_path)
        if not video_file.exists():
            raise ValueError(f"Video file does not exist: {video_path}")
        if video_file.is_dir():
            raise ValueError(f"video_path points to a directory, not a file: {video_path}")
    
    # 如果 video_path 为空，让 Planner 来处理（通过 getUserMessageTool 询问用户，或根据 task skill 判断是否为纯生成任务）
    
    recorder = TraceRecorder(video_path=video_path)
    recorder.set_episode()
    recorder.set_meta_info(
        video_path=video_path,
        instruction=InitUserMessage,
        image_paths=image_paths,
        time_length=time_length,
    )

    # 设置运行上下文，供 planner_copy 和 tools_implement_copy 使用
    import planner as _pc
    _pc.RUN_CONTEXT_IMAGE_PATHS = image_paths
    _pc.RUN_CONTEXT_VIDEO_PATH = video_path
    tools_implement.RUN_CONTEXT_IMAGE_PATHS = image_paths
    tools_implement.RUN_CONTEXT_VIDEO_PATH = video_path

    # Step 1: 使用 planner_copy 生成新格式 Video Execution Plan
    from planner import run_planner_with_user_input

    user_message = (
        "### Runtime Inputs\n"
        + f"- InitUserMessage: {InitUserMessage}\n"
        + f"- time_length: {time_length}\n"
        + f"- use_auto_split_pipeline: {use_auto_split_pipeline}\n"
        + (f"- video_path: {video_path}\n" if video_path else "- video_path: (无)\n")
        + f"- image_paths: {image_paths}\n"
    )
    run_response = run_planner_with_user_input(
        user_message,
        stream=False,
        use_print_response=False,
        use_task_specific_prompt=True,
        allow_interactive=allow_interactive,
        video_path=video_path,
        image_paths=image_paths,
        time_length=time_length,
        InitUserMessage=InitUserMessage,
        use_auto_split_pipeline=use_auto_split_pipeline,
    )

    # Step 2: 获取 Plan（优先从 execution_plan_store，否则从 planner 回复解析）
    plan = None
    try:
        from utils.execution_plan_store import get_plan
        plan = get_plan()
    except Exception:
        pass
    if plan is None and run_response and getattr(run_response, "content", None):
        raw = run_response.content.strip()
        extracted = extract_plan_json(raw)
        if extracted.startswith("{"):
            try:
                plan = json.loads(extracted)
            except json.JSONDecodeError:
                pass
    if plan is None:
        raise RuntimeError(
            "No Video Execution Plan obtained. "
            "Planner should call submit_video_execution_plan or output valid plan JSON."
        )

    # 纯生成且单段时长 > time_length 时自动拆成多段，避免 API duration 报错
    plan = normalize_plan_pure_generation_split(plan, time_length)

    plan_json = json.dumps(plan, ensure_ascii=False)
    print(plan_json[:500] + "..." if len(plan_json) > 500 else plan_json)

    # Step 3: Actor 根据新 Plan 执行编辑并合并
    actor_output = actor_agent.run(plan_json, stream=False)
    result = actor_output.content if actor_output.content else ""

    recorder.set_final_output(result)
    return result


if __name__ == "__main__":
    video_path = "rawdata/100/1月12日(6)-28.mp4"
    image_paths = [
        "rawdata/100/f581363980a547fe0514faeefc8c1a6f.jpg"
        # "rawdata/90/参考图2.png",
    ]
    InitUserMessage = "将视频里的男生修改成参考图片中的女性，背景等其他内容保持不变"
    time_length = 15

    import time

    start_time = time.time()
    result = run_long_video_edit_workflow(
        video_path=video_path,
        InitUserMessage=InitUserMessage,
        image_paths=image_paths,
        time_length=time_length,
    )
    print(f"Workflow 完成，输出: {result}")
    print(f"Workflow 耗时: {time.time() - start_time:.1f} 秒")
