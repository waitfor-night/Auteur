from __future__ import annotations

import os

from openai import OpenAI
from textgrad.engine.local_model_openai_api import ChatExternalClient

MAX_TOKENS = 100000


class ChatExternalClientWithMaxTokens(ChatExternalClient):
    """在初始化时注入 max_tokens，所有 generate 调用均使用该上限。"""

    def __init__(self, *, max_tokens: int = MAX_TOKENS, **kwargs):
        super().__init__(**kwargs)
        self._max_tokens = max_tokens

    def generate(self, content, system_prompt=None, **kwargs):
        kwargs.setdefault("max_tokens", self._max_tokens)
        return super().generate(content, system_prompt=system_prompt, **kwargs)


LLM_CONFIG = [
    {
        "model": "doubao-seed-2-0-pro-260215",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_env": "ARK_API_KEY",
    },
    {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    {
        "model": "kimi-k2-turbo-preview",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
    },
]


def get_engine_from_builtin(model_name: str):
    """从 LLM_CONFIG 按 model 名取配置；未找到返回 None（交给 TextGrad 默认）。"""
    for c in LLM_CONFIG:
        if c.get("model") == model_name:
            api_key = os.environ.get(c["api_key_env"])
            if not api_key:
                raise ValueError(
                    f"Environment variable {c['api_key_env']!r} is not set (required for model {model_name!r})."
                )
            client = OpenAI(base_url=c["base_url"], api_key=api_key)
            return ChatExternalClientWithMaxTokens(
                client=client, model_string=c["model"], max_tokens=MAX_TOKENS
            )
    return None
