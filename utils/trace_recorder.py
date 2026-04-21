import datetime
import functools
import json
import os
import re
import time
import uuid
from contextvars import ContextVar
from typing import Any, Callable

# 过滤键名：包含这些的 kwargs 值用占位符替代
_FILTER_KEYS = frozenset(
    {"base64", "video_data", "image_data", "ref_video", "ref_images", "data"}
)
_MAX_STR_LEN = 10000
_MAX_LIST_LEN = 500

# segment_id 提取：支持两种命名
# - rawdata/3/3gf-1_segment_1.mp4 -> 1
# - rawdata/82/13-13-Scene-001.mp4 -> 1（scenedetect 默认）
_SEGMENT_ID_PATTERN_LEGACY = re.compile(r"_segment_(\d+)\.mp4", re.IGNORECASE)
_SEGMENT_ID_PATTERN_SCENE = re.compile(r"-Scene-(\d+)\.mp4", re.IGNORECASE)

# 当前 TraceRecorder 实例，由 workflow 在入口设置
_recorder_var: ContextVar["TraceRecorder | None"] = ContextVar(
    "trace_recorder_instance", default=None
)


def _sanitize_value(v: Any) -> Any:
    """将值转为可 JSON 序列化的结构，过滤超大或敏感数据。"""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, bytes):
        return "<filtered: bytes>"
    if isinstance(v, str):
        if len(v) > _MAX_STR_LEN:
            return v[:_MAX_STR_LEN] + f"... [truncated, total {len(v)} chars]"
        return v
    if isinstance(v, (list, tuple)):
        if len(v) > _MAX_LIST_LEN:
            head = _sanitize_value(v[:_MAX_LIST_LEN])
            return head + [f"... [truncated, total {len(v)} items]"]
        return [_sanitize_value(x) for x in v]
    if isinstance(v, dict):
        out = {}
        for k, kval in v.items():
            k_lower = str(k).lower()
            if any(fk in k_lower for fk in _FILTER_KEYS):
                out[k] = "<filtered: sensitive>"
            else:
                out[k] = _sanitize_value(kval)
        return out
    try:
        json.dumps(v, default=str)
        return v
    except (TypeError, ValueError):
        return repr(v)


def _serialize_inputs(args: tuple, kwargs: dict) -> dict:
    """序列化输入参数。"""
    args_safe = [_sanitize_value(a) for a in args]
    kwargs_safe = {}
    for k, v in kwargs.items():
        k_lower = str(k).lower()
        if any(fk in k_lower for fk in _FILTER_KEYS):
            kwargs_safe[k] = "<filtered: sensitive>"
        else:
            kwargs_safe[k] = _sanitize_value(v)
    return {"args": args_safe, "kwargs": kwargs_safe}


def _serialize_output(obj: Any) -> Any:
    """序列化输出，无法序列化时用 repr。"""
    try:
        sanitized = _sanitize_value(obj)
        json.dumps(sanitized, ensure_ascii=False, default=str)
        return sanitized
    except (TypeError, ValueError):
        return repr(obj)


def _extract_segment_id(video_path: str) -> int | None:
    """从 video_path 提取 segment_id。支持 _segment_1.mp4 与 -Scene-001.mp4。"""
    m = _SEGMENT_ID_PATTERN_SCENE.search(video_path) or _SEGMENT_ID_PATTERN_LEGACY.search(
        video_path
    )
    return int(m.group(1)) if m else None


def _get_trace_dir_for_video(video_path: str) -> str:
    """获取 traces 目录：当前视频所在目录下的 agent_trace 子目录。"""
    video_dir = os.path.dirname(os.path.abspath(video_path))
    log_dir = os.path.join(video_dir, "agent_trace")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


class TraceRecorder:
    """Episode - Iteration - Step 结构的埋点记录器。"""

    def __init__(
        self,
        video_path: str | None = None,
        base_dir: str | None = None,
        username: str | None = None,
    ):
        self.episode_id = f"ep_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        if base_dir:
            self.base_dir = base_dir
        elif video_path:
            self.base_dir = _get_trace_dir_for_video(video_path)
        else:
            self.base_dir = _get_trace_dir_for_video(".")
        os.makedirs(self.base_dir, exist_ok=True)
        self.current_iteration = 0
        self._pending_raw_segments: list[dict[str, Any]] | None = None
        self.data: dict[str, Any] = {
            "episode_id": self.episode_id,
            "username": username,
            "meta_info": {},
            "history": [],
        }
        self._start_new_iteration()

    def set_episode(self) -> None:
        """将当前实例注入 ContextVar，供 tools 使用。"""
        _recorder_var.set(self)

    def _start_new_iteration(self) -> None:
        """开始新的一轮 Plan-Act 循环。"""
        self.data["history"].append(
            {
                "iteration_id": self.current_iteration,
                "timestamp": datetime.datetime.now().isoformat(),
                "plan": {},
                "actions": [],
                "tool_calls": [],
                "verification": None,
            }
        )

    def set_meta_info(
        self,
        video_path: str,
        instruction: str,
        image_paths: list[str],
        time_length: int = 6,
    ) -> None:
        """设置 episode 元信息。"""
        self.data["meta_info"] = {
            "original_video": video_path,
            "user_instruction": instruction,
            "image_paths": _sanitize_value(image_paths),
            "time_length": time_length,
            "status": "pending_verify",
            "start_time": datetime.datetime.now().isoformat(),
        }
        self._save()

    def log_plan(
        self,
        raw_segments: list[dict[str, Any]] | None,
        refined_segments: list[dict[str, Any]],
        multi_stage_plan: dict[str, Any] | None = None,
    ) -> None:
        """
        记录 Planning 阶段的结果。

        - 单阶段时 refined_segments 为 timeline。
        - 多阶段时：**第一次调用**（Planner 刚结束）写入 original_multi_stage_plan 与当次的 final_multi_stage_plan；
          **第二次调用**（Actor 执行结束后）仅更新 final_multi_stage_plan（执行后状态），不覆盖 original_multi_stage_plan。
        """
        hist = self.data["history"][self.current_iteration]
        # 若已是第二次记录（仅更新执行后的多阶段 plan），只改 final，不覆盖整块 plan
        is_update_final_only = (
            multi_stage_plan is not None
            and (hist.get("plan") or {}).get("original_multi_stage_plan") is not None
        )
        if is_update_final_only:
            hist["plan"]["final_multi_stage_plan"] = _sanitize_value(multi_stage_plan)
            hist["plan"]["multi_stage_plan"] = hist["plan"]["final_multi_stage_plan"]
            self._save()
            return
        hist["plan"] = {
            "raw_model_output": _sanitize_value(raw_segments) if raw_segments else [],
            "final_execution_plan": _sanitize_value(refined_segments),
        }
        if multi_stage_plan is not None:
            # 首次记录：保存原始多阶段计划快照
            hist["plan"]["original_multi_stage_plan"] = _sanitize_value(multi_stage_plan)
            hist["plan"]["final_multi_stage_plan"] = _sanitize_value(multi_stage_plan)
            hist["plan"]["multi_stage_plan"] = hist["plan"]["final_multi_stage_plan"]
        # 初始化 actions：每个 segment 一条（多阶段时 refined_segments 为空，不生成 segment 级 actions）
        for seg in refined_segments:
            seg_id = seg.get("segment_id")
            editing_required = seg.get("editing_required", False)
            action_record = {
                "segment_id": seg_id,
                "relevance_analysis": {
                    "editing_required": editing_required,
                    "editing_prompt": seg.get("editing_prompt", ""),
                    "file_id": seg.get("file_id"),
                },
                "execution": {
                    "edited_file_path": None,
                    "status": "skipped" if not editing_required else "pending",
                },
            }
            hist["actions"].append(action_record)
        self._save()

    def set_plan_result(self, result: dict[str, Any]) -> None:
        """在 plan 下追加一个执行结果 summary（例如多阶段各阶段完成状态）。"""
        hist = self.data["history"][self.current_iteration]
        plan = hist.get("plan") or {}
        plan["execution_result"] = _sanitize_value(result)
        hist["plan"] = plan
        self._save()

    def log_action_result(
        self,
        segment_id: int,
        relevance_res: dict[str, Any],
        edit_res: str | None,
    ) -> None:
        """记录 ACT 阶段：更新对应 segment 的 execution。"""
        hist = self.data["history"][self.current_iteration]
        for action in hist["actions"]:
            if action.get("segment_id") == segment_id:
                action["relevance_analysis"] = {
                    "editing_required": relevance_res.get("editing_required"),
                    "editing_prompt": relevance_res.get("editing_prompt"),
                    "file_id": relevance_res.get("file_id"),
                }
                action["execution"] = {
                    "edited_file_path": edit_res,
                    "status": (
                        "skipped"
                        if not relevance_res.get("editing_required")
                        else ("success" if edit_res else "failed")
                    ),
                }
                break
        else:
            # 未找到则追加（兜底）
            hist["actions"].append(
                {
                    "segment_id": segment_id,
                    "relevance_analysis": {
                        "editing_required": relevance_res.get("editing_required"),
                        "editing_prompt": relevance_res.get("editing_prompt"),
                        "file_id": relevance_res.get("file_id"),
                    },
                    "execution": {
                        "edited_file_path": edit_res,
                        "status": "success" if edit_res else "failed",
                    },
                }
            )
        self._save()

    def log_tool_call(
        self,
        tool_name: str,
        args: tuple,
        kwargs: dict,
        result: Any,
        success: bool,
        duration: float,
        error_msg: str | None = None,
    ) -> None:
        """记录每次 Tool 调用的参数、返回值、耗时、状态。"""
        hist = self.data["history"][self.current_iteration]
        entry = {
            "tool_name": tool_name,
            "timestamp": datetime.datetime.now().isoformat(),
            "duration": round(duration, 4),
            "inputs": _serialize_inputs(args, kwargs),
            "outputs": _serialize_output(result) if success else None,
            "status": "success" if success else "error",
            "error_msg": error_msg,
        }
        hist["tool_calls"].append(entry)
        self._save()

    def log_verify(self, result: dict[str, Any]) -> None:
        """预留给未来的 Verify 步骤。"""
        hist = self.data["history"][self.current_iteration]
        hist["verification"] = result
        self._save()

    def set_final_output(self, final_output: str) -> None:
        """设置最终输出路径，更新 meta_info。"""
        self.data["meta_info"]["final_output"] = final_output
        self.data["meta_info"]["status"] = "completed"
        self.data["meta_info"]["end_time"] = datetime.datetime.now().isoformat()
        self._save()

    def _save(self) -> None:
        """将 data 写入 JSON 文件。"""
        try:
            file_path = os.path.join(self.base_dir, f"{self.episode_id}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[TraceRecorder] 写入失败: {e}")

    def record(self, func: Callable) -> Callable:
        """装饰器：按 tool 类型分发到语义记录。"""
        return _create_record_wrapper(func)


def _create_record_wrapper(func: Callable) -> Callable:
    """创建 record 装饰器的 wrapper，运行时从 Context 获取 recorder。"""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        rec = _recorder_var.get()
        start_time = time.time()
        success = True
        result = None
        error_msg = None
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            success = False
            error_msg = str(e)
            raise
        finally:
            if rec:
                try:
                    duration = time.time() - start_time
                    _dispatch_tool_record(
                        rec,
                        func.__name__,
                        args,
                        kwargs,
                        result,
                        success,
                        duration,
                        error_msg,
                    )
                except Exception as log_err:
                    print(f"[TraceRecorder] 记录失败: {log_err}")

    return wrapper


def _dispatch_tool_record(
    rec: TraceRecorder,
    tool_name: str,
    args: tuple,
    kwargs: dict,
    result: Any,
    success: bool,
    duration: float,
    error_msg: str | None,
) -> None:
    """根据 tool 名称分发到对应的语义记录逻辑。"""
    # 对所有 Tool 调用记录参数与返回
    rec.log_tool_call(
        tool_name=tool_name,
        args=args,
        kwargs=kwargs,
        result=result,
        success=success,
        duration=duration,
        error_msg=error_msg,
    )

    if tool_name == "video_split_tool" and success and result is not None:
        rec._pending_raw_segments = result if isinstance(result, list) else []

    elif tool_name == "fill_reference_images_tool" and success and result is not None:
        raw = rec._pending_raw_segments
        rec.log_plan(raw_segments=raw, refined_segments=result)
        rec._pending_raw_segments = None

    elif tool_name == "video_generate_tool":
        # 优先从 kwargs 获取 segment_id，否则从 video_path 提取
        seg_id = kwargs.get("segment_id")
        if seg_id is None:
            video_path = kwargs.get("video_path") or (args[0] if args else None)
            if video_path and isinstance(video_path, str):
                seg_id = _extract_segment_id(video_path)
        if seg_id is not None:
            # 从当前 actions 中查找该 segment 的 relevance（可能已有）
            hist = rec.data["history"][rec.current_iteration]
            relevance = {}
            for a in hist.get("actions", []):
                if a.get("segment_id") == seg_id:
                    relevance = a.get("relevance_analysis", {})
                    break
            # 若 actions 尚未初始化，用空 relevance
            rec.log_action_result(
                segment_id=seg_id,
                relevance_res=relevance
                or {
                    "editing_required": True,
                    "editing_prompt": kwargs.get("user_message", ""),
                },
                edit_res=result if success else None,
            )


def generate_episode_id() -> str:
    """生成 episode_id。"""
    return f"ep_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def get_recorder() -> TraceRecorder | None:
    """获取当前 Context 中的 TraceRecorder。"""
    return _recorder_var.get()


class _RecordDecorator:
    """供 tools 使用的装饰器代理，record 方法在运行时从 Context 获取 TraceRecorder。"""

    def record(self, func: Callable) -> Callable:
        return _create_record_wrapper(func)


# 供 tools 使用：@recorder.record
recorder = _RecordDecorator()
