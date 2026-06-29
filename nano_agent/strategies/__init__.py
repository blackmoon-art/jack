"""
推理策略模块：ReAct, Plan-Execute, Reflexion, Tree-of-Thought。

每种策略都是对 Agent 核心循环的不同控制流包装。
策略注册表替代 if-else 分发，新增策略只需在注册表中添加一行。
"""

from .base import BaseStrategy
from .default import DefaultStrategy
from .plan_execute import PlanExecuteStrategy
from .react import ReActStrategy
from .reflexion import ReflexionStrategy
from .tree_of_thought import TreeOfThoughtStrategy
from .meta import MetaStrategy

# ── 策略注册表 ────────────────────────────────────────
# key = 策略名称, value = 策略类
# 新增策略只需: 1) 实现策略类  2) 在此注册
STRATEGY_REGISTRY: dict[str, type] = {
    "default": DefaultStrategy,
    "react": ReActStrategy,
    "plan-execute": PlanExecuteStrategy,
    "reflexion": ReflexionStrategy,
    "tree-of-thought": TreeOfThoughtStrategy,
    "meta": MetaStrategy,
}

__all__ = [
    "BaseStrategy",
    "DefaultStrategy", "ReActStrategy", "PlanExecuteStrategy",
    "ReflexionStrategy", "TreeOfThoughtStrategy",
    "STRATEGY_REGISTRY",
]
