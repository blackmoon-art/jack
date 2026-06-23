"""
Default 策略 — 标准 agent loop（隐式 Orient）。

这是最基础的策略：O-O-D-A 循环直接委托给 Agent._agent_loop。
不做额外的推理控制、不生成 Thought 文本、不评分、不反思。
适合日常简单任务。
"""

import logging

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry
from .base import BaseStrategy

logger = logging.getLogger("nano_agent.strategies.default")


class DefaultStrategy(BaseStrategy):
    """默认推理策略 — 直接走 Agent 核心循环。"""

    def run(self, task: str, agent_loop_fn) -> str:
        """
        执行默认策略：构建初始消息，跑 agent loop。

        Args:
            task: 用户任务
            agent_loop_fn: 核心循环 f(messages, exclude_tools) -> (text, messages)
        """
        logger.info(f"[Default] Task: {task}")
        messages = self.build_messages(task, include_memory=True)
        result, _ = agent_loop_fn(messages)
        return result
