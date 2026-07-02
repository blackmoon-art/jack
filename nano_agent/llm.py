"""
LLM 客户端抽象 — 统一 Anthropic / OpenAI / DeepSeek / OpenRouter 接口。

Provider 架构:
  LLM 类只负责重试、JSON 解析、模型路由。实际的 API 调用委托给
  BaseProvider 子类（AnthropicProvider / OpenAIProvider）。
  新增后端只需实现 BaseProvider 并注册到 ProviderRegistry。
"""

import json
import logging
import threading
import time
from typing import Any

from .config import Config
from .providers.base import BaseProvider, ProviderRegistry

# 确保 Provider 子类已注册（导入有副作用）
from .providers import AnthropicProvider, OpenAIProvider  # noqa: F401

logger = logging.getLogger("nano_agent.llm")


# ── 工具函数（模块级，不依赖 LLM 实例）──────────────────

def clean_json_response(text: str) -> str:
    """清理 LLM 返回的 JSON 文本：去 markdown 代码块包裹。

    使用正则匹配 ``` 代码块，处理嵌套和异常格式。
    支持 ```json / ``` 开头，容错不完整的结尾。
    """
    import re
    text = text.strip()
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def format_tool_call_for_message(tc: dict) -> dict:
    """
    将内部 tool_call 格式转为 OpenAI 兼容的 message 格式。
    内部: {"id": str, "name": str, "arguments": dict}
    输出: {"id": str, "type": "function", "function": {"name": str, "arguments": str}}
    """
    args = tc.get("arguments", {})
    if isinstance(args, dict):
        args = json.dumps(args, ensure_ascii=False)
    return {
        "id": tc.get("id", ""),
        "type": "function",
        "function": {
            "name": tc.get("name", ""),
            "arguments": args if isinstance(args, str) else str(args),
        },
    }


class LLM:
    """统一 LLM 调用接口，内置重试逻辑。Provider 懒加载，便于测试 mock。"""

    def __init__(self, config: Config):
        self.config = config
        self._provider: BaseProvider | None = None
        self._model_override: str | None = None
        self._provider_override: str | None = None
        self._provider_lock = threading.Lock()

    def set_model(self, model: str):
        """运行时覆盖模型名称。用于 Web UI 模型切换。

        线程安全：加锁防止并发请求读到半更新状态。
        不修改共享的 Config 对象，而是用实例级 _provider_override
        避免影响其他 session。
        """
        self._model_override = model
        self._provider_override = ProviderRegistry.resolve_by_model(model)
        with self._provider_lock:
            self._provider = None

    @property
    def _effective_provider_str(self) -> str:
        """当前生效的 provider 字符串。"""
        if self._provider_override is not None:
            return self._provider_override
        return self.config.provider

    @property
    def _model(self) -> str:
        return self._model_override or self.config.model

    def _get_provider(self) -> BaseProvider:
        """懒加载 Provider 实例（双检查锁）。"""
        if self._provider is not None:
            return self._provider
        with self._provider_lock:
            if self._provider is not None:
                return self._provider
            self._provider = ProviderRegistry.resolve_provider(
                self._effective_provider_str, self.config
            )
            return self._provider

    # ── 向后兼容：类方法委托到模块级工具函数 ─────────────

    @staticmethod
    def clean_json_response(text: str) -> str:
        return clean_json_response(text)

    @staticmethod
    def format_tool_call_for_message(tc: dict) -> dict:
        return format_tool_call_for_message(tc)

    # ── JSON 重试 ───────────────────────────────────────

    def chat_json_with_retry(self, messages: list[dict], max_retries: int = 2,
                             system: str = "", model: str | None = None,
                             tools: list = None) -> Any:
        """调用 LLM 并解析 JSON 响应，失败自动重试。

        统一的 JSON 重试逻辑，供 Orient、BaseStrategy._chat_json、
        Reflexion 等共用，避免重复实现。

        Returns:
            解析后的 Python 对象 (dict/list)，或 None（全部重试失败）
        """
        for attempt in range(max_retries + 1):
            response = self.chat(messages=messages, tools=tools or [], system=system,
                                  model=model)
            text = self.clean_json_response(response["text"])
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if attempt < max_retries:
                    logger.warning(
                        f"JSON parse failed (attempt {attempt+1}/{max_retries+1}), "
                        f"retrying... Response: {text[:200]}")
                else:
                    logger.warning(
                        f"JSON parse failed after {max_retries+1} attempts. "
                        f"Response: {text[:200]}")
        return None

    # ── 主入口 ──────────────────────────────────────────

    def chat(self, messages: list, tools: list, system: str = "",
             model: str | None = None) -> dict:
        """
        调用 LLM，返回统一格式。

        Args:
            model: 单次调用的模型覆盖（不修改实例状态，线程安全）
        """
        for attempt in range(3):
            try:
                provider = self._get_provider()
                result = provider.chat(
                    messages=messages, tools=tools, system=system,
                    model=model or self._model,
                    max_tokens=self.config.max_tokens,
                    timeout=120,
                )
                return result
            except Exception as e:
                retryable = False
                err_str = str(e).lower()
                status_code = (
                    getattr(e, 'status_code', None)
                    or getattr(getattr(e, 'response', None), 'status_code', None)
                )
                if status_code in (429, 500, 502, 503, 504):
                    retryable = True
                elif any(kw in err_str for kw in (
                    'rate_limit', 'timeout', 'connection', 'overloaded', 'server error',
                )):
                    retryable = True

                if not retryable or attempt == 2:
                    raise
                wait = 2 ** attempt
                retry_after = (
                    getattr(getattr(e, 'response', None), 'headers', {})
                    .get('Retry-After')
                )
                if retry_after:
                    try:
                        wait = min(int(retry_after), 30)
                    except (ValueError, TypeError):
                        pass
                logger.warning(
                    f"LLM retryable error (attempt {attempt+1}/3): {e}, waiting {wait}s"
                )
                time.sleep(wait)

        raise RuntimeError("unreachable")

    # ── 流式 ────────────────────────────────────────────

    def chat_stream(self, messages: list, system: str = "", tools: list = None,
                    model: str | None = None):
        """流式调用 LLM，yield 文本片段或 tool_calls 信号。

        Args:
            model: 单次调用的模型覆盖（不修改实例状态，线程安全）

        Yields:
            str: 文本 chunk，或 dict: {"type": "tool_calls", "tool_calls": [...]}
        """
        provider = self._get_provider()
        yield from provider.chat_stream(
            messages=messages, system=system, tools=tools,
            model=model or self._model,
            max_tokens=self.config.max_tokens,
        )
