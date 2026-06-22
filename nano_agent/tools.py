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

    def __init__(self, work_dir: str, bash_timeout: int = 120,
                 brave_api_key: str = ""):
        self.sandbox = PathSandbox(work_dir)
        self.bash_timeout = bash_timeout
        self.work_dir = work_dir
        self.brave_api_key = brave_api_key

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
        self._register("web_search", "Search the web using DuckDuckGo.", self.web_search, {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default: 5)"},
        }, required=["query"])
        self._register("fetch_url", "Fetch and extract text content from a URL.", self.fetch_url, {
            "url": {"type": "string", "description": "URL to fetch"},
        }, required=["url"])
        self._register("search_and_fetch", "Search the web and auto-fetch top result content. Best for questions needing detailed answers.", self.search_and_fetch, {
            "query": {"type": "string", "description": "Search query"},
        }, required=["query"])
        self._register("plan", "Break a complex task into steps and execute sequentially.", self.plan, {
            "task": {"type": "string", "description": "The task to plan"},
        }, required=["task"])
        self._register("calculate", "Evaluate a mathematical expression safely.", self.calculate, {
            "expression": {"type": "string", "description": "Math expression (e.g. 1+2*3)"},
        }, required=["expression"])
        self._register("get_weather", "Get real-time weather for a city using Open-Meteo API (free, no key).", self.get_weather, {
            "city": {"type": "string", "description": "City name in Chinese or English (e.g. 北京, Shanghai, Tokyo)"},
        }, required=["city"])
        self._register("stock_info", "Get real-time stock quote and news. Supports US stocks (AAPL), HK (0700.HK), China (600519.SS). Data from Yahoo Finance.", self.stock_info, {
            "symbol": {"type": "string", "description": "Stock symbol (e.g. AAPL, TSLA, 0700.HK, 600519.SS)"},
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
                ["grep", "-rn",
                 "--include=*.py", "--include=*.js", "--include=*.ts",
                 "--include=*.md", "--include=*.json", "--include=*.html", "--include=*.css",
                 "--include=*.txt", "--include=*.yaml", "--include=*.yml",
                 "--include=*.sh", "--include=*.toml", "--include=*.ini", "--include=*.cfg",
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
        """搜索网页：Brave → DuckDuckGo → Bing → SearXNG → Wikipedia 五级降级。"""
        max_results = min(max(max_results, 1), 10)

        # Level 0: Brave Search API (如有 API key)
        if self.brave_api_key:
            results = self._search_brave(query, max_results)
            if results:
                return f"Search results for '{query}' (Brave):\n\n" + "\n\n".join(results)

        # Level 1: DuckDuckGo Lite (POST)
        results = self._search_duckduckgo(query, max_results)
        if results:
            return f"Search results for '{query}':\n\n" + "\n\n".join(results)

        # Level 2: Bing (国际版 → 国内版)
        results = self._search_bing(query, max_results)
        if results:
            return f"Search results for '{query}' (Bing):\n\n" + "\n\n".join(results)

        # Level 3: SearXNG 公共实例
        results = self._search_searxng(query, max_results)
        if results:
            return f"Search results for '{query}' (SearXNG):\n\n" + "\n\n".join(results)

        # Level 4: Wikipedia API
        results = self._search_wikipedia(query, max_results)
        if results:
            return f"Search results for '{query}' (Wikipedia):\n\n" + "\n\n".join(results)

        return f"No search results found for '{query}'."

    def _search_brave(self, query: str, max_results: int) -> list[str]:
        """Brave Search API (需要 API key)。失败返回空列表。"""
        try:
            req = urllib.request.Request(
                f"https://api.search.brave.com/res/v1/web/search?"
                f"{urllib.parse.urlencode({'q': query, 'count': str(max_results)})}",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.brave_api_key,
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8"))
        except Exception:
            return []

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            title = item.get("title", "").strip()
            url = item.get("url", "")
            desc = item.get("description", "").strip()
            if title and url:
                s = f" — {desc[:200]}" if desc else ""
                results.append(f"{len(results)+1}. {title}\n   {url}{s}")
        return results

    def _search_duckduckgo(self, query: str, max_results: int) -> list[str]:
        """DuckDuckGo Lite POST 搜索。被反爬时返回空列表。"""
        data = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode()
        url = "https://lite.duckduckgo.com/lite/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://lite.duckduckgo.com/",
        }
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            return []

        # 反爬检测
        if "anomaly" in html.lower():
            return []

        results = []
        # 提取结果块 (包含链接、标题和摘要)
        blocks = re.findall(
            r'<tr[^>]*class="result-snippet"[^>]*>.*?</tr>',
            html, re.DOTALL,
        )
        if not blocks:
            # 回退到简单链接匹配
            blocks = re.findall(
                r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                html, re.DOTALL,
            )
            seen = set()
            for href, title in blocks:
                if len(results) >= max_results:
                    break
                title_clean = self._clean_html(title)
                if not title_clean or len(title_clean) < 3 or href in seen:
                    continue
                if "duckduckgo" in href:
                    continue
                seen.add(href)
                results.append(f"{len(results)+1}. {title_clean}\n   {href}")
        else:
            seen = set()
            for block in blocks:
                if len(results) >= max_results:
                    break
                link_m = re.search(
                    r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL
                )
                snippet_m = re.search(
                    r'<td class="result-snippet"[^>]*>(.*?)</td>', block, re.DOTALL
                )
                if link_m:
                    href = link_m.group(1)
                    title = self._clean_html(link_m.group(2))
                    if not title or len(title) < 3 or href in seen:
                        continue
                    if "duckduckgo" in href:
                        continue
                    seen.add(href)
                    snippet = ""
                    if snippet_m:
                        snippet = " — " + self._clean_html(snippet_m.group(1))[:200]
                    results.append(f"{len(results)+1}. {title}\n   {href}{snippet}")
        return results

    def _clean_html(self, text: str) -> str:
        """清理 HTML 实体和标签。"""
        return (re.sub(r'<[^>]+>', '', text).strip()
                .replace("&#x27;", "'").replace("&amp;", "&")
                .replace("&quot;", '"').replace("&lt;", "<")
                .replace("&gt;", ">").replace("&#039;", "'"))

    def _search_bing(self, query: str, max_results: int) -> list[str]:
        """Bing 搜索 (国际版 → 国内版)。被反爬时返回空列表。"""
        sources = [
            ("https://www.bing.com/search", {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }),
            ("https://cn.bing.com/search", {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            }),
        ]
        params = f"q={urllib.parse.quote_plus(query)}&count={max_results}"

        html = ""
        for base_url, headers in sources:
            try:
                req = urllib.request.Request(f"{base_url}?{params}", headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                if html and len(html) > 1000:
                    break
            except Exception:
                continue

        if not html:
            return []

        # 解析 Bing 结果 (<li class="b_algo">)
        blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL)
        results = []
        seen = set()
        for block in blocks:
            if len(results) >= max_results:
                break
            title_m = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h2>', block, re.DOTALL)
            snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            if not snippet_m:
                snippet_m = re.search(r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', block, re.DOTALL)

            if title_m:
                href = title_m.group(1)
                title = re.sub(r'<[^>]+>', '', title_m.group(2)).strip().replace("&#x27;", "'").replace("&amp;", "&").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
                if not title or len(title) < 3 or href in seen:
                    continue
                seen.add(href)
                snippet = ""
                if snippet_m:
                    snippet = " — " + re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip()[:200]
                results.append(f"{len(results)+1}. {title}\n   {href}{snippet}")
        return results

    def _search_searxng(self, query: str, max_results: int) -> list[str]:
        """SearXNG 公共实例搜索。被反爬时返回空列表。"""
        instances = [
            "https://searx.be",
            "https://search.sapti.me",
        ]
        params = urllib.parse.urlencode({
            "q": query, "format": "json", "categories": "general",
        })
        for base_url in instances:
            try:
                req = urllib.request.Request(
                    f"{base_url}/search?{params}",
                    headers={"User-Agent": "nano_agent_plus/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                results = []
                for item in data.get("results", [])[:max_results]:
                    title = item.get("title", "").strip()
                    url = item.get("url", "")
                    snippet = item.get("content", "") or item.get("snippet", "")
                    if title and url:
                        s = f" — {self._clean_html(snippet)[:200]}" if snippet else ""
                        results.append(f"{len(results)+1}. {title}\n   {url}{s}")
                return results
            except Exception:
                continue
        return []

    def search_and_fetch(self, query: str) -> str:
        """搜索 + 自动抓取第一个结果的内容。"""
        # 先用 web_search 获取结果
        search_result = self.web_search(query, max_results=3)
        if "No search results" in search_result or "Error" in search_result:
            return search_result

        # 提取第一个 URL
        urls = re.findall(r'(https?://[^\s\n]+)', search_result)
        if not urls:
            return search_result + "\n\n(Could not extract URL for auto-fetch)"

        first_url = urls[0]
        content = self.fetch_url(first_url)
        return (
            f"{search_result}\n\n"
            f"─── Auto-fetched: {first_url} ───\n"
            f"{content[:5000]}"
        )

    def _search_wikipedia(self, query: str, max_results: int) -> list[str]:
        """Wikipedia API 搜索 (中英文自动检测，无需 API key)。"""
        # 检测中文：含中文字符则用中文 Wikipedia
        has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in query)
        lang = "zh" if has_cjk else "en"
        api_url = f"https://{lang}.wikipedia.org/w/api.php"
        params = urllib.parse.urlencode({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": str(max_results),
        })
        try:
            req = urllib.request.Request(
                f"{api_url}?{params}",
                headers={"User-Agent": "nano_agent_plus/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        results = []
        search_items = data.get("query", {}).get("search", [])
        for item in search_items:
            title = item["title"]
            snippet = re.sub(r'<[^>]+>', '', item.get("snippet", ""))
            page_url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            results.append(f"{len(results)+1}. {title}\n   {page_url}\n   {snippet[:200]}")
        return results

    def fetch_url(self, url: str) -> str:
        """抓取网页并提取文本内容。"""
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "html" not in content_type and "text" not in content_type:
                    return f"Error: Unsupported content type — {content_type}"
                raw = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            return f"Error: HTTP {e.code} — {e.reason}"
        except urllib.error.URLError as e:
            return f"Error: Cannot reach URL — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        # 提取文本：去标签 + 去多余空白 + 截断
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000] if text else "(页面无文本内容)"

    # ── 天气查询（实时数据，Open-Meteo API）──────────────

    _WMO_CODES: dict = {
        0: "晴天 ☀️", 1: "大部晴朗 🌤️", 2: "多云 ⛅", 3: "阴天 ☁️",
        45: "有雾 🌫️", 48: "雾凇 🌫️",
        51: "毛毛雨 🌧️", 53: "毛毛雨 🌧️", 55: "大毛毛雨 🌧️",
        61: "小雨 🌧️", 63: "中雨 🌧️", 65: "大雨 🌧️",
        71: "小雪 ❄️", 73: "中雪 ❄️", 75: "大雪 ❄️", 77: "雪粒 ❄️",
        80: "阵雨 ⛈️", 81: "中阵雨 ⛈️", 82: "大阵雨 ⛈️",
        85: "小阵雪 🌨️", 86: "大阵雪 🌨️",
        95: "雷暴 ⚡", 96: "冰雹雷暴 ⚡", 99: "强冰雹雷暴 ⚡",
    }

    def get_weather(self, city: str) -> str:
        """获取城市实时天气。数据来源 Open-Meteo，免费无需 API Key。"""
        import json as _json

        # Step 1: 地理编码 (city → lat/lon)
        geo_url = (
            "https://geocoding-api.open-meteo.com/v1/search"
            f"?name={urllib.parse.quote(city)}&count=1&language=zh&format=json"
        )
        try:
            req = urllib.request.Request(geo_url, headers={
                "User-Agent": "nano_agent_plus/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                geo_data = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            return f"Error: Cannot reach weather service — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        results = geo_data.get("results")
        if not results or not isinstance(results, list) or len(results) == 0:
            return f"Error: City not found — '{city}'"

        r = results[0]
        lat, lon = r["latitude"], r["longitude"]
        location = f"{r.get('admin1', '')} {r['name']}".strip()
        country = r.get("country", "")

        # Step 2: 获取天气
        weather_url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            "weather_code,wind_speed_10m,pressure_msl"
            "&timezone=auto"
        )
        try:
            req = urllib.request.Request(weather_url, headers={
                "User-Agent": "nano_agent_plus/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                w_data = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            return f"Error: Cannot fetch weather — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        c = w_data.get("current", {})
        if not c:
            return f"Error: Weather data unavailable for {location}"

        weather = self._WMO_CODES.get(c.get("weather_code", -1), "未知")
        return (
            f"📍 {location} ({country}) 实时天气\n"
            f"🌡️  温度: {c['temperature_2m']}°C\n"
            f"🤔 体感温度: {c['apparent_temperature']}°C\n"
            f"💧 湿度: {c['relative_humidity_2m']}%\n"
            f"🌬️  风速: {c['wind_speed_10m']} km/h\n"
            f"🌀 气压: {c['pressure_msl']} hPa\n"
            f"☁️  天气: {weather}"
        )

    def stock_info(self, symbol: str) -> str:
        """获取股票实时行情。数据来源 Yahoo Finance，免费无需 Key。"""
        import json as _json

        symbol = symbol.upper().strip()
        try:
            # Yahoo Finance v8 chart API (无需 API Key)
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
                "?interval=1d&range=1mo"
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return f"Error: Stock symbol not found — '{symbol}' (HTTP {e.code})"
        except urllib.error.URLError as e:
            return f"Error: Cannot reach Yahoo Finance — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        result = data.get("chart", {}).get("result", [])
        if not result:
            return f"Error: No data for symbol '{symbol}'"

        meta = result[0].get("meta", {})
        quotes = result[0].get("indicators", {}).get("quote", [{}])[0]

        price = meta.get("regularMarketPrice", "N/A")
        prev_close = meta.get("previousClose", "N/A")
        change = round(price - prev_close, 2) if isinstance(price, (int, float)) and isinstance(prev_close, (int, float)) else "N/A"
        change_pct = round(change / prev_close * 100, 2) if isinstance(change, (int, float)) and prev_close else "N/A"
        high = meta.get("regularMarketDayHigh", "N/A")
        low = meta.get("regularMarketDayLow", "N/A")
        volume = meta.get("regularMarketVolume", "N/A")
        name = meta.get("longName") or meta.get("shortName") or symbol
        currency = meta.get("currency", "USD")
        exchange = meta.get("exchangeName", "")

        sign = "+" if isinstance(change, (int, float)) and change >= 0 else ""
        arrow = "📈" if isinstance(change, (int, float)) and change >= 0 else "📉"

        return (
            f"{arrow} {name} ({symbol}) — {exchange}\n"
            f"💵 价格: {price} {currency}\n"
            f"📊 涨跌: {sign}{change} ({sign}{change_pct}%)\n"
            f"📈 最高: {high} | 📉 最低: {low}\n"
            f"📦 成交量: {volume}\n"
            f"🕐 昨收: {prev_close} {currency}"
        )

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
