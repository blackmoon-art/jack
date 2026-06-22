"""
统一日志配置。

用法:
  - 自动初始化：import nano_agent 时自动调用 setup_logging()
  - 环境变量控制:
      AGENT_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR  (默认 INFO)
      AGENT_LOG_FILE=/path/to/agent.log          (可选文件日志)
"""

import logging
import os
import sys

_INITIALIZED = False

_DEFAULT_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_DEFAULT_DATEFMT = "%H:%M:%S"


def setup_logging():
    """配置 nano_agent 全局日志。重复调用安全。"""
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    level_name = os.getenv("AGENT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = os.getenv("AGENT_LOG_FILE", "")

    logger = logging.getLogger("nano_agent")
    logger.setLevel(level)

    # 控制台输出到 stderr（不干扰 CLI 的 stdout 用户输出和 Web 的 stdout）
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT))
    logger.addHandler(console)

    # 可选：文件日志
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT))
        logger.addHandler(fh)

    # 防止日志向 root logger 重复传播
    logger.propagate = False
