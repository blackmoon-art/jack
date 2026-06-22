"""
记忆模块单元测试。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nano_agent.memory import Memory, LongTermMemory


class TestWindowMemory(unittest.TestCase):
    def setUp(self):
        self.mem = Memory(window_size=3, file_path=None)

    def test_save_and_retrieve_context(self):
        self.mem.save_context("hello", "hi there")
        self.mem.save_context("how are you", "good")
        msgs = self.mem.get_window_messages()
        self.assertEqual(len(msgs), 4)  # 2 rounds × 2 messages
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"], "hello")
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[1]["content"], "hi there")

    def test_round_count(self):
        self.assertEqual(self.mem.round_count, 0)
        self.mem.save_context("a", "b")
        self.assertEqual(self.mem.round_count, 1)
        self.mem.save_context("c", "d")
        self.assertEqual(self.mem.round_count, 2)

    def test_window_eviction(self):
        # window_size=3, 所以最多保留 6 条消息
        for i in range(5):
            self.mem.save_context(f"user {i}", f"assistant {i}")
        msgs = self.mem.get_window_messages()
        self.assertEqual(len(msgs), 6)  # 2*3
        # 最早的消息已被淘汰
        self.assertEqual(msgs[0]["content"], "user 2")

    def test_clear(self):
        self.mem.save_context("a", "b")
        self.mem.clear()
        self.assertEqual(self.mem.round_count, 0)
        self.assertEqual(len(self.mem.get_window_messages()), 0)


class TestPersistentMemory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.file_path = os.path.join(self.tmpdir.name, "memory.md")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_save_and_load(self):
        mem = Memory(window_size=5, file_path=self.file_path)
        mem.save_persistent("test task", "test result")
        self.assertTrue(os.path.exists(self.file_path))

        content = mem.load_persistent()
        self.assertIn("test task", content)
        self.assertIn("test result", content)

    def test_load_empty_file(self):
        mem = Memory(window_size=5, file_path="/nonexistent/path/memory.md")
        content = mem.load_persistent()
        self.assertEqual(content, "")

    def test_truncate_long_persistent(self):
        mem = Memory(window_size=5, file_path=self.file_path)
        # 写入超过 200 行的内容
        for i in range(80):
            mem.save_persistent(f"task {i}", f"result {i}")
        content = mem.load_persistent()
        lines = content.split("\n")
        self.assertLessEqual(len(lines), 200)

    def test_get_summary(self):
        mem = Memory(window_size=5, file_path=self.file_path)
        # 写入一条记录使文件存在
        mem.save_persistent("test", "result")
        summary = mem.get_summary()
        self.assertIn("Working:", summary)
        self.assertIn("Persistent:", summary)


class TestLongTermMemory(unittest.TestCase):
    """长期记忆 (SQLite FTS5) 测试。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_long_term.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_init_creates_db(self):
        ltm = LongTermMemory(self.db_path)
        self.assertTrue(os.path.exists(self.db_path))
        self.assertEqual(ltm.count(), 0)

    def test_add_and_count(self):
        ltm = LongTermMemory(self.db_path)
        ltm.add("calculate 1+1", "result is 2")
        ltm.add("write a file", "file written")
        self.assertEqual(ltm.count(), 2)

    def test_search_exact_match(self):
        ltm = LongTermMemory(self.db_path)
        ltm.add("calculate 1+1", "result is 2")
        ltm.add("write a file", "file written")
        results = ltm.search("calculate")
        self.assertEqual(len(results), 1)
        self.assertIn("calculate", results[0]["task"])

    def test_search_no_match(self):
        ltm = LongTermMemory(self.db_path)
        ltm.add("hello world", "hi")
        results = ltm.search("nonexistent_query_xyz")
        self.assertEqual(len(results), 0)

    def test_search_top_k(self):
        ltm = LongTermMemory(self.db_path)
        for i in range(10):
            ltm.add(f"task number {i}", f"result {i}")
        results = ltm.search("task", top_k=3)
        self.assertLessEqual(len(results), 3)

    def test_clear(self):
        ltm = LongTermMemory(self.db_path)
        ltm.add("a", "b")
        self.assertEqual(ltm.count(), 1)
        ltm.clear()
        self.assertEqual(ltm.count(), 0)

    def test_memory_load_relevant(self):
        """测试 Memory 门面的 load_relevant()。"""
        mem = Memory(window_size=5, file_path=None, long_term_db=self.db_path)
        mem.save_persistent("python web server", "use fastapi")
        mem.save_persistent("data analysis", "use pandas")
        result = mem.load_relevant("python server")
        self.assertIn("fastapi", result)

    def test_memory_summary_includes_long_term(self):
        mem = Memory(window_size=5, file_path=None, long_term_db=self.db_path)
        mem.save_persistent("test", "result")
        summary = mem.get_summary()
        self.assertIn("Long-term:", summary)
        self.assertIn("1 entries", summary)


if __name__ == "__main__":
    unittest.main()
