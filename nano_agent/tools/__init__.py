"""
工具模块 — 所有可调用工具的实现。

安全设计:
  - bash:  shell=False + shlex, 危险命令白名单模式
  - 文件:  工作目录沙箱 (realpath 校验)
  - 搜索:  使用 urllib + 合法 User-Agent, 保留 SSL 验证
  - 计算:  使用 ast 安全解析，禁用 eval

每工具返回 Observation 对象或字符串。execute() 统一包装为 Observation。
"""

import logging
from typing import Any, Optional

from .observation import Observation
from .sandbox import PathSandbox
from .file_ops import FileOps
from .shell import Shell
from .search import Search
from .fetch import Fetch
from .weather import Weather
from .stock_quote import StockQuote
from .stock_chart import StockChart
from .stock_market import StockMarket
from .ppt import PPT
from .chart import Chart
from .diagram import Diagram
from .ai_image import AIImage

logger = logging.getLogger("nano_agent.tools")


class ToolRegistry:
    """管理所有工具的：执行函数 + OpenAI tool schema + 描述。

    工具类通过 TOOLS 类属性声明自己提供的工具。__init__ 自动遍历注册。
    新增工具只需：1) 在工具类加 TOOLS 列表 2) 实例化子模块 3) 加到 _MODULE_MAP
    """

    # 子模块实例名 → 类的映射
    _MODULE_MAP = {
        "_shell": Shell,
        "_file_ops": FileOps,
        "_search": Search,
        "_fetch": Fetch,
        "_weather": Weather,
        "_stock_quote": StockQuote,
        "_stock_chart": StockChart,
        "_stock_market": StockMarket,
        "_chart": Chart,
        "_diagram": Diagram,
        "_ai_image": AIImage,
        "_ppt": PPT,
    }

    def __init__(self, work_dir: str, bash_timeout: int = 120,
                 brave_api_key: str = "", charts_dir: str = ""):
        self.sandbox = PathSandbox(work_dir)
        self.bash_timeout = bash_timeout
        self.work_dir = work_dir
        self.brave_api_key = brave_api_key

        # 实例化子模块
        self._file_ops = FileOps(self.sandbox, work_dir)
        self._shell = Shell(work_dir, bash_timeout)
        self._search = Search(brave_api_key=brave_api_key)
        self._fetch = Fetch()
        self._weather = Weather()
        self._stock_quote = StockQuote(work_dir, charts_dir=charts_dir)
        self._stock_chart = StockChart(work_dir, charts_dir=charts_dir)
        self._stock_market = StockMarket(work_dir, charts_dir=charts_dir)
        self._ppt = PPT(work_dir)
        self._chart = Chart(work_dir, charts_dir=charts_dir)
        self._diagram = Diagram(work_dir, charts_dir=charts_dir)
        self._ai_image = AIImage(work_dir, charts_dir=charts_dir)

        # 自动注册工具
        self._tools: dict[str, dict[str, Any]] = {}
        self._auto_register()

        # PPT + search_and_fetch 有复杂/跨模块 schema，手动补充
        self._register_manual_overrides()

    def _auto_register(self):
        """从各工具类的 TOOLS 属性自动注册。"""
        for attr_name, cls in self._MODULE_MAP.items():
            instance = getattr(self, attr_name, None)
            if instance is None:
                continue
            tools_def = getattr(cls, 'TOOLS', [])
            for name, desc, method_name, properties, required in tools_def:
                func = getattr(instance, method_name)
                self._register(name, desc, func, properties, required=required)

    def _register_manual_overrides(self):
        """手动注册复合/跨模块工具（无法通过 TOOLS 属性表达的）。"""
        # search_and_fetch — 复合工具
        self._register("search_and_fetch",
            "Search the web and auto-fetch top result content. Best for questions needing detailed answers.",
            lambda query: self._search.search_and_fetch(self._fetch.fetch_url, query),
            {"query": {"type": "string", "description": "Search query"}},
            required=["query"])

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

    def execute(self, name: str, arguments: dict) -> Observation:
        """执行指定工具，返回结构化 Observation。"""
        if name not in self._tools:
            return Observation(tool_name=name, result=f"Error: Unknown tool '{name}'",
                               success=False, args=arguments)
        try:
            result = self._tools[name]["func"](**arguments)
            # 如果工具已经返回 Observation，直接用；否则包装
            if isinstance(result, Observation):
                return result
            is_success = not str(result).startswith("Error:")
            return Observation(tool_name=name, result=str(result),
                               success=is_success, args=arguments)
        except TypeError as e:
            return Observation(tool_name=name, result=f"Error: Invalid arguments — {e}",
                               success=False, args=arguments)
        except PermissionError as e:
            return Observation(tool_name=name, result=f"Error: {e}",
                               success=False, args=arguments)
        except Exception as e:
            return Observation(tool_name=name, result=f"Error: {type(e).__name__}: {e}",
                               success=False, args=arguments)

    # ── 测试兼容委托（旧测试直接调 registry.write_file() 等）──
    # TODO: 迁移测试到用 execute() 后可删除

    _DELEGATES = {
        "bash": "_shell",
        "read_file": ("_file_ops", "read"),
        "write_file": ("_file_ops", "write"),
        "edit_file": ("_file_ops", "edit"),
        "glob_files": ("_file_ops", "glob"),
        "grep_files": ("_file_ops", "grep"),
        "web_search": "_search",
        "fetch_url": "_fetch",
        "search_and_fetch": "_search",
        "calculate": "_shell",
        "get_weather": "_weather",
        "stock_info": "_stock_quote",
        "stock_history": "_stock_quote",
        "stock_chart": "_stock_chart",
        "stock_indicators": "_stock_quote",
        "stock_market": "_stock_market",
        "stock_market_us": "_stock_market",
        "create_ppt": "_ppt",
        "mermaid_chart": "_diagram",
        "drawio_diagram": "_diagram",
        "ai_image": "_ai_image",
        "generate_chart": "_chart",
        "_search_brave": ("_search", "_search_brave"),
        "_search_duckduckgo": ("_search", "_search_duckduckgo"),
        "_search_bing": ("_search", "_search_bing"),
        "_search_searxng": ("_search", "_search_searxng"),
        "_search_wikipedia": ("_search", "_search_wikipedia"),
        "_clean_html": ("_search", "_clean_html"),
        "_parse_stock_symbol": ("_stock_quote", "_parse_stock_symbol"),
        "_format_history": ("_stock_quote", "_format_history"),
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
