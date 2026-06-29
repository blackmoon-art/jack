"""StrategyContext — 策略与引擎之间的显式契约。替代猴子补丁注入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Optional


@dataclass
class StrategyContext:
    """策略执行所需的全部上下文。由 Agent 创建，传递给策略构造函数。

    策略通过 context 访问引擎能力，不再依赖运行时 setattr 注入。
    """

    # ── 基础设施 ──
    config: Any                    # Config 实例
    llm: Any                       # LLM 实例
    tools: Any                     # ToolRegistry 实例
    memory: Any = None             # Memory 实例（可选）

    # ── 事件回调 ──
    emit: Optional[Callable[[str, dict], None]] = None
    """发送事件给上层 (Web UI 等)。event_type: text|tool_call|tool_result|orient"""

    # ── 引擎能力 ──
    execute_tool: Optional[Callable] = None
    """执行单个工具调用。由 Agent 提供统一实现。"""

    agent_loop: Optional[Callable] = None
    """核心 O-O-D-A 循环。f(messages, exclude_tools=None) -> (text, messages)"""

    orient_fn: Optional[Callable] = None
    """Orient 解读函数。已绑定原始任务。"""

    # ── 请求级覆盖 ──
    model_override: Optional[str] = None
    """请求级模型覆盖（线程安全）"""

    # ── Prompt 构建 ──
    system_prompt_fn: Optional[Callable[[], str]] = None
    """构建 system prompt 的函数（委托到 Agent._system_prompt）"""
