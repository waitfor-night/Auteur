#!/usr/bin/env python3
"""
UserSimulator: RolePlay 虚拟用户，根据 Plan 判断是否满足期望 Tool 顺序并给出反馈。
用于沙箱实验：Role 看到 Planner 产出的 Plan（无视频），给出 (feedback, is_accepted)。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agno.agent import Agent
from agno.models.openai import OpenAIResponses

try:
    from .watch_media_tool import watch_media_tool  # type: ignore
except Exception:  # pragma: no cover
    from sandbox.watch_media_tool import watch_media_tool  # type: ignore

# 项目根目录
_SANDBOX_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _SANDBOX_ROOT.parent


def load_roles_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """加载 roles.yaml，返回 { role_id: role_config }。"""
    if config_path is None:
        config_path = _SANDBOX_ROOT / "config" / "roles.yaml"
    if not config_path.is_file():
        return {}
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    roles = data.get("roles") or {}
    return roles


class _ContextBuilder:
    """保存多轮 roleplay 的交互历史与本地媒体理解缓存（用于避免重复 watch）。"""

    def __init__(self, max_rounds_in_prompt: int = 3):
        self.max_rounds_in_prompt = max_rounds_in_prompt
        self.round_history: List[Dict[str, Any]] = []
        # abs_path -> {"type": ..., "description": ...}
        self.media_cache: Dict[str, Dict[str, str]] = {}

    def add_round(
        self,
        *,
        task: str,
        plan_summary: str,
        tools_execute_order: Optional[List[str]],
        assistant_output: str,
        watched_media_paths: List[str],
        watched_media_results: List[Dict[str, str]],
        feedback: str,
        accepted: bool,
    ) -> None:
        for r in watched_media_results:
            p = (r.get("path") or "").strip()
            if not p:
                continue
            self.media_cache[p] = {
                "type": (r.get("type") or ""),
                "description": (r.get("description") or ""),
            }

        self.round_history.append(
            {
                "task": task,
                "plan_summary": plan_summary,
                "tools_execute_order": tools_execute_order or [],
                "assistant_output": assistant_output,
                "watched_media_paths": watched_media_paths,
                "feedback": feedback,
                "accepted": accepted,
            }
        )

    def build_history_prompt(self) -> str:
        if not self.round_history:
            return ""
        recent = self.round_history[-self.max_rounds_in_prompt :]
        chunks: List[str] = []
        for i, r in enumerate(recent, 1):
            chunks.append(
                f"[History Round {i}] accepted={r.get('accepted')}\n"
                f"Task: {str(r.get('task') or '')[:500]}\n"
                f"Feedback: {(r.get('feedback') or '')[:800]}\n"
            )
        return "\n".join(chunks).strip()

    def get_cached_media_descriptions(self, media_paths: List[str], limit: int = 3) -> List[str]:
        out: List[str] = []
        for p in media_paths:
            cached = self.media_cache.get(p)
            if not cached:
                continue
            desc = cached.get("description") or ""
            out.append(f"- {p} ({cached.get('type') or 'media'}): {desc[:600]}")
            if len(out) >= limit:
                break
        return out


class UserSimulator:
    """
    roleplay 复盘虚拟用户：
    - 输入：task + plan_summary + tools_execute_order + assistant 本轮输出字符串（含媒体路径）
    - 内部：从 assistant 输出中提取媒体路径 -> 调用 sandbox/watch_media_tool 观看理解（带本地缓存）-> agno 生成反馈与 accepted
    - 多轮交互：只由外部调用一次 review；本类通过 context_builder 保存历史与媒体理解结果，避免重复 watch。
    """

    def __init__(
        self,
        role_id: str,
        role_config: Optional[Dict[str, Any]] = None,
        model: str = "doubao-seed-2-0-pro-260215",
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        api_key_env: str = "ARK_API_KEY",
    ):
        self.role_id = role_id
        if role_config is None:
            roles = load_roles_config()
            role_config = roles.get(role_id) or {}
        self.role_config = role_config
        self.persona_name = role_config.get("persona_name") or role_id
        self.persona_prompt = role_config.get("persona_prompt") or ""
        self.expected_tasks = role_config.get("expected_tasks") or []
        self.expected_tool_order = role_config.get("expected_tool_order") or []
        self.model = model
        self.base_url = base_url
        self.api_key = os.environ.get(api_key_env) or ""

        self.context_builder = _ContextBuilder(max_rounds_in_prompt=3)

        self._agent = Agent(
            name="user-simulator-roleplay",
            description=self._build_system_prompt(),
            tools=[watch_media_tool],
            model=OpenAIResponses(
                id=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
            ),
        )

    def _build_system_prompt(self) -> str:
        expected_tasks = "、".join(self.expected_tasks) if self.expected_tasks else "（无）"
        expected_order = "\n".join(f"- {x}" for x in self.expected_tool_order) if self.expected_tool_order else "（无）"

        feedback_style = self.role_config.get("feedback_style") or "真实、具体、以用户口吻提出改进建议"
        patience = self.role_config.get("patience") or "中等"

        domain = self.role_config.get("domain") or "视频制作"
        goal = self.role_config.get("goal") or "产出符合计划并连贯无明显问题的成片"
        task_type = self.role_config.get("type") or expected_tasks

        content_pref = self.role_config.get("content_preference") or "更看重画面内容是否连贯、是否重复/跳切、人物是否一致"
        process_pref = self.role_config.get("process_preference") or "希望执行过程按清晰的步骤推进：理解/检查 -> 规划 -> 生成/编辑 -> 最后合成"

        planning_pref = self.role_config.get("planning_preference") or "喜欢把计划拆成明确阶段与检查点"
        execution_pref = self.role_config.get("execution_preference") or (expected_order.replace("- ", "").strip() or "（无）")

        return (
            "你是一个视频制作任务中的用户，请严格扮演以下角色，并根据 Assistant 的计划、执行过程、执行顺序和媒体结果给出真实用户式反馈。"
            "\n\n"
            "角色：\n"
            f"名字：{self.persona_name}\n"
            f"背景：{self.persona_prompt}\n"
            f"反馈风格：{feedback_style}\n"
            f"耐心程度：{patience}\n"
            "\n"
            "常见任务：\n"
            f"领域：{domain}\n"
            f"目标：{goal}\n"
            f"类型：{task_type}\n"
            "\n"
            "偏好：\n"
            f"内容偏好：{content_pref}\n"
            f"过程偏好：{process_pref}\n"
            "\n"
            "对 Assistant 的期待：\n"
            f"喜欢的规划方式：{planning_pref}\n"
            f"喜欢的执行顺序：{execution_pref}\n"
            "\n"
            "你将收到：\n"
            "- task（任务文本）\n"
            "- plan_summary（planner 计划摘要，字符串）\n"
            "- tools_execute_order（执行顺序，字符串列表）\n"
            "- assistant_output（actor 输出字符串，含执行轨迹与媒体路径）\n"
            "- media_inspection（已观看/理解得到的媒体文字证据）\n"
            "\n"
            "要求：\n"
            "- 请像真实用户一样直接说“我觉得哪里不对/我希望怎么改”。不要做旁白分析，也不要解释你为何这么判断。\n"
            "- 请像真实用户一样，基于watch_media_tool的观看结果，给出具体的反馈。不要只说“我觉得哪里不对”，要具体到哪个镜头、哪个动作、哪个角色、哪个场景。\n"
            "- feedback 必须基于生成的媒体内容和结果是否符合期待来写；如果只有计划信息而没有充分媒体证据，请提出“需要执行结果/再看更多媒体/缺少关键信息”的要求。\n"
            "- accepted= true 表示你觉得这轮结果已经可以通过；否则 accepted=false。\n"
            "\n"
            "输出格式要求：只输出一个严格 JSON。\n"
            "{\n"
            '  "feedback": string,\n'
            '  "accepted": boolean\n'
            "}\n"
            "不要输出任何多余文本。feedback 用中文，简短但具体。"
        )

    def review(
        self,
        task: str,
        plan_summary: str,
        tools_execute_order: Optional[list] = None,
        *,
        actor_output: Optional[str] = None,
        assistant_output_dir: Optional[str | Path] = None,
    ) -> Tuple[str, bool]:
        actor_output = (actor_output or "").strip()
        assistant_output_dir_path: Optional[Path] = None
        if assistant_output_dir is not None:
            assistant_output_dir_path = Path(assistant_output_dir).resolve()

        tools_execute_order_list = tools_execute_order or []

        # 提取候选媒体路径（从 actor_output 字符串中）
        extracted_paths = self._extract_media_paths_from_text(actor_output)
        resolved_paths = self._resolve_paths(extracted_paths, assistant_output_dir_path)

        watch_prompt_for_tool = (
            "你是严谨的质检员。请基于该视频/图片内容，用中文用不超过6条要点总结："
            "1) 画面发生了什么（动作/事件）；2) 人物/角色是否一致；"
            "3) 是否存在明显重复镜头或不连贯衔接；4) 与计划/期望是否相符。"
        )

        # 先把缓存证据展示给 agent；agent 仍可根据 assistant_output 自动识别路径并调用 watch_media_tool
        cached_media_evidence = self.context_builder.get_cached_media_descriptions(resolved_paths, limit=3)
        media_inspection = "\n".join(cached_media_evidence).strip() if cached_media_evidence else ""
        history_prompt = self.context_builder.build_history_prompt()

        user_prompt = (
            f"task（任务文本）：\n{task}\n\n"
            "plan_summary（planner 计划摘要）：\n"
            f"{(plan_summary or '')[:]}\n\n"
            "tools_execute_order：\n"
            + "\n".join(f"- {x}" for x in tools_execute_order_list)[:]
            + "\n\n"
            "assistant_output（actor 输出字符串，含执行轨迹与媒体路径）：\n"
            f"{actor_output[:]}\n\n"
            f"assistant_output_dir（用于解析相对媒体路径到绝对路径）：\n{str(assistant_output_dir_path) if assistant_output_dir_path is not None else '(无)'}\n\n"
            "watch_prompt（传给 watch_media_tool 的 watch_prompt 参数原样使用）：\n"
            f"{watch_prompt_for_tool}\n\n"
            "media_inspection（来自上下文缓存的已观看/理解证据；如果为空，表示你需要调用 watch_media_tool 来得到证据）：\n"
            f"{(media_inspection or '（无缓存证据）')[:]}\n\n"
            "观看规则：\n"
            "- 请从 assistant_output 中识别所有媒体路径（.mp4/.png/.jpg/.webp/.gif 等）。\n"
            "- 若媒体路径是相对路径，请先以 assistant_output_dir 解析为绝对路径，再与 media_inspection 中的缓存证据路径对比；对尚未缓存的绝对路径调用 watch_media_tool；已缓存的不重复调用。\n"
            "- 为了节省成本：最多只调用 5 个尚未缓存的媒体路径；优先 result_video，其次 actions[].edited_video（按出现顺序从前往后）。\n"
            "多轮历史摘要（最近几轮 feedback 可能用于判断）：\n"
            f"{history_prompt[:] if history_prompt else '（无）'}\n\n"
            '请输出严格 JSON：{"feedback": string, "accepted": boolean}。'
        )

        # 让 watch_media_tool 能把 agent 传入的相对路径解析为正确的绝对路径
        prev_base_dir = os.environ.get("SANDBOX_MEDIA_BASE_DIR")
        try:
            if assistant_output_dir_path is not None:
                os.environ["SANDBOX_MEDIA_BASE_DIR"] = str(assistant_output_dir_path)
            agent_response = self._agent.run(user_prompt)
        finally:
            if prev_base_dir is None:
                os.environ.pop("SANDBOX_MEDIA_BASE_DIR", None)
            else:
                os.environ["SANDBOX_MEDIA_BASE_DIR"] = prev_base_dir
        content = (getattr(agent_response, "content", None) or getattr(agent_response, "output", None) or "") or ""
        # agno RunResponse.content 的类型可能随版本变化，尽量兼容
        if not content and hasattr(agent_response, "content"):
            content = agent_response.content
        content = str(content).strip()

        feedback, accepted = self._parse_feedback_and_accepted(content)

        watched_media_results = self._extract_watch_media_results_from_agent_response(agent_response)

        # 更新 context_builder（用于下一轮）
        self.context_builder.add_round(
            task=task,
            plan_summary=plan_summary,
            tools_execute_order=tools_execute_order_list,
            assistant_output=actor_output,
            watched_media_paths=[r.get("path") for r in watched_media_results if r.get("path")],
            watched_media_results=watched_media_results,
            feedback=feedback,
            accepted=accepted,
        )

        return feedback, accepted

    def _parse_feedback_and_accepted(self, content: str) -> Tuple[str, bool]:
        if content:
            # 尝试严格 JSON 解析
            try:
                obj = json.loads(content)
                feedback = str(obj.get("feedback") or "").strip()
                accepted = bool(obj.get("accepted"))
                if feedback:
                    return feedback, accepted
            except Exception:
                pass

            # 尝试提取 JSON 子串
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    obj = json.loads(content[start : end + 1])
                    feedback = str(obj.get("feedback") or "").strip()
                    accepted = bool(obj.get("accepted"))
                    if feedback:
                        return feedback, accepted
                except Exception:
                    pass

        # 最后容错：accepted 关键词
        upper = content.upper() if content else ""
        accepted = ("ACCEPTED" in upper) or ("满意" in content) or ("接受" in content)
        feedback = (content or "").strip()
        if not feedback:
            feedback = "（生成反馈失败）"
        return feedback[:2000], bool(accepted)

    def _extract_watch_media_results_from_agent_response(self, agent_response: Any) -> List[Dict[str, str]]:
        """
        从 agno 的 run_response 中尽量提取 watch_media_tool 的 tool 输出。
        兼容不同版本 agno 对 tool_calls/result 字段的结构差异。
        """
        tool_name = "watch_media_tool"
        out: List[Dict[str, str]] = []

        def _maybe_get_tool_name(tc: Any) -> str:
            if hasattr(tc, "name"):
                return str(getattr(tc, "name") or "")
            if isinstance(tc, dict):
                return str(tc.get("name") or tc.get("tool_name") or "")
            return ""

        def _maybe_get_tool_result(tc: Any) -> Any:
            for k in ("result", "output", "outputs", "content"):
                if hasattr(tc, k):
                    return getattr(tc, k)
            if isinstance(tc, dict):
                for k in ("result", "output", "outputs", "content"):
                    if k in tc:
                        return tc.get(k)
            return None

        messages = getattr(agent_response, "messages", None) or []
        for m in messages:
            tool_calls = getattr(m, "tool_calls", None) or []
            for tc in tool_calls:
                if _maybe_get_tool_name(tc) != tool_name:
                    continue
                tr = _maybe_get_tool_result(tc)
                if tr is None:
                    continue
                if isinstance(tr, str):
                    # tool 输出可能是 JSON 字符串
                    try:
                        tr_obj = json.loads(tr)
                        tr = tr_obj
                    except Exception:
                        tr = None
                if not isinstance(tr, dict):
                    continue
                results = tr.get("results") or []
                for r in results:
                    if not isinstance(r, dict):
                        continue
                    p = (r.get("path") or "").strip()
                    d = (r.get("description") or "").strip()
                    if not p or not d:
                        continue
                    out.append(
                        {
                            "path": p,
                            "type": (r.get("type") or "media"),
                            "description": d,
                        }
                    )

        # 兼容：有些 agno 版本可能直接把 tool_calls 挂在 run_response 上
        extra_tool_calls = getattr(agent_response, "tool_calls", None) or []
        for tc in extra_tool_calls:
            if _maybe_get_tool_name(tc) != tool_name:
                continue
            tr = _maybe_get_tool_result(tc)
            if tr is None:
                continue
            if isinstance(tr, str):
                try:
                    tr_obj = json.loads(tr)
                    tr = tr_obj
                except Exception:
                    tr = None
            if not isinstance(tr, dict):
                continue
            results = tr.get("results") or []
            for r in results:
                if not isinstance(r, dict):
                    continue
                p = (r.get("path") or "").strip()
                d = (r.get("description") or "").strip()
                if not p or not d:
                    continue
                out.append({"path": p, "type": (r.get("type") or "media"), "description": d})

        # 去重（按 path）
        seen: set[str] = set()
        dedup: List[Dict[str, str]] = []
        for r in out:
            p = r.get("path") or ""
            if not p or p in seen:
                continue
            seen.add(p)
            dedup.append(r)
        return dedup

    def _extract_media_paths_from_text(self, text: str) -> List[str]:
        if not text:
            return []

        # 1) 先尝试 outer json 解析
        obj = self._try_load_json_obj(text)
        if isinstance(obj, dict):
            paths: List[str] = []
            rv = obj.get("result_video")
            if isinstance(rv, str) and rv.strip():
                paths.append(rv.strip())
            actions = obj.get("actions") or []
            if isinstance(actions, list):
                for a in actions:
                    if not isinstance(a, dict):
                        continue
                    ev = a.get("edited_video")
                    if isinstance(ev, str) and ev.strip():
                        paths.append(ev.strip())
            return self._dedupe_preserve_order(paths)

        # 2) 正则兜底：抓所有带常见媒体后缀的路径片段
        pattern = re.compile(
            r'(?P<path>(?:/[\w\-.]+/|\.{1,2}/|[\w\-.]+/)*[\w\-.]+\.(?:mp4|mov|mkv|avi|flv|wmv|m4v|png|jpg|jpeg|webp|gif))'
            r"\b"
        )
        matches = [m.group("path") for m in pattern.finditer(text)]
        return self._dedupe_preserve_order([m for m in matches if m and "." in m])

    def _try_load_json_obj(self, text: str) -> Any:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        snippet = text[start : end + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None

    def _resolve_paths(self, paths: List[str], assistant_output_dir: Optional[Path]) -> List[str]:
        out: List[str] = []
        for p in paths:
            if not p:
                continue
            pp = Path(str(p).strip())
            if pp.is_absolute():
                out.append(str(pp))
                continue
            if assistant_output_dir is not None:
                out.append(str((assistant_output_dir / pp).resolve()))
            else:
                out.append(str(pp.resolve()))
        return self._dedupe_preserve_order(out)

    @staticmethod
    def _dedupe_preserve_order(items: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for x in items:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out


def get_role_config(role_id: str) -> Dict[str, Any]:
    """按 role_id 获取角色配置。"""
    roles = load_roles_config()
    return roles.get(role_id) or {}
