"""StrategyContext — 策略与引擎之间的显式契约。替代猴子补丁注入。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any


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
    emit: Callable[[str, dict], None] | None = None
    """发送事件给上层 (Web UI 等)。event_type: text|tool_call|tool_result|orient"""

    # ── 引擎能力 ──
    execute_tool: Callable | None = None
    """执行单个工具调用。由 Agent 提供统一实现。"""

    agent_loop: Callable | None = None
    """核心 O-O-D-A 循环。f(messages, exclude_tools=None) -> (text, messages)"""

    orient_fn: Callable | None = None
    """Orient 解读函数。已绑定原始任务。"""

    # ── 请求级覆盖 ──
    model_override: str | None = None
    """请求级模型覆盖（线程安全）"""

    # ── Prompt 构建 ──
    system_prompt_fn: Callable[[], str] | None = None
    """构建 system prompt 的函数（委托到 Agent._system_prompt）"""

    # ── 请求追踪 ──
    trace_id: str = ""
    """请求级唯一标识。贯穿 Agent → LLM → Tools 全链路。"""

    # ── 跨策略通信 ──
    pipeline_state: dict = field(default_factory=dict)
    """跨策略共享状态。Meta 策略初始化一次，所有子策略通过同一个
    StrategyContext 读写中间结果，避免重复探索。

    约定（按策略名命名，避免冲突）:
      pipeline_state["tot"]       — Tree-of-Thought: candidates, explored_paths
      pipeline_state["plan"]      — PlanExecute: steps, step_results
      pipeline_state["reflexion"] — Reflexion: lessons
      pipeline_state["meta"]      — Meta 策略自己的评估/选择记录

    示例:
      # ToT 写入候选方案
      ctx.pipeline_state["tot"] = {
          "candidates": [{"approach": "...", "score": 9}, ...],
          "best_index": 0,
      }

      # PlanExecute 读取
      tot = ctx.pipeline_state.get("tot", {})
      for c in tot.get("candidates", []):
          ...

    线程安全: 使用 ctx.pipeline_lock 保护对 pipeline_state 的读写。
    """
    pipeline_lock: threading.Lock = field(default_factory=threading.Lock)
    """保护 pipeline_state 的锁。并行工具执行或跨策略访问时使用。"""
