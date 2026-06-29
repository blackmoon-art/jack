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
from .context import StrategyContext

logger = logging.getLogger("nano_agent.strategies.base")


class BaseStrategy:
    """所有推理策略的基类。

    子类必须实现 run() 方法。
    通过 self.emit() 发送事件给上层（Web UI 等）。

    构造函数支持两种模式:
      1. 新式: BaseStrategy(context=StrategyContext(...)) — 显式契约
      2. 旧式: BaseStrategy(config, llm, tools, **kwargs) — 向后兼容
    """

    # ── 策略元数据（子类覆盖）────────────────────────
    uses_orient: bool = False
    """是否需要 Orient 解读阶段。Reflexion 等需要深度反思的策略设为 True。"""

    default_params: dict = {}
    """默认参数，Agent 从 Config 读取并合并。key → env var 的默认值。"""

    auto_keywords: tuple[str, ...] = ()
    """auto 模式的关键词匹配。Agent._auto_select_strategy 遍历所有策略按优先级匹配。"""

    auto_priority: int = 0
    """auto 模式匹配优先级。越大越优先。Default=0, ReAct=1, ToT=2, PlanExecute=3。"""

    def __init__(self, config: Config = None, llm: LLM = None,
                 tools: ToolRegistry = None, **kwargs):
        ctx = kwargs.pop("context", None)
        if ctx is not None:
            # 新式: 从 StrategyContext 提取
            self.config = ctx.config
            self.llm = ctx.llm
            self.tools = ctx.tools
            self.memory = ctx.memory
            self._emit = ctx.emit
            self._execute_tool = ctx.execute_tool  # 明确定义，IDE 可见
            self._agent_loop = ctx.agent_loop
            self._orient_fn = ctx.orient_fn
            self._model_override = ctx.model_override
            self._system_prompt_fn = ctx.system_prompt_fn  # Agent._system_prompt
        else:
            # 旧式: 向后兼容
            self.config = config
            self.llm = llm
            self.tools = tools
            self.memory = kwargs.get("memory")
            self._emit: Optional[Callable[[str, dict], None]] = None
            self._execute_tool = None  # 等待猴子补丁注入
            self._agent_loop = None
            self._orient_fn: Optional[Callable] = None
            self._model_override: Optional[str] = None
            self._system_prompt_fn: Optional[Callable[[], str]] = None

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
                      orient_fn: Optional[Callable] = None,
                      enable_orient: bool = True) -> dict:
        """委托给 Agent 的统一实现，确保行为一致。"""
        if self._execute_tool:
            return self._execute_tool(tool_call, messages, orient_fn=orient_fn,
                                     enable_orient=enable_orient)
        # 兜底：依赖注入未完成（直接 new 策略绕过 Agent 时触发）
        logger.warning("_execute_tool not injected — using fallback. "
                       "Create strategy via Agent.run() or pass StrategyContext.")
        from ..tools.observation import Observation
        name = tool_call["name"]
        args = tool_call.get("arguments", {})
        if isinstance(args, str):
            try: args = json.loads(args)
            except json.JSONDecodeError: args = {}
        logger.info(f"[Tool:fallback] {name}({json.dumps(args, ensure_ascii=False)[:200]})")
        self.emit("tool_call", {"name": name, "args": args})
        observation = self.tools.execute(name, args)
        result_text = str(observation)
        is_success = observation.success
        logger.debug(f"[Tool Result:fallback] {result_text[:200]}")
        self.emit("tool_result", {"name": name, "result": result_text, "success": is_success})
        _orient = orient_fn or self._orient_fn
        content = result_text
        if enable_orient and _orient:
            orientation = _orient(result_text)
            if orientation:
                self.emit("orient", orientation)
                content = f"{result_text}\n\n[Orient] interpretation={orientation.get('interpretation', '')[:200]}\n[Orient] implication={orientation.get('implication', '')[:200]}"
        messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": content})
        return {"name": name, "result": result_text, "success": is_success}

    # ── 并行工具执行公共方法 ──────────────────────────────

    def execute_tools_parallel(self, tool_calls: list[dict],
                                messages: list[dict],
                                tool_callback: Optional[Callable] = None) -> dict[int, dict]:
        """并行执行多个工具，追加结果到 messages，执行批量 Orient。

        共享逻辑：agent.py 的 _agent_loop 和 default.py 的 _execute_tools_parallel
        都调用此方法，避免代码重复。

        返回 {idx: info_dict}，info 含 name/result/content/success/_tool_msg。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[int, dict] = {}

        def _run_one(idx: int, tc: dict):
            try:
                tmp_msgs: list = []
                info = self.execute_tool(tc, tmp_msgs, enable_orient=False)
                if tmp_msgs:
                    info["_tool_msg"] = tmp_msgs[-1]
            except Exception as e:
                logger.warning(f"Parallel tool '{tc.get('name', '?')}' failed: {e}")
                info = {"name": tc.get("name", "?"),
                        "result": f"Error: {e}",
                        "content": f"Error: {e}",
                        "success": False}
            results[idx] = info

        with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
            futures = [executor.submit(_run_one, i, tc)
                       for i, tc in enumerate(tool_calls)]
            for f in as_completed(futures):
                f.result()

        # 按顺序合并 tool messages 到主 messages list
        for i in sorted(results.keys()):
            info = results[i]
            tc = tool_calls[i]
            if "_tool_msg" in info:
                messages.append(info["_tool_msg"])
            else:
                content = str(info.get("content") or info.get("result", ""))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": content,
                })
            if tool_callback:
                tool_callback(info["name"], tc.get("arguments", {}),
                              info["result"], info["success"])

        # 批量 Orient：合并所有工具结果做一次 Orient（1次 LLM vs N次）
        if self._orient_fn:
            combined = "\n---\n".join(
                f"[{results[i]['name']}] {results[i]['result'][:500]}"
                for i in sorted(results.keys())
            )
            enriched = self._orient_fn(combined)
            if enriched and enriched != combined:
                orient_part = enriched[len(combined):].strip() if enriched.startswith(combined) else enriched
                if orient_part and messages:
                    messages[-1]["content"] += f"\n\n{orient_part}"

        return results

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

        内部调用 self.llm.chat + self.llm.clean_json_response，
        保持与 mock 兼容（测试 mock 这两个方法）。
        """
        for attempt in range(max_retries + 1):
            response = self.llm.chat(messages=messages, tools=[], system="",
                                      model=self._model_override)
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
                    self.emit("text", {
                        "text": "LLM returned unparseable JSON, using fallback."
                    })
        return None
