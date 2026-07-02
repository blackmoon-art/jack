"""
Nano Agent Plus — 融合 nanoAgent 生产力工具 + demo_2 模块化设计
"""

from .logging_config import setup_logging, set_trace_id, get_trace_id

# 初始化全局日志（import 即生效）
setup_logging()

from .agent import Agent
from .config import Config
from .memory import Memory

__all__ = ["Agent", "Config", "Memory", "set_trace_id", "get_trace_id"]
