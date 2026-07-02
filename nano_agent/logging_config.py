"""
统一日志配置。

用法:
  - 自动初始化：import nano_agent 时自动调用 setup_logging()
  - 环境变量控制:
      AGENT_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR   (默认 INFO)
      AGENT_LOG_FORMAT=text|json                  (默认 text, json=结构化)
      AGENT_LOG_FILE=/path/to/agent.log           (可选文件日志)

trace_id:
  每个请求生成唯一 trace_id，通过 set_trace_id() 写入 thread-local，
  TraceFilter 自动注入到所有日志记录中。多请求并发时日志可按 trace_id 关联。
"""

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone

_INITIALIZED = False

_DEFAULT_FORMAT = "%(asctime)s [%(trace_id)s] [%(name)s] %(levelname)s: %(message)s"
_DEFAULT_DATEFMT = "%H:%M:%S"

# ── trace_id thread-local ───────────────────────────────

_trace_local = threading.local()


def set_trace_id(tid: str):
    """设置当前线程的 trace_id。在请求入口调用。"""
    _trace_local.trace_id = tid


def get_trace_id() -> str:
    """获取当前线程的 trace_id。未设置时返回 '-'。"""
    return getattr(_trace_local, 'trace_id', '-')


class TraceFilter(logging.Filter):
    """将 thread-local 中的 trace_id 注入到 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志格式器。"""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": getattr(record, 'trace_id', '-'),
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

    # trace_id filter — 自动注入到所有日志
    trace_filter = TraceFilter()

    # 格式器：JSON 或文本
    if log_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)

    # 控制台输出到 stderr（不干扰 CLI stdout 和 Web SSE）
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(trace_filter)
    logger.addHandler(console)

    # 可选：文件日志
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(formatter)
        fh.addFilter(trace_filter)
        logger.addHandler(fh)

    # 防止日志向 root logger 重复传播
    logger.propagate = False
