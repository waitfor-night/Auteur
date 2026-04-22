from agno.tools import tool
from typing import Optional
from utils.trace_recorder import recorder


@tool
@recorder.record
def load_skill_tool(
    InitUserMessage: str = "",
    skill_type: Optional[str] = None,
) -> str:
    """
    通用的 skill 加载工具，根据 skill_type 加载对应的 skill 内容。支持多次调用。
    Args:
        InitUserMessage: 用户输入，用于在未指定 skill_type 时自动推断。
        skill_type: 要加载的 skill 类型（参见 Available Skills）。如果不传，会根据 InitUserMessage 自动推断。

    Returns:
        skill 的内容，包含该 skill 的详细指导说明（仅 task-skill，不含 meta-skill）。
    """
    from skills.skill_loader import infer_task_type, load_skill, ALL_SKILLS
    from utils.context_recorder import RunContext

    print(f"\n[DEBUG] load_skill_tool 被调用: skill_type={skill_type}")

    if skill_type:
        target_skill = skill_type
    elif InitUserMessage:
        target_skill = infer_task_type(InitUserMessage)
    else:
        target_skill = "VFX_EDIT"

    if target_skill in ALL_SKILLS:
        skill_content = load_skill(ALL_SKILLS[target_skill])
        if skill_content:
            ctx = RunContext.get_current()
            if ctx is not None:
                ctx.add_round_skill(target_skill, skill_content)
            return f"## Skill guidance (current: {target_skill})\n\n{skill_content}"

    return f"[Warning] Unknown skill_type: {target_skill}"


__all__ = ["load_skill_tool"]