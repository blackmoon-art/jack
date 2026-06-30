"""
LLM 客户端抽象 — 统一 Anthropic / OpenAI / DeepSeek / OpenRouter 接口。
"""

import json
import logging
import time
from typing import Any

from .config import Config

logger = logging.getLogger("nano_agent.llm")


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


# ── 工具函数（模块级，不依赖 LLM 实例）──────────────────

def clean_json_response(text: str) -> str:
    """清理 LLM 返回的 JSON 文本：去 markdown 代码块包裹。"""
    text = text.strip()
    if '```' in text:
        start = text.find('```')
        if start >= 0:
            after_start = text.index('```', start) + 3
            newline_pos = text.find('\n', after_start)
            if newline_pos >= 0 and newline_pos - after_start < 20:
                after_start = newline_pos + 1
            end = text.rfind('```')
            if end > after_start:
                text = text[after_start:end]
    return text.strip()


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
    """统一 LLM 调用接口，内置重试逻辑。客户端懒加载，便于测试 mock。"""

    def __init__(self, config: Config):
        self.config = config
        self._client = None  # 懒初始化
        self._model_override: str | None = None  # 运行时模型覆盖
        self._provider_override: str | None = None  # 运行时 provider 覆盖（不修改共享 Config）

    # 模型名前缀 → provider 映射（不修改共享 Config）
    _MODEL_PROVIDER_MAP = {
        "claude": "anthropic",
        "deepseek": "openai_compatible",
        "qwen": "openai_compatible",
        "glm": "openai_compatible",
        "moonshot": "openai_compatible",
    }

    def set_model(self, model: str):
        """运行时覆盖模型名称。用于 Web UI 模型切换。

        不修改共享的 Config 对象，而是用实例级 _provider_override
        避免影响其他 session。
        """
        self._model_override = model
        self._provider_override = None
        self._client = None  # 重新创建客户端

        for prefix, provider in self._MODEL_PROVIDER_MAP.items():
            if model.startswith(prefix):
                self._provider_override = provider
                break
        else:
            self._provider_override = "openai_compatible"

    @property
    def _provider(self) -> str:
        """当前生效的 provider（优先使用实例级覆盖，不修改共享 Config）。"""
        if self._provider_override is not None:
            return self._provider_override
        return self.config.provider

    @property
    def _model(self) -> str:
        return self._model_override or self.config.model

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

    def _get_client(self):
        if self._client is None:
            if self._provider == "anthropic":
                self._client = _create_anthropic_client(self.config)
            else:
                self._client = _create_openai_client(self.config)
        return self._client

    # ── 向后兼容：类方法委托到模块级工具函数 ─────────────

    @staticmethod
    def clean_json_response(text: str) -> str:
        """委托到模块级 clean_json_response。"""
        return clean_json_response(text)

    @staticmethod
    def format_tool_call_for_message(tc: dict) -> dict:
        """委托到模块级 format_tool_call_for_message。"""
        return format_tool_call_for_message(tc)

    def chat(self, messages: list, tools: list, system: str = "",
             model: str | None = None) -> dict:
        """
        调用 LLM，返回统一格式。

        Args:
            model: 单次调用的模型覆盖（不修改实例状态，线程安全）
        """
        for attempt in range(3):
            try:
                if self._provider == "anthropic":
                    return self._chat_anthropic(messages, tools, system, model=model)
                else:
                    return self._chat_openai(messages, tools, system, model=model)
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
                # 优先用 Retry-After header，否则指数退避
                wait = 2 ** attempt
                retry_after = getattr(getattr(e, 'response', None), 'headers', {}).get('Retry-After')
                if retry_after:
                    try:
                        wait = min(int(retry_after), 30)  # 最多等 30s
                    except (ValueError, TypeError):
                        pass
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

    def _chat_anthropic(self, messages: list, tools: list, system: str,
                        model: str | None = None) -> dict:
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
            model=model or self._model,
            system=system,
            messages=anthropic_messages,
            tools=anthropic_tools,
            max_tokens=self.config.max_tokens,
            timeout=120,
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

    def _chat_openai(self, messages: list, tools: list, system: str,
                      model: str | None = None) -> dict:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        response = self._get_client().chat.completions.create(
            model=model or self._model,
            messages=api_messages,
            tools=tools,
            tool_choice="auto",
            timeout=120,
        )

        msg = response.choices[0].message
        text = msg.content or ""

        # DeepSeek reasoner: 必须保留 reasoning_content 并在下一轮传回
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

        return {
            "text": text,
            "tool_calls": tool_calls,
            "stop_reason": "tool_calls" if tool_calls else "stop",
            "reasoning_content": reasoning,
        }

    def chat_stream(self, messages: list, system: str = "", tools: list = None,
                    model: str | None = None):
        """流式调用 LLM，yield 文本片段或 tool_calls 信号。

        如果 LLM 要调工具，会 yield {"type": "tool_calls", "tool_calls": [...]},
        调用方应中断流式处理。

        Args:
            model: 单次调用的模型覆盖（不修改实例状态，线程安全）

        Yields:
            str: 文本 chunk，或 dict: {"type": "tool_calls", "tool_calls": [...]}
        """
        if self._provider == "anthropic":
            yield from self._chat_stream_anthropic(messages, system, tools, model=model)
        else:
            yield from self._chat_stream_openai(messages, system, tools, model=model)

    def _chat_stream_openai(self, messages: list, system: str = "", tools: list = None,
                            model: str | None = None):
        """OpenAI 兼容 API 流式调用。支持 tools 参数，检测 tool_calls。"""
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        kwargs = {"model": model or self._model, "messages": api_messages, "stream": True, "timeout": 120}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = self._get_client().chat.completions.create(**kwargs)

        # 检测 tool_calls：流式中如果 LLM 要调工具，收集完整后 yield 信号
        tool_calls_map = {}  # id -> {id, name, arguments_str}
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            # 文本内容
            if delta.content:
                yield delta.content

            # tool_calls 片段
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

        # 如果有 tool_calls，yield 信号
        if tool_calls_map:
            parsed_calls = []
            for idx in sorted(tool_calls_map.keys()):
                tc = tool_calls_map[idx]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                parsed_calls.append({"id": tc["id"], "name": tc["name"], "arguments": args})
            yield {"type": "tool_calls", "tool_calls": parsed_calls}

    def _chat_stream_anthropic(self, messages: list, system: str = "", tools: list = None,
                               model: str | None = None):
        """Anthropic API 流式调用。支持 tools 参数。"""
        converted = self._convert_messages_for_anthropic(messages)
        kwargs = {
            "model": model or self._model,
            "messages": converted,
            "max_tokens": self.config.max_tokens,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        tool_use_blocks = []
        with self._get_client().messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield event.delta.text
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    tool_use_blocks.append({"id": event.content_block.id, "name": event.content_block.name, "arguments": ""})
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
