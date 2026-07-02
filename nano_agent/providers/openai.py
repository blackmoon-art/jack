"""OpenAI Provider — 封装 OpenAI SDK（兼容 DeepSeek / OpenRouter / Ollama 等）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from .base import BaseProvider, ProviderRegistry

logger = logging.getLogger("nano_agent.providers.openai")


class OpenAIProvider(BaseProvider):
    """通过 OpenAI SDK 调用所有 OpenAI 兼容的 API。"""

    def __init__(self, config: Any):
        self._config = config
        self._client = None

    def _get_client(self):
        """懒加载 OpenAI 客户端。"""
        if self._client is not None:
            return self._client
        from openai import OpenAI

        self._client = OpenAI(
            api_key=self._config.openai_api_key,
            base_url=self._config.openai_base_url,
        )
        return self._client

    # ── 非流式 ──────────────────────────────────────────

    def chat(self, messages: list, tools: list, system: str,
             model: str, max_tokens: int, timeout: int = 120) -> dict:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        response = self._get_client().chat.completions.create(
            model=model,
            messages=api_messages,
            tools=tools,
            tool_choice="auto",
            timeout=timeout,
        )

        msg = response.choices[0].message
        text = msg.content or ""

        # DeepSeek reasoner: 保留 reasoning_content
        reasoning = getattr(msg, "reasoning_content", None) or ""

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        return self._validate_response({
            "text": text,
            "tool_calls": tool_calls,
            "stop_reason": "tool_calls" if tool_calls else "stop",
            "reasoning_content": reasoning,
        })

    # ── 流式 ────────────────────────────────────────────

    def chat_stream(self, messages: list, system: str, tools: list | None,
                    model: str, max_tokens: int):
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": model, "messages": api_messages,
            "stream": True, "timeout": 120,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = self._get_client().chat.completions.create(**kwargs)

        tool_calls_map: dict[int, dict] = {}
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            if delta.content:
                yield delta.content

            # DeepSeek reasoner thinking mode
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                yield {"type": "reasoning", "text": delta.reasoning_content}

            # tool_calls 片段收集
            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index if hasattr(tc_delta, 'index') else 0
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                    if hasattr(tc_delta, 'id') and tc_delta.id:
                        tool_calls_map[idx]["id"] = tc_delta.id
                    if hasattr(tc_delta, 'function') and tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_map[idx]["arguments"] += tc_delta.function.arguments

        if tool_calls_map:
            parsed_calls = []
            for idx in sorted(tool_calls_map.keys()):
                tc = tool_calls_map[idx]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                parsed_calls.append({
                    "id": tc["id"], "name": tc["name"], "arguments": args,
                })
            yield {"type": "tool_calls", "tool_calls": parsed_calls}


# ── 注册 ────────────────────────────────────────────────

ProviderRegistry.register("openai_compatible", OpenAIProvider)
