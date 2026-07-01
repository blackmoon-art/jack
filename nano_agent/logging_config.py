"""
统一日志配置。

用法:
  - 自动初始化：import nano_agent 时自动调用 setup_logging()
  - 环境变量控制:
      AGENT_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR   (默认 INFO)
      AGENT_LOG_FORMAT=text|json                  (默认 text, json=结构化)
      AGENT_LOG_FILE=/path/to/agent.log           (可选文件日志)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

_INITIALIZED = False

_DEFAULT_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_DEFAULT_DATEFMT = "%H:%M:%S"


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志格式器。"""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def setup_logging():
    """配置 nano_agent 全局日志。重复调用安全。"""
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    level_name = os.getenv("AGENT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = os.getenv("AGENT_LOG_FORMAT", "text").lower()
    log_file = os.getenv("AGENT_LOG_FILE", "")

    logger = logging.getLogger("nano_agent")
    logger.setLevel(level)

    # 格式器：JSON 或文本
    if log_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)

    # 控制台输出到 stderr（不干扰 CLI stdout 和 Web SSE）
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 可选：文件日志
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    # 防止日志向 root logger 重复传播
    logger.propagate = False
