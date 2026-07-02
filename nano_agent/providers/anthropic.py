"""Anthropic Provider — 封装 Anthropic SDK + 消息格式转换。"""

from __future__ import annotations

import json
import logging
from typing import Any

from .base import BaseProvider, ProviderRegistry

logger = logging.getLogger("nano_agent.providers.anthropic")


class AnthropicProvider(BaseProvider):
    """通过 Anthropic SDK 调用 Claude 模型。"""

    def __init__(self, config: Any):
        self._config = config
        self._client = None

    def _get_client(self):
        """懒加载 Anthropic 客户端。"""
        if self._client is not None:
            return self._client
        from anthropic import Anthropic

        kwargs: dict[str, Any] = {}
        if self._config.anthropic_api_key:
            kwargs["api_key"] = self._config.anthropic_api_key
        if self._config.anthropic_base_url:
            kwargs["base_url"] = self._config.anthropic_base_url
        self._client = Anthropic(**kwargs)
        return self._client

    @staticmethod
    def convert_messages(messages: list) -> list:
        """将内部消息格式转换为 Anthropic content blocks 格式。

        内部格式 (OpenAI 风格):
          {"role": "assistant", "content": "...", "tool_calls": [{...}]}
          {"role": "tool", "tool_call_id": "...", "content": "..."}

        Anthropic 格式:
          {"role": "assistant", "content": [{"type": "text", ...},
                                            {"type": "tool_use", ...}]}
          {"role": "user", "content": [{"type": "tool_result", ...}]}
        """
        converted = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")

            if role == "assistant" and tool_calls:
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args_str = fn.get("arguments", "{}")
                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except json.JSONDecodeError:
                        args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": name,
                        "input": args,
                    })
                converted.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": content,
                    }],
                })
            else:
                converted.append({"role": role, "content": content})
        return converted

    # ── 非流式 ──────────────────────────────────────────

    def chat(self, messages: list, tools: list, system: str,
             model: str, max_tokens: int, timeout: int = 120) -> dict:
        anthropic_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]
        anthropic_messages = self.convert_messages(messages)

        response = self._get_client().messages.create(
            model=model,
            system=system,
            messages=anthropic_messages,
            tools=anthropic_tools,
            max_tokens=max_tokens,
            timeout=timeout,
        )

        text = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })

        return self._validate_response({
            "text": text,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
        })

    # ── 流式 ────────────────────────────────────────────

    def chat_stream(self, messages: list, system: str, tools: list | None,
                    model: str, max_tokens: int):
        converted = self.convert_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        tool_use_blocks: list[dict] = []
        with self._get_client().messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield event.delta.text
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    tool_use_blocks.append({
                        "id": event.content_block.id,
                        "name": event.content_block.name,
                        "arguments": "",
                    })
                elif event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                    if tool_use_blocks:
                        tool_use_blocks[-1]["arguments"] += event.delta.partial_json

        if tool_use_blocks:
            parsed = []
            for tb in tool_use_blocks:
                try:
                    args = json.loads(tb["arguments"]) if tb["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                parsed.append({"id": tb["id"], "name": tb["name"], "arguments": args})
            yield {"type": "tool_calls", "tool_calls": parsed}


# ── 注册 ────────────────────────────────────────────────

ProviderRegistry.register("anthropic", AnthropicProvider)
