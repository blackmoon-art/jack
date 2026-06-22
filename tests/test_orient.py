"""
Orient 模块单元测试。
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import os
import unittest
from unittest.mock import MagicMock

from nano_agent.config import Config
from nano_agent.orient import Orient


def _make_config():
    cfg = Config()
    cfg.provider = "openai"
    cfg.model = "test-model"
    cfg.work_dir = "/tmp"
    return cfg


class TestOrientEngine(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()
        self.config.rules_dir = None  # 默认无规则

    def _make_llm(self, response_text):
        mock = MagicMock()
        mock.chat.return_value = {
            "text": response_text,
            "tool_calls": [],
            "stop_reason": "stop",
        }
        return mock

    def test_orient_parses_valid_json(self):
        llm = self._make_llm(json.dumps({
            "interpretation": "The command succeeded and listed 3 files.",
            "association": "Similar to the previous directory listing.",
            "implication": "Now we can read the files to understand their content.",
            "confidence": 8,
            "focus": "Read agent.py to understand the structure.",
        }))
        orient = Orient(self.config, llm)
        result = orient.orient("file1.py\nfile2.py", "list files")
        self.assertEqual(result["interpretation"],
                         "The command succeeded and listed 3 files.")
        self.assertEqual(result["confidence"], 8)
        self.assertIn("agent.py", result["focus"])

    def test_orient_handles_markdown_wrapped_json(self):
        llm = self._make_llm("""```json
{"interpretation": "ok", "association": "", "implication": "", "confidence": 5, "focus": "x"}
```""")
        orient = Orient(self.config, llm)
        result = orient.orient("data", "task")
        self.assertEqual(result["interpretation"], "ok")

    def test_orient_handles_invalid_json_gracefully(self):
        llm = self._make_llm("This is not JSON at all, just a paragraph of text.")
        orient = Orient(self.config, llm)
        result = orient.orient("data", "task")
        self.assertIn("interpretation", result)
        self.assertIn("implication", result)
        self.assertEqual(result["confidence"], 5)  # fallback

    def test_load_rules_returns_empty_when_no_dir(self):
        llm = self._make_llm("")
        orient = Orient(self.config, llm)
        self.assertEqual(orient.load_rules(), "")

    def test_load_rules_reads_markdown_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rule_file = os.path.join(tmpdir, "code_style.md")
            with open(rule_file, "w") as f:
                f.write("Always use type hints.")
            rule_file2 = os.path.join(tmpdir, "testing.md")
            with open(rule_file2, "w") as f:
                f.write("Write tests before code.")

            self.config.rules_dir = tmpdir
            llm = self._make_llm("")
            orient = Orient(self.config, llm)
            rules = orient.load_rules()
            self.assertIn("type hints", rules)
            self.assertIn("tests before code", rules)

    def test_load_rules_is_cached(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rule_file = os.path.join(tmpdir, "rule.md")
            with open(rule_file, "w") as f:
                f.write("Cache test.")
            self.config.rules_dir = tmpdir
            llm = self._make_llm("")
            orient = Orient(self.config, llm)

            rules1 = orient.load_rules()
            # 第二次调用应命中缓存
            rules2 = orient.load_rules()
            self.assertEqual(rules1, rules2)

    def test_find_applicable_rules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rule_file = os.path.join(tmpdir, "python.md")
            with open(rule_file, "w") as f:
                f.write("Always format Python code with black.")
            rule_file2 = os.path.join(tmpdir, "git.md")
            with open(rule_file2, "w") as f:
                f.write("Always squash commits before merging.")

            self.config.rules_dir = tmpdir
            llm = self._make_llm("")
            orient = Orient(self.config, llm)

            # 提到 Python 的观察应匹配 python 规则
            result = orient.find_applicable_rules(
                "The python code needs formatting"
            )
            self.assertIn("black", result)

            # 不相关的观察不应匹配
            result = orient.find_applicable_rules(
                "unrelated topic about cooking"
            )
            self.assertEqual(result, "")

    def test_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rule_file = os.path.join(tmpdir, "rule.md")
            with open(rule_file, "w") as f:
                f.write("Old rule.")
            self.config.rules_dir = tmpdir
            llm = self._make_llm("")
            orient = Orient(self.config, llm)

            orient.load_rules()  # 缓存
            orient.invalidate_cache()
            self.assertIsNone(orient._rule_cache)


if __name__ == "__main__":
    unittest.main()
