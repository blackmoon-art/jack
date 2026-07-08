"""Zhipu AI (ChatGLM) provider — OpenAI-compatible API.

Usage:
  AGENT_PROVIDER=zhipu
  MODEL_NAME=GLM-5.2
  ZHIPU_API_KEY=your-key
"""

import logging
import os
from .openai import OpenAIProvider
from .base import ProviderRegistry

logger = logging.getLogger("nano_agent.providers.zhipu")

ZHIPU_BASE_URL = "https://api.z.ai/api/coding/paas/v4/"


class ZhipuProvider(OpenAIProvider):
    """Zhipu AI provider — extends OpenAIProvider with Zhipu defaults."""

    def _get_api_key(self) -> str:
        return (os.getenv("ZHIPU_API_KEY", "") or
                self._config.openai_api_key)

    def _get_base_url(self) -> str:
        return (ZHIPU_BASE_URL if os.getenv("ZHIPU_API_KEY") else
                self._config.openai_base_url)


ProviderRegistry.register("zhipu", ZhipuProvider)
