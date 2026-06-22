"""
策略基类 — 定义所有推理策略的统一接口契约。

agent_loop_fn 契约:
    f(messages: list[dict], exclude_tools: list[str] | None) -> (text: str, messages: list[dict])

    - messages:       传入的对话消息列表（会被修改并返回）
    - exclude_tools:  需要排除的工具名称列表（可选）
    - 返回值:          (最终文本回复, 完整消息列表)

事件回调契约:
    策略可通过 self.emit(event_type, data) 发送事件。
    event_type: "text" | "tool_call" | "tool_result" | "orient"
    Agent 会在策略返回后自行发送 "done" 事件。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry

logger = logging.getLogger("nano_agent.strategies.base")


class BaseStrategy:
    """所有推理策略的基类。

    子类必须实现 run() 方法。
    通过 self.emit() 发送事件给上层（Web UI 等）。
    """

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry):
        self.config = config
        self.llm = llm
        self.tools = tools
        self._emit: Optional[Callable[[str, dict], None]] = None

    def emit(self, event_type: str, data: dict):
        """发送事件给回调（如果已设置）。静默失败。"""
        if self._emit:
            try:
                self._emit(event_type, data)
            except Exception as e:
                logger.warning(f"Strategy emit error ({event_type}): {e}")

    def run(self, task: str, agent_loop_fn) -> str:
        """执行推理策略。子类必须实现。

        Args:
            task:          用户任务描述
            agent_loop_fn: 核心循环函数
                           f(messages, exclude_tools=None) -> (text, messages)

        Returns:
            最终回复文本
        """
        raise NotImplementedError

    def _chat_json(self, messages: list[dict], max_retries: int = 2) -> Optional[Any]:
        """调用 LLM 并解析 JSON 响应，失败自动重试。

        Args:
            messages:    发给 LLM 的消息列表
            max_retries: JSON 解析失败时的重试次数

        Returns:
            解析后的 Python 对象 (dict/list)，或 None（全部重试失败）
        """
        for attempt in range(max_retries + 1):
            response = self.llm.chat(messages=messages, tools=[], system="")
            text = self.llm.clean_json_response(response["text"])
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
