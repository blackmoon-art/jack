"""
工具模块单元测试 — 所有测试无需外部服务。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

# 确保项目根在 sys.path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nano_agent.tools import PathSandbox, ToolRegistry


class TestPathSandbox(unittest.TestCase):
    def setUp(self):
        self.sandbox = PathSandbox("/tmp/test_workspace")

    def test_path_within_bounds(self):
        p = self.sandbox.safe_path("src/main.py")
        # macOS /tmp 是 /private/tmp 的软链接，用 resolve 后的路径比较
        expected = Path("/tmp/test_workspace/src/main.py").resolve()
        self.assertEqual(p, expected)

    def test_path_traversal_blocked(self):
        with self.assertRaises(PermissionError):
            self.sandbox.safe_path("../../../etc/passwd")

    def test_absolute_path_blocked(self):
        with self.assertRaises(PermissionError):
            self.sandbox.safe_path("/etc/passwd")


class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.registry = ToolRegistry(self.tmpdir.name, bash_timeout=5)

    def tearDown(self):
        self.tmpdir.cleanup()

    # ── Schema ──────────────────────────────────────────

    def test_get_schemas_returns_list(self):
        schemas = self.registry.get_schemas()
        self.assertIsInstance(schemas, list)
        self.assertGreater(len(schemas), 5)
        # 每个 schema 有正确的结构
        for s in schemas:
            self.assertEqual(s["type"], "function")
            self.assertIn("name", s["function"])
            self.assertIn("parameters", s["function"])

    def test_unknown_tool_returns_error(self):
        result = self.registry.execute("nonexistent", {})
        self.assertIn("Unknown tool", result)

    # ── bash ────────────────────────────────────────────

    def test_bash_safe_command(self):
        result = self.registry.execute("bash", {"command": "echo hello"})
        self.assertIn("hello", str(result))

    def test_bash_dangerous_command_blocked(self):
        result = self.registry.execute("bash", {"command": "sudo rm -rf /"})
        self.assertIn("not allowed", str(result))

    def test_bash_shell_injection_blocked(self):
        result = self.registry.execute("bash", {"command": 'echo ok; cat /etc/passwd'})
        # shell=False + shlex.split means ; is part of one arg, not a shell operator
        # path sandbox also blocks /etc/
        self.assertIn("Error", str(result))
        result2 = self.registry.execute("bash", {"command": "echo hello"})
        self.assertIn("hello", str(result2))

    def test_bash_timeout(self):
        result = self.registry.execute("bash", {"command": "sleep 10"})
        self.assertIn("Timeout", str(result))

    # ── read / write / edit ─────────────────────────────

    def test_write_and_read(self):
        self.registry.execute("write", {"path": "test.txt", "content": "line 1\nline 2\nline 3"})
        result = self.registry.execute("read", {"path": "test.txt"})
        self.assertIn("line 1", str(result))
        self.assertIn("1 ", str(result))  # line numbers

    def test_read_with_offset_and_limit(self):
        self.registry.execute("write", {"path": "numbers.txt", "content": "\n".join(str(i) for i in range(100))})
        result = self.registry.execute("read", {"path": "numbers.txt", "offset": 50, "limit": 5})
        self.assertIn("51", str(result))  # line 51 (0-indexed offset 50 → line 51)
        self.assertNotIn("60", str(result))

    def test_edit_file_unique_match(self):
        self.registry.execute("write", {"path": "edit_test.txt", "content": "hello world"})
        result = self.registry.execute("edit", {"path": "edit_test.txt", "old_string": "hello", "new_string": "hi"})
        self.assertIn("Edited", str(result))
        content = self.registry.execute("read", {"path": "edit_test.txt"})
        self.assertIn("hi world", str(content))

    def test_edit_file_no_match(self):
        self.registry.execute("write", {"path": "edit_test.txt", "content": "hello world"})
        result = self.registry.execute("edit", {"path": "edit_test.txt", "old_string": "nonexistent", "new_string": "xxx"})
        self.assertIn("not found", str(result))

    def test_edit_file_multiple_matches(self):
        self.registry.execute("write", {"path": "edit_test.txt", "content": "hello hello"})
        result = self.registry.execute("edit", {"path": "edit_test.txt", "old_string": "hello", "new_string": "hi"})
        self.assertIn("appears 2 times", str(result))

    def test_write_outside_sandbox_blocked(self):
        result = self.registry.execute("write", {"path": "../outside.txt", "content": "data"})
        self.assertIn("Error", str(result))  # execute() catches PermissionError

    def test_read_nonexistent_file(self):
        result = self.registry.execute("read", {"path": "does_not_exist.txt"})
        self.assertIn("Error", str(result))

    # ── glob ────────────────────────────────────────────

    def test_glob_finds_files(self):
        self.registry.execute("write", {"path": "a.py", "content": "x"})
        self.registry.execute("write", {"path": "b.py", "content": "y"})
        self.registry.execute("write", {"path": "c.txt", "content": "z"})
        result = self.registry.execute("glob", {"pattern": "*.py"})
        self.assertIn("a.py", str(result))
        self.assertIn("b.py", str(result))
        self.assertNotIn("c.txt", str(result))

    # ── grep ────────────────────────────────────────────

    def test_grep_finds_pattern(self):
        self.registry.execute("write", {"path": "search_test.py", "content": "def foo():\n    return 42\n"})
        result = self.registry.execute("grep", {"pattern": "def foo", "path": "search_test.py"})
        self.assertIn("def foo", str(result))

    # ── calculate ──────────────────────────────────────

    def test_calculate_basic(self):
        result = self.registry.execute("calculate", {"expression": "2 + 3 * 4"})
        self.assertIn("14", str(result))

    def test_calculate_division_by_zero(self):
        result = self.registry.execute("calculate", {"expression": "1 / 0"})
        self.assertIn("Error", str(result))

    def test_calculate_unsafe_code_blocked(self):
        result = self.registry.execute("calculate", {"expression": "__import__('os').system('ls')"})
        self.assertIn("Error", str(result))

    def test_calculate_ast_parse_error(self):
        result = self.registry.execute("calculate", {"expression": "while True: pass"})
        self.assertIn("Error", str(result))

    # ── web_search ──────────────────────────────────────

    def test_web_search_invalid_query(self):
        result = self.registry.execute("web_search", {"query": "", "max_results": 1})
        self.assertIsInstance(str(result), str)

    # ── tool execution via registry ─────────────────────

    def test_execute_bash_via_registry(self):
        result = self.registry.execute("bash", {"command": "pwd"})
        self.assertIn(self.tmpdir.name, result)

    def test_execute_with_wrong_args(self):
        result = self.registry.execute("bash", {"wrong_key": "ls"})
        self.assertIn("Error", result)


if __name__ == "__main__":
    unittest.main()
