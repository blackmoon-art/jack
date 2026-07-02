"""BaseProvider — 所有 LLM 后端的抽象接口。

Provider 负责:
  - 客户端创建和管理
  - 后端特定的 API 调用（chat / chat_stream）
  - 消息格式转换（如 Anthropic content blocks）

Provider 不负责:
  - 重试逻辑（由 LLM 类统一处理）
  - JSON 解析重试（由 LLM.chat_json_with_retry 处理）
  - 模型名 → provider 路由（由 ProviderRegistry 处理）
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("nano_agent.providers")


class BaseProvider(ABC):
    """所有 LLM Provider 的抽象基类。

    子类必须实现 chat() 和 chat_stream()。
    """

    @abstractmethod
    def chat(self, messages: list, tools: list, system: str,
             model: str, max_tokens: int, timeout: int = 120) -> dict:
        """非流式调用 LLM。

        Returns:
            {"text": str, "tool_calls": [{"id","name","arguments"}, ...],
             "stop_reason": str, "reasoning_content": str}
        """

    @abstractmethod
    def chat_stream(self, messages: list, system: str, tools: list | None,
                    model: str, max_tokens: int):
        """流式调用 LLM。

        Yields:
            str: 文本 chunk
            dict: {"type": "tool_calls", "tool_calls": [...]}
            dict: {"type": "reasoning", "text": "..."}
        """

    def _validate_response(self, response: dict) -> dict:
        """验证并规范化 Provider 返回的 response dict。"""
        response.setdefault("text", "")
        response.setdefault("tool_calls", [])
        response.setdefault("stop_reason", "stop")
        response.setdefault("reasoning_content", "")
        return response


# ── Provider Registry ────────────────────────────────────

class ProviderRegistry:
    """管理 Provider 的注册和查找。

    两个分发维度:
      1. provider 字符串（"anthropic" / "openai_compatible"）→ Provider 类
      2. 模型名前缀 → provider 字符串（用于 set_model() 自动检测）
    """

    _MODEL_PREFIX_MAP: dict[str, str] = {
        "claude": "anthropic",
        "deepseek": "openai_compatible",
        "qwen": "openai_compatible",
        "glm": "openai_compatible",
        "moonshot": "openai_compatible",
    }

    _PROVIDER_MAP: dict[str, type[BaseProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: type[BaseProvider]):
        """注册一个 Provider 类。"""
        cls._PROVIDER_MAP[name] = provider_cls

    @classmethod
    def resolve_provider(cls, provider_str: str, config: Any) -> BaseProvider:
        """根据 provider 字符串创建 Provider 实例。"""
        provider_cls = cls._PROVIDER_MAP.get(provider_str)
        if provider_cls is None:
            logger.warning(
                f"Unknown provider '{provider_str}', falling back to openai_compatible"
            )
            provider_cls = cls._PROVIDER_MAP.get("openai_compatible")
            if provider_cls is None:
                raise ValueError(
                    f"No provider registered for '{provider_str}' "
                    f"and no fallback available"
                )
        return provider_cls(config)

    @classmethod
    def resolve_by_model(cls, model: str) -> str:
        """根据模型名推断 provider 字符串。"""
        for prefix, provider in cls._MODEL_PREFIX_MAP.items():
            if model.startswith(prefix):
                return provider
        return "openai_compatible"
