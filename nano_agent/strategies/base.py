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

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry, **kwargs):
        self.config = config
        self.llm = llm
        self.tools = tools
        self.memory = kwargs.get("memory")  # 可选 Memory 实例
        self._emit: Optional[Callable[[str, dict], None]] = None
        self._orient_fn: Optional[Callable] = None  # 由 Agent 注入（已绑定原始任务）

    def emit(self, event_type: str, data: dict):
        """发送事件给回调（如果已设置）。静默失败。"""
        if self._emit:
            try:
                self._emit(event_type, data)
            except Exception as e:
                logger.warning(f"Strategy emit error ({event_type}): {e}")

    def build_messages(self, task: str, include_memory: bool = True,
                        include_long_term: bool = False) -> list[dict]:
        """构建消息列表，注入窗口记忆。长期记忆需显式开启。"""
        messages = []
        if include_memory and self.memory:
            for msg in self.memory.get_window_messages():
                messages.append(msg)
            # 长期记忆：默认关闭，避免旧对话干扰当前上下文
            # 仅 reflexion 等需要跨会话学习的策略开启
            if include_long_term:
                relevant = self.memory.load_relevant(task, top_k=3)
                if relevant:
                    messages.append({
                        "role": "user",
                        "content": f"[Context from past experience]\n{relevant}"
                    })
                    messages.append({"role": "assistant", "content": "Understood, I will consider this context."})
        messages.append({"role": "user", "content": task})
        return messages

    def execute_tool(self, tool_call: dict, messages: list[dict],
                      orient_fn: Optional[Callable] = None) -> dict:
        """执行单个工具调用，发送事件，追加消息。

        Args:
            tool_call:    {'name': str, 'arguments': dict, 'id': str}
            messages:     消息列表（会被修改）
            orient_fn:    Orient 函数 f(result_text) -> dict|None
                          不传则用 self._orient_fn（由 Agent 注入）

        Returns:
            {'name': str, 'result': str, 'success': bool}
        """
        from ..tools.observation import Observation

        name = tool_call["name"]
        args = tool_call.get("arguments", {})
        if isinstance(args, str):
            import json as _json
            try:
                args = _json.loads(args)
            except _json.JSONDecodeError:
                args = {}

        self.emit("tool_call", {"name": name, "args": args})
        logger.info(f"[Tool] {name}({json.dumps(args, ensure_ascii=False)[:200]})")

        observation = self.tools.execute(name, args)
        result_text = str(observation)
        is_success = observation.success  # execute() 保证返回 Observation

        self.emit("tool_result", {"name": name, "result": result_text, "success": is_success})
        logger.debug(f"[Tool Result] {result_text[:200]}")

        # Orient: 显式解读（使用注入的 orient_fn 或参数传入的）
        _orient = orient_fn or self._orient_fn
        content = result_text
        if _orient:
            orientation = _orient(result_text)
            if orientation:
                self.emit("orient", orientation)
                content = (
                    f"{result_text}\n\n"
                    f"[Orient] interpretation={orientation.get('interpretation', '')[:200]}\n"
                    f"[Orient] implication={orientation.get('implication', '')[:200]}"
                )

        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": content,
        })

        return {"name": name, "result": result_text, "success": is_success}

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
