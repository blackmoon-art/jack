"""文件操作工具：read, write, edit, glob, grep。"""

import glob as _glob
import re
import subprocess
from pathlib import Path

from ..tools.sandbox import PathSandbox


class FileOps:
    def __init__(self, sandbox: PathSandbox, work_dir: str):
        self.sandbox = sandbox
        self.work_dir = work_dir

    def read(self, path: str, offset: int = 0, limit: int = 500) -> str:
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

    def write(self, path: str, content: str) -> str:
        """写入文件到工作目录内。"""
        safe = self.sandbox.safe_path(path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        try:
            safe.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {path}"
        except OSError as e:
            return f"Error: {e}"

    def edit(self, path: str, old_string: str, new_string: str) -> str:
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

    def glob(self, pattern: str) -> str:
        """在工作目录内查找匹配的文件。"""
        full_pattern = str(Path(self.work_dir) / pattern)
        try:
            files = _glob.glob(full_pattern, recursive=True)
            rel_files = [str(Path(f).relative_to(self.work_dir)) for f in sorted(files)]
            return "\n".join(rel_files) if rel_files else "No files found"
        except Exception as e:
            return f"Error: {e}"

    def grep(self, pattern: str, path: str = ".") -> str:
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
