"""
工具模块 — 所有可调用工具的实现。

安全设计:
  - bash:  shell=False + shlex, 危险命令白名单模式
  - 文件:  工作目录沙箱 (realpath 校验)
  - 搜索:  使用 urllib + 合法 User-Agent, 保留 SSL 验证
  - 计算:  使用 ast 安全解析，禁用 eval

每工具返回 (result: str)。错误以 "Error: ..." 返回，让 LLM 自我纠正。
"""

import logging
from typing import Any, Optional

from .sandbox import PathSandbox
from .file_ops import FileOps
from .shell import Shell
from .search import Search
from .weather import Weather
from .stock import Stock

logger = logging.getLogger("nano_agent.tools")


class ToolRegistry:
    """管理所有工具的：执行函数 + OpenAI tool schema + 描述。"""

    def __init__(self, work_dir: str, bash_timeout: int = 120,
                 brave_api_key: str = ""):
        self.sandbox = PathSandbox(work_dir)
        self.bash_timeout = bash_timeout
        self.work_dir = work_dir
        self.brave_api_key = brave_api_key

        # 实例化子模块
        self._file_ops = FileOps(self.sandbox, work_dir)
        self._shell = Shell(work_dir, bash_timeout)
        self._search = Search(brave_api_key)
        self._weather = Weather()
        self._stock = Stock(work_dir)

        # 注册内部工具
        self._tools: dict[str, dict[str, Any]] = {}
        self._register("bash", "Run a shell command.", self._shell.bash, {
            "command": {"type": "string", "description": "The command to execute"},
        }, required=["command"])
        self._register("read", "Read a file with line numbers.", self._file_ops.read, {
            "path": {"type": "string", "description": "File path (relative to workspace)"},
            "offset": {"type": "integer", "description": "Start line (0-indexed)"},
            "limit": {"type": "integer", "description": "Max lines to read"},
        }, required=["path"])
        self._register("write", "Write content to a file.", self._file_ops.write, {
            "path": {"type": "string", "description": "File path (relative to workspace)"},
            "content": {"type": "string", "description": "Content to write"},
        }, required=["path", "content"])
        self._register("edit", "Replace a string in a file.", self._file_ops.edit, {
            "path": {"type": "string", "description": "File path (relative to workspace)"},
            "old_string": {"type": "string", "description": "Exact text to replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
        }, required=["path", "old_string", "new_string"])
        self._register("glob", "Find files by glob pattern.", self._file_ops.glob, {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')"},
        }, required=["pattern"])
        self._register("grep", "Search files for a regex pattern.", self._file_ops.grep, {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory or file to search (default: '.')"},
        }, required=["pattern"])
        self._register("web_search", "Search the web using DuckDuckGo.", self._search.web_search, {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default: 5)"},
        }, required=["query"])
        self._register("fetch_url", "Fetch and extract text content from a URL.", self._search.fetch_url, {
            "url": {"type": "string", "description": "URL to fetch"},
        }, required=["url"])
        self._register("search_and_fetch", "Search the web and auto-fetch top result content. Best for questions needing detailed answers.", self._search.search_and_fetch, {
            "query": {"type": "string", "description": "Search query"},
        }, required=["query"])
        self._register("calculate", "Evaluate a mathematical expression safely.", self._shell.calculate, {
            "expression": {"type": "string", "description": "Math expression (e.g. 1+2*3)"},
        }, required=["expression"])
        self._register("get_weather", "Get real-time weather for a city using Open-Meteo API (free, no key).", self._weather.get_weather, {
            "city": {"type": "string", "description": "City name in Chinese or English (e.g. 北京, Shanghai, Tokyo)"},
        }, required=["city"])
        self._register("stock_info", "Get real-time stock quote and news. Supports US stocks (AAPL), HK (0700.HK), China (600519.SS). Data from Yahoo Finance.", self._stock.stock_info, {
            "symbol": {"type": "string", "description": "Stock symbol (e.g. AAPL, TSLA, 0700.HK, 600519.SS)"},
        }, required=["symbol"])
        self._register("stock_history", "Get historical stock prices. Requires: pip install akshare yfinance. A-shares via akshare (600519), US/HK via yfinance (AAPL). Period: 1mo/3mo/6mo/1y/3y/5y.", self._stock.stock_history, {
            "symbol": {"type": "string", "description": "Stock symbol (e.g. 600519, AAPL, 0700.HK)"},
            "period": {"type": "string", "description": "Time range: 1mo, 3mo, 6mo, 1y, 3y, 5y (default: 1mo)"},
        }, required=["symbol"])
        self._register("stock_chart", "Generate stock price chart (PNG). Requires: pip install akshare yfinance matplotlib. Supports line and candle charts. Saves to charts/ directory.", self._stock.stock_chart, {
            "symbol": {"type": "string", "description": "Stock symbol (e.g. 600519, AAPL)"},
            "period": {"type": "string", "description": "Time range: 1mo, 3mo, 6mo, 1y (default: 3mo)"},
            "chart_type": {"type": "string", "description": "line or candle (default: line)"},
        }, required=["symbol"])

    # ── 内部注册 ──────────────────────────────────────

    def _register(self, name: str, desc: str, func, properties: dict,
                  required: Optional[list] = None):
        self._tools[name] = {
            "func": func,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required or [],
                    },
                },
            },
        }

    # ── 公开方法 ──────────────────────────────────────

    def get_schemas(self) -> list:
        """返回所有工具的 OpenAI tool schema 列表。"""
        return [t["schema"] for t in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> str:
        """执行指定工具，返回结果字符串。"""
        if name not in self._tools:
            return f"Error: Unknown tool '{name}'"
        try:
            return self._tools[name]["func"](**arguments)
        except TypeError as e:
            return f"Error: Invalid arguments — {e}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    # ── 向后兼容委托（旧测试直接调 registry.write_file() 等）──

    _DELEGATES = {
        "bash": "_shell",
        "read_file": ("_file_ops", "read"),
        "write_file": ("_file_ops", "write"),
        "edit_file": ("_file_ops", "edit"),
        "glob_files": ("_file_ops", "glob"),
        "grep_files": ("_file_ops", "grep"),
        "web_search": "_search",
        "fetch_url": "_search",
        "search_and_fetch": "_search",
        "calculate": "_shell",
        "get_weather": "_weather",
        "stock_info": "_stock",
        "stock_history": "_stock",
        "stock_chart": "_stock",
        "_search_brave": ("_search", "_search_brave"),
        "_search_duckduckgo": ("_search", "_search_duckduckgo"),
        "_search_bing": ("_search", "_search_bing"),
        "_search_searxng": ("_search", "_search_searxng"),
        "_search_wikipedia": ("_search", "_search_wikipedia"),
        "_clean_html": ("_search", "_clean_html"),
        "_parse_stock_symbol": ("_stock", "_parse_stock_symbol"),
        "_format_history": ("_stock", "_format_history"),
    }

    def __getattr__(self, name: str):
        """委托旧方法名到子模块。"""
        delegate = self._DELEGATES.get(name)
        if delegate is None:
            raise AttributeError(f"'ToolRegistry' object has no attribute '{name}'")
        if isinstance(delegate, tuple):
            mod_name, method_name = delegate
            return getattr(getattr(self, mod_name), method_name)
        else:
            mod_name = delegate
            return getattr(getattr(self, mod_name), name)
