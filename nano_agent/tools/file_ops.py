"""文件操作工具：read, write, edit, glob, grep。

所有方法返回 Observation。
"""

import glob as _glob
import re
import subprocess
from pathlib import Path

from ..tools.sandbox import PathSandbox
from .observation import Observation


class FileOps:
    # 工具注册声明
    TOOLS = [
        ("read", "Read a file with line numbers.", "read",
         {"path": {"type": "string", "description": "File path (relative to workspace)"},
          "offset": {"type": "integer", "description": "Start line (0-indexed)"},
          "limit": {"type": "integer", "description": "Max lines to read"}},
         ["path"]),
        ("write", "Write content to a file.", "write",
         {"path": {"type": "string", "description": "File path (relative to workspace)"},
          "content": {"type": "string", "description": "Content to write"}},
         ["path", "content"]),
        ("edit", "Replace a string in a file.", "edit",
         {"path": {"type": "string", "description": "File path (relative to workspace)"},
          "old_string": {"type": "string", "description": "Exact text to replace"},
          "new_string": {"type": "string", "description": "Replacement text"}},
         ["path", "old_string", "new_string"]),
        ("glob", "Find files by glob pattern.", "glob",
         {"pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')"}},
         ["pattern"]),
        ("grep", "Search for a pattern in files.", "grep",
         {"pattern": {"type": "string", "description": "Regex pattern to search"},
          "path": {"type": "string", "description": "Directory to search in (default: .)"}},
         ["pattern"]),
    ]

    def __init__(self, sandbox: PathSandbox, work_dir: str, public_mode: bool = False, charts_dir: str = ""):
        self.sandbox = sandbox
        self.work_dir = work_dir
        self.public_mode = public_mode
        self.charts_dir = charts_dir or work_dir

    def get_tools(self) -> list:
        """返回当前模式允许的工具列表。公网模式下只暴露只读工具。"""
        if self.public_mode:
            return [t for t in self.TOOLS if t[0] in ("read", "glob", "grep")]
        return list(self.TOOLS)

    def read(self, path: str, offset: int = 0, limit: int = 500) -> Observation:
        """读取工作目录内的文件，带行号。"""
        safe = self.sandbox.safe_path(path)
        if not safe.is_file():
            return Observation.error("read_file", f"Not a file: {path}")
        try:
            lines = safe.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return Observation.error("read_file", f"File is not UTF-8 text: {path}")
        except OSError as e:
            return Observation.error("read_file", str(e))

        start = max(offset, 0)
        end = start + limit if limit else len(lines)
        numbered = [f"{i+1:4d} {line}" for i, line in enumerate(lines[start:end], start)]
        return Observation.ok("read_file", "\n".join(numbered))

    def write(self, path: str, content: str) -> Observation:
        """写入文件到工作目录内。"""
        if self.public_mode:
            return Observation.error("write_file", "Write access denied (public mode)")
        safe = self.sandbox.safe_path(path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        try:
            safe.write_text(content, encoding="utf-8")
            result = f"Wrote {len(content)} bytes to {path}"
            # 可下载文件（html/pptx/pdf 等）同时复制到 charts_dir 并返回 Web URL
            import os as _os
            ext = _os.path.splitext(path)[1].lower().lstrip('.')
            _DOWNLOADABLE = {"html", "pptx", "pdf", "csv", "json", "txt", "md", "mp3", "wav"}
            if ext in _DOWNLOADABLE and self.charts_dir != self.work_dir:
                import shutil as _sh, time as _t
                basename = _os.path.basename(path)
                # ASCII 安全文件名
                import re as _re
                safe_name = _re.sub(r'[^a-zA-Z0-9._-]', '_', basename)
                if safe_name == basename or not safe_name.strip('_.'):
                    safe_name = f"file_{int(_t.time())}.{ext}"
                dest = _os.path.join(self.charts_dir, safe_name)
                _sh.copy2(str(safe), dest)
                url = f"/charts/{safe_name}"
                result += f"\n[Download]({url})"
            return Observation.ok("write_file", result)
        except OSError as e:
            return Observation.error("write_file", str(e))

    def edit(self, path: str, old_string: str, new_string: str) -> Observation:
        """精确替换文件中的字符串（仅替换一次，要求唯一匹配）。"""
        if self.public_mode:
            return Observation.error("edit_file", "Edit access denied (public mode)")
        safe = self.sandbox.safe_path(path)
        try:
            content = safe.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Observation.error("edit_file", f"File not found: {path}")
        except OSError as e:
            return Observation.error("edit_file", str(e))

        count = content.count(old_string)
        if count == 0:
            return Observation.error("edit_file", "old_string not found in file")
        if count > 1:
            return Observation.error("edit_file", f"old_string appears {count} times (must be unique)")

        new_content = content.replace(old_string, new_string, 1)
        safe.write_text(new_content, encoding="utf-8")
        return Observation.ok("edit_file", f"Edited {path}: replaced 1 occurrence")

    def glob(self, pattern: str) -> Observation:
        """在工作目录内查找匹配的文件。"""
        safe = self.sandbox.safe_path(pattern)
        full_pattern = str(Path(self.work_dir) / safe)
        try:
            files = _glob.glob(full_pattern, recursive=True)
            rel_files = [str(Path(f).relative_to(self.work_dir)) for f in sorted(files)]
            result = "\n".join(rel_files) if rel_files else "No files found"
            return Observation.ok("glob", result)
        except Exception as e:
            return Observation.error("glob", str(e))

    def grep(self, pattern: str, path: str = ".") -> Observation:
        """在工作目录内搜索正则表达式。"""
        safe = self.sandbox.safe_path(path)
        try:
            result = subprocess.run(
                ["grep", "-rn", "-e", pattern,
                 "--include=*.py", "--include=*.js", "--include=*.ts",
                 "--include=*.md", "--include=*.json", "--include=*.html", "--include=*.css",
                 "--include=*.txt", "--include=*.yaml", "--include=*.yml",
                 "--include=*.sh", "--include=*.toml", "--include=*.ini", "--include=*.cfg",
                 str(safe)],
                capture_output=True, text=True, timeout=30,
            )
            out = result.stdout.strip()
            return Observation.ok("grep", out[:30000] if out else "No matches found")
        except subprocess.TimeoutExpired:
            return Observation.error("grep", "grep timed out")
        except Exception as e:
            return Observation.error("grep", str(e))
