"""Zhipu AI (ChatGLM) provider — OpenAI-compatible API.

Usage:
  AGENT_PROVIDER=zhipu
  MODEL_NAME=GLM-5.2
  ZHIPU_API_KEY=your-key
"""

import logging
from .openai import OpenAIProvider
from .base import ProviderRegistry

logger = logging.getLogger("nano_agent.providers.zhipu")

ZHIPU_BASE_URL = "https://z.ai/api/paas/v4/"


class ZhipuProvider(OpenAIProvider):
    """Zhipu AI provider — extends OpenAIProvider with Zhipu defaults."""

    def _get_api_key(self) -> str:
        import os
        return (self._config.openai_api_key or
                os.getenv("ZHIPU_API_KEY", ""))

    def _get_base_url(self) -> str:
        return (self._config.openai_base_url or ZHIPU_BASE_URL)


ProviderRegistry.register("zhipu", ZhipuProvider)
