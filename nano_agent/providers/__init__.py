"""Provider 包 — LLM 后端的可插拔实现。

通过 ProviderRegistry 注册，LLM 类自动路由到正确的 Provider。
新增后端只需实现 BaseProvider 并注册，无需修改 LLM 或 Agent。
"""

from .base import BaseProvider, ProviderRegistry
from .anthropic import AnthropicProvider
from .openai import OpenAIProvider

__all__ = [
    "BaseProvider",
    "ProviderRegistry",
    "AnthropicProvider",
    "OpenAIProvider",
]
