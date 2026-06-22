"""
LLM 客户端抽象 — 统一 Anthropic / OpenAI / DeepSeek / OpenRouter 接口。
"""

import time
from typing import Any

from .config import Config


def _create_anthropic_client(cfg: Config):
    from anthropic import Anthropic

    kwargs: dict[str, Any] = {}
    if cfg.anthropic_api_key:
        kwargs["api_key"] = cfg.anthropic_api_key
    if cfg.anthropic_base_url:
        kwargs["base_url"] = cfg.anthropic_base_url
    return Anthropic(**kwargs)


def _create_openai_client(cfg: Config):
    from openai import OpenAI

    return OpenAI(api_key=cfg.openai_api_key, base_url=cfg.openai_base_url)


class LLM:
    """统一 LLM 调用接口，内置重试逻辑。客户端懒加载，便于测试 mock。"""

    def __init__(self, config: Config):
        self.config = config
        self._client = None  # 懒初始化
        self._model_override: str | None = None  # 运行时模型覆盖

    def set_model(self, model: str):
        """运行时覆盖模型名称。用于 Web UI 模型切换。"""
        self._model_override = model
        # 切换 provider 如果需要
        if model.startswith("claude"):
            self.config.provider = "anthropic"
            self._client = None  # 重新创建客户端
        elif any(model.startswith(p) for p in ("deepseek", "qwen", "glm", "moonshot")):
            self.config.provider = "deepseek"
            self._client = None
        else:
            # openrouter / openai 兼容
            self.config.provider = "openai"
            self._client = None

    @property
    def _model(self) -> str:
        return self._model_override or self.config.model

    def _get_client(self):
        if self._client is None:
            if self.config.is_anthropic:
                self._client = _create_anthropic_client(self.config)
            else:
                self._client = _create_openai_client(self.config)
        return self._client

    # ── 公开 API ──────────────────────────────────────────

    @staticmethod
    def clean_json_response(text: str) -> str:
        """清理 LLM 返回的 JSON 文本：去 markdown 代码块包裹。"""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        return text.strip()

    @staticmethod
    def format_tool_call_for_message(tc: dict) -> dict:
        """
        将内部 tool_call 格式转为 OpenAI 兼容的 message 格式。

        内部: {"id": str, "name": str, "arguments": dict}
        输出: {"id": str, "type": "function", "function": {"name": str, "arguments": str}}
        """
        import json as _json
        args = tc.get("arguments", {})
        if isinstance(args, dict):
            args = _json.dumps(args, ensure_ascii=False)
        return {
            "id": tc.get("id", ""),
            "type": "function",
            "function": {
                "name": tc.get("name", ""),
                "arguments": args if isinstance(args, str) else str(args),
            },
        }

    def chat(self, messages: list, tools: list, system: str = "") -> dict:
        """
        调用 LLM，返回统一格式:
          {
            "text": str,            # 模型文本输出
            "tool_calls": [         # 工具调用列表 (可能为空)
              {"id": str, "name": str, "arguments": dict}
            ],
            "stop_reason": str,     # "end_turn" | "tool_use" | "max_tokens"
          }
        """
        for attempt in range(3):
            try:
                if self.config.is_anthropic:
                    return self._chat_anthropic(messages, tools, system)
                else:
                    return self._chat_openai(messages, tools, system)
            except Exception as e:
                # 只重试可恢复错误：429 限速、5xx 服务端错误、网络超时
                retryable = False
                err_str = str(e).lower()
                status_code = getattr(e, 'status_code', None) or getattr(getattr(e, 'response', None), 'status_code', None)
                if status_code in (429, 500, 502, 503, 504):
                    retryable = True
                elif any(kw in err_str for kw in ('rate_limit', 'timeout', 'connection', 'overloaded', 'server error')):
                    retryable = True

                if not retryable or attempt == 2:
                    raise
                wait = 2 ** attempt
                logger.warning(f"LLM retryable error (attempt {attempt+1}/3): {e}, waiting {wait}s")
                time.sleep(wait)

        raise RuntimeError("unreachable")

    # ── Anthropic 实现 ────────────────────────────────────

    def _convert_messages_for_anthropic(self, messages: list) -> list:
        """
        将内部消息格式转换为 Anthropic 期望的 content blocks 格式。

        内部格式 (OpenAI 风格):
          {"role": "assistant", "content": "...", "tool_calls": [{...}]}
          {"role": "tool", "tool_call_id": "...", "content": "..."}

        Anthropic 格式:
          {"role": "assistant", "content": [{"type": "text", ...}, {"type": "tool_use", ...}]}
          {"role": "user", "content": [{"type": "tool_result", ...}]}
        """
        converted = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")

            if role == "assistant" and tool_calls:
                # 有 tool_calls 的 assistant → content blocks
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args_str = fn.get("arguments", "{}")
                    try:
                        import json as _json
                        args = _json.loads(args_str) if isinstance(args_str, str) else args_str
                    except _json.JSONDecodeError:
                        args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": name,
                        "input": args,
                    })
                converted.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                # tool 结果 → user message with tool_result block
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": content,
                    }],
                })
            else:
                # 普通 user/system/assistant 消息，保持不变
                converted.append({"role": role, "content": content})
        return converted

    def _chat_anthropic(self, messages: list, tools: list, system: str) -> dict:
        # 转换 tools 格式到 Anthropic schema
        anthropic_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]

        # 转换消息格式：内部格式 → Anthropic content blocks
        anthropic_messages = self._convert_messages_for_anthropic(messages)

        response = self._get_client().messages.create(
            model=self._model,
            system=system,
            messages=anthropic_messages,
            tools=anthropic_tools,
            max_tokens=self.config.max_tokens,
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

        return {
            "text": text,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
        }

    # ── OpenAI 兼容实现 ───────────────────────────────────

    def _chat_openai(self, messages: list, tools: list, system: str) -> dict:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        response = self._get_client().chat.completions.create(
            model=self._model,
            messages=api_messages,
            tools=tools,
            tool_choice="auto",
        )

        msg = response.choices[0].message
        text = msg.content or ""

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                import json as _json

                try:
                    args = _json.loads(tc.function.arguments)
                except _json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        return {
            "text": text,
            "tool_calls": tool_calls,
            "stop_reason": "tool_calls" if tool_calls else "stop",
        }
