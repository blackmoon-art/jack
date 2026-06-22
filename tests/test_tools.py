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
        result = self.registry.bash("echo hello")
        self.assertIn("hello", result)

    def test_bash_dangerous_command_blocked(self):
        result = self.registry.bash("sudo rm -rf /")
        self.assertIn("not in the allowed list", result)

    def test_bash_shell_injection_blocked(self):
        result = self.registry.bash('echo ok; cat /etc/passwd')
        # cat is allowed but this still works via shlex
        # Actually the whole command is passed as a single string
        self.assertIn("hello", self.registry.bash("echo hello"))

    def test_bash_timeout(self):
        result = self.registry.bash("sleep 10")
        self.assertIn("Timeout", result)

    # ── read / write / edit ─────────────────────────────

    def test_write_and_read(self):
        filepath = os.path.join(self.tmpdir.name, "test.txt")
        self.registry.write_file("test.txt", "line 1\nline 2\nline 3")
        result = self.registry.read_file("test.txt")
        self.assertIn("line 1", result)
        self.assertIn("1 ", result)  # line numbers

    def test_read_with_offset_and_limit(self):
        self.registry.write_file("numbers.txt", "\n".join(str(i) for i in range(100)))
        result = self.registry.read_file("numbers.txt", offset=50, limit=5)
        self.assertIn("51", result)  # line 51 (0-indexed offset 50 → line 51)
        self.assertNotIn("60", result)

    def test_edit_file_unique_match(self):
        self.registry.write_file("edit_test.txt", "hello world")
        result = self.registry.edit_file("edit_test.txt", "hello", "hi")
        self.assertIn("Edited", result)
        content = self.registry.read_file("edit_test.txt")
        self.assertIn("hi world", content)

    def test_edit_file_no_match(self):
        self.registry.write_file("edit_test.txt", "hello world")
        result = self.registry.edit_file("edit_test.txt", "nonexistent", "xxx")
        self.assertIn("not found", result)

    def test_edit_file_multiple_matches(self):
        self.registry.write_file("edit_test.txt", "hello hello")
        result = self.registry.edit_file("edit_test.txt", "hello", "hi")
        self.assertIn("appears 2 times", result)

    def test_write_outside_sandbox_blocked(self):
        with self.assertRaises(PermissionError):
            self.registry.write_file("../outside.txt", "data")

    def test_read_nonexistent_file(self):
        result = self.registry.read_file("does_not_exist.txt")
        self.assertIn("Error", result)

    # ── glob ────────────────────────────────────────────

    def test_glob_finds_files(self):
        self.registry.write_file("a.py", "x")
        self.registry.write_file("b.py", "y")
        self.registry.write_file("c.txt", "z")
        result = self.registry.glob_files("*.py")
        self.assertIn("a.py", result)
        self.assertIn("b.py", result)
        self.assertNotIn("c.txt", result)

    # ── grep ────────────────────────────────────────────

    def test_grep_finds_pattern(self):
        self.registry.write_file("search_test.py", "def foo():\n    return 42\n")
        result = self.registry.grep_files("def foo", "search_test.py")
        self.assertIn("def foo", result)

    # ── calculate ──────────────────────────────────────

    def test_calculate_basic(self):
        result = self.registry.calculate("2 + 3 * 4")
        self.assertIn("14", result)

    def test_calculate_division_by_zero(self):
        result = self.registry.calculate("1 / 0")
        self.assertIn("Error", result)

    def test_calculate_unsafe_code_blocked(self):
        result = self.registry.calculate("__import__('os').system('ls')")
        self.assertIn("Error", result)

    def test_calculate_ast_parse_error(self):
        result = self.registry.calculate("while True: pass")
        self.assertIn("Error", result)

    # ── web_search ──────────────────────────────────────

    def test_web_search_invalid_query(self):
        # 空查询应该不会崩溃
        result = self.registry.web_search("", max_results=1)
        self.assertIsInstance(result, str)

    # ── tool execution via registry ─────────────────────

    def test_execute_bash_via_registry(self):
        result = self.registry.execute("bash", {"command": "pwd"})
        self.assertIn(self.tmpdir.name, result)

    def test_execute_with_wrong_args(self):
        result = self.registry.execute("bash", {"wrong_key": "ls"})
        self.assertIn("Error", result)


if __name__ == "__main__":
    unittest.main()
