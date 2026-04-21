"""
剧本分析与图生原子工具。

1. extract_script_entities: 从剧本中抽取核心人物与场景描述。
2. generate_image: 根据单条文本和可选参考图生成图片（原子工具），支持组图；main 中可对多个人物/场景分次调用并汇总路径。
"""
import agno
from agno.tools import tool
from utils.trace_recorder import recorder
import os
import re
import sys
import json
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime.types.images.images import SequentialImageGenerationOptions


# 用于规划/抽取的对话模型
CHAT_MODEL = "doubao-seed-2-0-pro-260215"

# ---------- 工具：从剧本抽取核心人物与场景 ----------
EXTRACT_ENTITIES_SYSTEM = """你是一个剧本分析专家。用户会给出一段剧本/分镜描述。

你的任务：从剧本中抽取「核心人物」和「场景」两类信息，且只输出一个 JSON 对象，不要其他文字。

要求：
1. 核心人物：列出剧本中的主要角色，每人包含 name（称呼/名字）、description（简短形象或身份描述）。
2. 场景：列出剧本中出现的核心场景，每项为简短描述（时间、地点、环境等）。

输出格式严格为（不要 markdown 代码块包裹，直接输出 JSON）：
{"characters": [{"name": "角色名", "description": "简短描述"}], "scenes": ["场景1描述", "场景2描述", ...]}"""


def extract_script_entities(script: str) -> Dict[str, Any]:
    """
    根据剧本文本，抽取其中的核心人物和场景描述。

    Args:
        script: 剧本/分镜的完整文本。

    Returns:
        包含 "characters" 和 "scenes" 的字典：
        - characters: [{"name": str, "description": str}, ...]
        - scenes: [str, ...]
        若解析失败则返回 {"characters": [], "scenes": []}。
    """
    script = (script or "").strip()
    if not script:
        return {"characters": [], "scenes": []}

    client = Ark(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=os.getenv("ARK_API_KEY"),
    )
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": EXTRACT_ENTITIES_SYSTEM},
            {"role": "user", "content": script},
        ],
        temperature=0.3,
    )
    content = (resp.choices[0].message.content or "").strip()
    if "```" in content:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if match:
            content = match.group(1).strip()
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            return {"characters": [], "scenes": []}
        characters = data.get("characters")
        scenes = data.get("scenes")
        if not isinstance(characters, list):
            characters = []
        if not isinstance(scenes, list):
            scenes = []
        return {"characters": characters, "scenes": scenes}
    except json.JSONDecodeError:
        return {"characters": [], "scenes": []}



# ========== Tools (agno @tool) ==========
@tool
@recorder.record
def extract_script_entities_tool(script: str) -> Dict[str, Any]:
    """
    Extract the entities from the script.
    Args:
        script: The script to extract entities from.
    Returns: The entities.
    """
    return extract_script_entities(script)




