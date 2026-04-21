from agno.tools import tool
from typing import Optional, Union, Dict, Any, List
from utils.trace_recorder import recorder
import json

@tool(requires_user_input=True, user_input_fields=["clarified_user_info"])
@recorder.record
def getUserMessageTool(
    InitUserMessage: str,
    message: str = "",
    clarified_user_info: Optional[Union[str, Dict[str, Any], List[Any]]] = None,
    **kwargs: Any,  # 允许传递任意额外参数
) -> Union[str, Dict[str, Any]]:
    """
    Get any information from the user that is needed but not available in runtime inputs.
    This is a general-purpose tool for collecting user input.

    When to use:
    - Whenever planning cannot proceed without extra user-provided information.
      This is intentionally generic: it can be used for clarifying instructions, providing reference
      resource paths, supplying constraints, or any other missing details.

    IMPORTANT:
    - InitUserMessage: This is ALREADY KNOWN from the user's initial input (found in "### Runtime Inputs" section).
      Agent MUST read it from runtime inputs and pass it here. This is NOT a user input field.
    - message: INTERNAL (agent-filled). The agent should decide what information is still needed
      (not limited to InitUserMessage clarification; it may include reference image/video paths, IDs, constraints,
      output requirements, etc.) and write a concise checklist for the user to provide, including an example
      format if helpful. This field is displayed to the user when prompting for clarified_user_info.
    - clarified_user_info: Filled by the system with user input. Accepts: JSON string, dict, plain string, or list.

    Args:
        InitUserMessage: The user's original InitUserMessage from runtime inputs. Agent must pass here. NOT user input.
        message: INTERNAL (agent-filled). A human-facing checklist of what the user should provide next.
        clarified_user_info: Filled by the system with user input.
        **kwargs: Any extra fields from agent, included in result.

    Returns:
        Union[str, Dict[str, Any]]: User's clarified_user_info, parsed as dict or string.
    """
    result: Dict[str, Any] = {}

    # 添加所有 kwargs 到结果中
    if kwargs:
        result.update(kwargs)

    # 处理 clarified_user_info
    if clarified_user_info is not None:
        if isinstance(clarified_user_info, str):
            # 尝试解析 JSON
            try:
                parsed = json.loads(clarified_user_info)
                if isinstance(parsed, dict):
                    result.update(parsed)
                elif isinstance(parsed, list):
                    # 如果是列表，转换为字典
                    result["items"] = parsed
                else:
                    # 其他类型，作为字符串值
                    result["clarified_edit_prompt"] = str(parsed)
            except (json.JSONDecodeError, ValueError):
                # 不是 JSON，当作纯文本处理
                result["clarified_edit_prompt"] = clarified_user_info

        elif isinstance(clarified_user_info, dict):
            # 直接使用字典
            result.update(clarified_user_info)

        elif isinstance(clarified_user_info, list):
            # 列表转换为字典
            result["items"] = clarified_user_info

        else:
            # 其他类型转换为字符串
            result["value"] = str(clarified_user_info)

    # 如果没有提供任何信息，返回空字典或包含 InitUserMessage
    if not result:
        if InitUserMessage:
            result["InitUserMessage"] = InitUserMessage
        return result

    # 如果结果是单个字符串字段（向后兼容），返回字符串
    if len(result) == 1 and "clarified_edit_prompt" in result:
        return result["clarified_edit_prompt"]

    # 否则返回字典
    return result

__all__ = ["getUserMessageTool"]