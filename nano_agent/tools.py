"""
工具模块 — 所有可调用工具的实现。

安全设计:
  - bash:  shell=False + shlex, 危险命令白名单模式
  - 文件:  工作目录沙箱 (realpath 校验)
  - 搜索:  使用 urllib + 合法 User-Agent, 保留 SSL 验证
  - 计算:  使用 ast 安全解析，禁用 eval

每工具返回 (result: str)。错误以 "Error: ..." 返回，让 LLM 自我纠正。
"""

import glob as _glob
import json
import os
import re
import shlex
import ssl
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

# ── 危险命令白名单（只允许这些前缀的命令通过）────────────
_SAFE_COMMAND_PREFIXES = [
    "ls", "cat", "head", "tail", "wc", "find", "grep", "git",
    "pwd", "echo", "date", "whoami", "uname", "env", "printenv",
    "python", "python3", "node", "npm", "npx", "tsc", "cargo", "go", "sleep",
    "mkdir", "touch", "cp", "mv", "rm", "chmod", "chown",
    "curl", "wget", "diff", "sort", "uniq", "cut", "sed", "awk", "tr",
    "which", "command", "type", "file", "stat", "du", "df",
    "pip", "pip3", "poetry", "uv", "cargo",
    # 编辑器（无交互模式）
    "nano", "code",
]
_SAFE_COMMAND_PREFIXES_TUPLE = tuple(_SAFE_COMMAND_PREFIXES)


class PathSandbox:
    """文件操作沙箱：限制读写在工作目录内。"""

    def __init__(self, root: str):
        self._root = Path(root).resolve()

    def safe_path(self, user_path: str) -> Path:
        """将用户给的路径解析为沙箱内的绝对路径，越界则拒绝。"""
        p = (self._root / user_path).resolve()
        try:
            p.relative_to(self._root)
        except ValueError:
            raise PermissionError(f"Access denied: '{user_path}' is outside workspace")
        return p


# ── 工具注册表 = 函数 + schema ──────────────────────────

class ToolRegistry:
    """管理所有工具的：执行函数 + OpenAI tool schema + 描述。"""

    def __init__(self, work_dir: str, bash_timeout: int = 120):
        self.sandbox = PathSandbox(work_dir)
        self.bash_timeout = bash_timeout
        self.work_dir = work_dir

        # 注册内部工具
        self._tools: dict[str, dict[str, Any]] = {}
        self._register("bash", "Run a shell command.", self.bash, {
            "command": {"type": "string", "description": "The command to execute"},
        }, required=["command"])
        self._register("read", "Read a file with line numbers.", self.read_file, {
            "path": {"type": "string", "description": "File path (relative to workspace)"},
            "offset": {"type": "integer", "description": "Start line (0-indexed)"},
            "limit": {"type": "integer", "description": "Max lines to read"},
        }, required=["path"])
        self._register("write", "Write content to a file.", self.write_file, {
            "path": {"type": "string", "description": "File path (relative to workspace)"},
            "content": {"type": "string", "description": "Content to write"},
        }, required=["path", "content"])
        self._register("edit", "Replace a string in a file.", self.edit_file, {
            "path": {"type": "string", "description": "File path (relative to workspace)"},
            "old_string": {"type": "string", "description": "Exact text to replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
        }, required=["path", "old_string", "new_string"])
        self._register("glob", "Find files by glob pattern.", self.glob_files, {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')"},
        }, required=["pattern"])
        self._register("grep", "Search files for a regex pattern.", self.grep_files, {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory or file to search (default: '.')"},
        }, required=["pattern"])
        self._register("web_search", "Search the web.", self.web_search, {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default: 5)"},
        }, required=["query"])
        self._register("plan", "Break a complex task into steps and execute sequentially.", self.plan, {
            "task": {"type": "string", "description": "The task to plan"},
        }, required=["task"])
        self._register("calculate", "Evaluate a mathematical expression safely.", self.calculate, {
            "expression": {"type": "string", "description": "Math expression (e.g. 1+2*3)"},
        }, required=["expression"])

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

    # ── 工具实现 ──────────────────────────────────────

    def bash(self, command: str) -> str:
        """安全执行 bash 命令：shell=False + shlex + 白名单前缀。"""
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return f"Error: Invalid shell syntax — {e}"
        if not parts:
            return "Error: Empty command"

        cmd_name = os.path.basename(parts[0])
        if not cmd_name.startswith(_SAFE_COMMAND_PREFIXES_TUPLE):
            return (
                f"Error: Command '{parts[0]}' is not in the allowed list. "
                f"Allowed prefixes: {', '.join(_SAFE_COMMAND_PREFIXES[:15])}..."
            )

        try:
            r = subprocess.run(
                parts,
                shell=False,
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                timeout=self.bash_timeout,
            )
            out = (r.stdout + r.stderr).strip()
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: Timeout ({self.bash_timeout}s)"
        except FileNotFoundError:
            return f"Error: Command not found: {parts[0]}"
        except OSError as e:
            return f"Error: {e}"

    def read_file(self, path: str, offset: int = 0, limit: int = 500) -> str:
        """读取工作目录内的文件，带行号。"""
        safe = self.sandbox.safe_path(path)
        if not safe.is_file():
            return f"Error: Not a file: {path}"
        try:
            lines = safe.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return f"Error: File is not UTF-8 text: {path}"
        except OSError as e:
            return f"Error: {e}"

        start = max(offset, 0)
        end = start + limit if limit else len(lines)
        numbered = [f"{i+1:4d} {line}" for i, line in enumerate(lines[start:end], start)]
        return "\n".join(numbered)

    def write_file(self, path: str, content: str) -> str:
        """写入文件到工作目录内。"""
        safe = self.sandbox.safe_path(path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        try:
            safe.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {path}"
        except OSError as e:
            return f"Error: {e}"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        """精确替换文件中的字符串（仅替换一次，要求唯一匹配）。"""
        safe = self.sandbox.safe_path(path)
        try:
            content = safe.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"Error: File not found: {path}"
        except OSError as e:
            return f"Error: {e}"

        count = content.count(old_string)
        if count == 0:
            return "Error: old_string not found in file"
        if count > 1:
            return f"Error: old_string appears {count} times (must be unique)"

        new_content = content.replace(old_string, new_string, 1)
        safe.write_text(new_content, encoding="utf-8")
        return f"Edited {path}: replaced 1 occurrence"

    def glob_files(self, pattern: str) -> str:
        """在工作目录内查找匹配的文件。"""
        full_pattern = str(Path(self.work_dir) / pattern)
        try:
            files = _glob.glob(full_pattern, recursive=True)
            # 转回相对路径
            rel_files = [str(Path(f).relative_to(self.work_dir)) for f in sorted(files)]
            return "\n".join(rel_files) if rel_files else "No files found"
        except Exception as e:
            return f"Error: {e}"

    def grep_files(self, pattern: str, path: str = ".") -> str:
        """在工作目录内搜索正则表达式。"""
        safe = self.sandbox.safe_path(path)
        try:
            result = subprocess.run(
                ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
                 "--include=*.md", "--include=*.json", "--include=*.html", "--include=*.css",
                 pattern, str(safe)],
                capture_output=True, text=True, timeout=30,
            )
            out = result.stdout.strip()
            return out[:30000] if out else "No matches found"
        except subprocess.TimeoutExpired:
            return "Error: grep timed out"
        except Exception as e:
            return f"Error: {e}"

    def web_search(self, query: str, max_results: int = 5) -> str:
        """使用 DuckDuckGo HTML 搜索（无 API key 需要）。"""
        max_results = min(max(max_results, 1), 10)
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.URLError as e:
            return f"Error: Network error — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        results = []
        # 解析 DuckDuckGo HTML 结果
        result_blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        )
        seen: set[str] = set()
        for href, title in result_blocks:
            if len(results) >= max_results:
                break
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            if not title_clean or len(title_clean) < 3:
                continue
            # DuckDuckGo 重定向链接 → 真实 URL
            if "uddg=" in href:
                m = re.search(r'uddg=([^&]+)', href)
                if m:
                    href = urllib.parse.unquote(m.group(1))
            if href in seen:
                continue
            seen.add(href)
            results.append(f"{len(results)+1}. {title_clean}\n   {href}")

        if not results:
            return "No search results found."
        return f"Search results for '{query}':\n\n" + "\n\n".join(results)

    def plan(self, task: str) -> str:
        """
        占位工具 — plan 的逻辑在 Agent 层实现。
        这里返回一个标记让 Agent.run 中的 plan handler 接管。
        """
        return f"__PLAN_TRIGGER__:{task}"

    def calculate(self, expression: str) -> str:
        """安全计算数学表达式（使用 ast 解析，无 eval）。"""
        import ast
        import operator as _op

        _ALLOWED_OPS = {
            ast.Add: _op.add,
            ast.Sub: _op.sub,
            ast.Mult: _op.mul,
            ast.Div: _op.truediv,
            ast.FloorDiv: _op.floordiv,
            ast.Mod: _op.mod,
            ast.Pow: _op.pow,
            ast.USub: _op.neg,
            ast.UAdd: _op.pos,
        }

        def _eval_ast(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.BinOp):
                left = _eval_ast(node.left)
                right = _eval_ast(node.right)
                op_cls = type(node.op)
                if op_cls not in _ALLOWED_OPS:
                    raise ValueError(f"Unsupported operator: {op_cls.__name__}")
                if op_cls is ast.Div and right == 0:
                    raise ZeroDivisionError("division by zero")
                return _ALLOWED_OPS[op_cls](left, right)
            if isinstance(node, ast.UnaryOp):
                operand = _eval_ast(node.operand)
                op_cls = type(node.op)
                if op_cls not in _ALLOWED_OPS:
                    raise ValueError(f"Unsupported unary: {op_cls.__name__}")
                return _ALLOWED_OPS[op_cls](operand)
            if isinstance(node, ast.Expression):
                return _eval_ast(node.body)
            raise ValueError(f"Unsupported expression element: {type(node).__name__}")

        try:
            tree = ast.parse(expression.strip(), mode="eval")
            result = _eval_ast(tree)
            return f"{expression} = {result}"
        except ZeroDivisionError:
            return "Error: Division by zero"
        except Exception as e:
            return f"Error: {e}"
