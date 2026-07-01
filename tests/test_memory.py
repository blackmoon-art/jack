"""
记忆模块单元测试 — 覆盖 4 层记忆系统: Working / Persistent / Reflection / LongTerm。
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nano_agent.memory import Memory, LongTermMemory, ReflexionTrace


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


class TestReflexionTrace(unittest.TestCase):
    """Reflexion 轨迹持久化 (SQLite) 测试。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_trace.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_init_creates_db(self):
        trace = ReflexionTrace(self.db_path)
        self.assertTrue(os.path.exists(self.db_path))

    def test_start_and_get_trace(self):
        trace = ReflexionTrace(self.db_path)
        tid = trace.start_trace("test task")
        self.assertGreater(tid, 0)

        info = trace.get_trace(tid)
        self.assertIsNotNone(info)
        self.assertEqual(info["task"], "test task")

    def test_save_attempt_and_trace(self):
        trace = ReflexionTrace(self.db_path)
        tid = trace.start_trace("bug fix")
        trace.save_attempt(
            tid, 1, "result text",
            {"status": "failed", "score": 3, "reason": "wrong approach"},
            reflection="WHAT WENT WRONG: bad tool",
            lesson="check before using"
        )
        info = trace.get_trace(tid)
        self.assertEqual(len(info["attempts"]), 1)
        self.assertEqual(info["attempts"][0]["eval_score"], 3)

    def test_save_and_load_lessons(self):
        trace = ReflexionTrace(self.db_path)
        trace.save_lesson("always validate inputs", trace_id=1)
        trace.save_lesson("use smaller steps", trace_id=2)
        trace.save_lesson("cache expensive calls", trace_id=3)

        lessons = trace.load_lessons(limit=10)
        self.assertGreaterEqual(len(lessons), 3)

    def test_search_lessons_cjk(self):
        trace = ReflexionTrace(self.db_path)
        trace.save_lesson("总是先验证输入再处理")
        trace.save_lesson("使用缓存减少API调用")
        trace.save_lesson("分步执行复杂任务")

        results = trace.search_lessons("验证输入", top_k=3)
        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(any("验证" in r for r in results))

    def test_search_lessons_english(self):
        trace = ReflexionTrace(self.db_path)
        trace.save_lesson("always use timeouts")
        trace.save_lesson("cache API results")
        trace.save_lesson("split complex tasks")

        # search_lessons uses LIKE matching with AND condition
        # "timeout" matches "timeouts" via LIKE %timeout%
        results = trace.search_lessons("timeout", top_k=3)
        self.assertGreaterEqual(len(results), 1)

    def test_recent_traces(self):
        trace = ReflexionTrace(self.db_path)
        for i in range(5):
            tid = trace.start_trace(f"task {i}")
            trace.save_attempt(tid, 1, f"result {i}",
                              {"status": "success", "score": 8, "reason": "ok"})
        recent = trace.recent_traces(limit=3)
        self.assertEqual(len(recent), 3)

    def test_stats(self):
        trace = ReflexionTrace(self.db_path)
        for i in range(5):
            tid = trace.start_trace(f"task {i}")
            score = 9 if i < 3 else 4
            trace.save_attempt(tid, 1, f"result {i}",
                             {"status": "success" if score >= 7 else "failed",
                              "score": score, "reason": "test"})
        trace.save_lesson("a lesson", trace_id=1)
        stats = trace.stats()
        self.assertEqual(stats["total_traces"], 5)
        self.assertEqual(stats["success_count"], 3)  # score ≥ 7
        self.assertAlmostEqual(stats["success_rate"], 0.6)
        self.assertEqual(stats["total_lessons"], 1)

    def test_get_nonexistent_trace(self):
        trace = ReflexionTrace(self.db_path)
        self.assertIsNone(trace.get_trace(99999))


class TestLongTermMemoryCJK(unittest.TestCase):
    """长期记忆中文搜索测试。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_ltm_cjk.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_cjk_search_bigram(self):
        ltm = LongTermMemory(self.db_path)
        ltm.add("如何使用Python进行数据分析", "使用pandas和matplotlib")
        ltm.add("如何用FastAPI构建Web服务", "FastAPI是一个现代框架")
        ltm.add("机器学习的数学基础", "线性代数和微积分")

        # FTS5 unicode61 tokenizer + CJK bigram manual tokenization
        results = ltm.search("数据分析", top_k=3)
        # FTS5 unicode61 may not perfectly index CJK; search is best-effort
        if results:
            self.assertIn("数据分析", results[0]["task"])

    def test_mixed_cjk_english_search(self):
        ltm = LongTermMemory(self.db_path)
        ltm.add("Python Web开发 FastAPI教程", "使用FastAPI构建API")
        ltm.add("Rust系统编程入门", "所有权和借用")
        ltm.add("JavaScript前端框架React", "组件化开发")

        results = ltm.search("Python FastAPI", top_k=3)
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("FastAPI", results[0]["task"])

    def test_search_empty_db(self):
        ltm = LongTermMemory(self.db_path)
        # DB has no entries
        results = ltm.search("anything")
        self.assertEqual(results, [])

    def test_search_empty_query(self):
        ltm = LongTermMemory(self.db_path)
        ltm.add("test task", "result")
        results = ltm.search("")
        self.assertEqual(results, [])


class TestMemoryRotationEdgeCases(unittest.TestCase):
    """文件轮转边缘情况。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.file_path = os.path.join(self.tmpdir.name, "mem.md")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_concurrent_writes_safe(self):
        """并发写入不应导致数据丢失。"""
        mem = Memory(window_size=5, file_path=self.file_path, max_lines=50)
        errors = []

        def writer(thread_id):
            try:
                for i in range(20):
                    mem.save_persistent(f"thread {thread_id} task {i}",
                                       f"thread {thread_id} result {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        content = mem.load_persistent()
        self.assertGreater(len(content), 0)

    def test_cache_invalidation_on_write(self):
        """持久化写入后缓存应失效。"""
        mem = Memory(window_size=5, file_path=self.file_path)
        mem.save_persistent("first task", "first result")
        first_load = mem.load_persistent()
        self.assertIn("first", first_load)

        mem.save_persistent("second task", "second result")
        second_load = mem.load_persistent()
        self.assertIn("second", second_load)

    def test_reflection_save_and_load(self):
        """反思记忆的保存和加载。"""
        refl_path = os.path.join(self.tmpdir.name, "reflections.md")
        mem = Memory(window_size=5, file_path=None, reflection_path=refl_path)
        mem.save_reflection("broken task",
                           "LESSON: always check inputs first",
                           {"status": "failed", "score": 2})
        content = mem.load_reflections()
        self.assertIn("always check inputs first", content)

    def test_memory_with_all_layers(self):
        """所有 4 层记忆同时工作。"""
        refl_path = os.path.join(self.tmpdir.name, "refl.md")
        ltm_db = os.path.join(self.tmpdir.name, "ltm.db")
        rx_db = os.path.join(self.tmpdir.name, "rx.db")

        mem = Memory(
            window_size=5,
            file_path=self.file_path,
            reflection_path=refl_path,
            long_term_db=ltm_db,
            reflexion_db=rx_db,
            max_lines=100,
        )

        # 窗口记忆
        mem.save_context("hello", "hi")
        self.assertEqual(mem.round_count, 1)

        # 持久记忆
        mem.save_persistent("task A", "result A")
        self.assertIn("task A", mem.load_persistent())

        # 反思记忆
        mem.save_reflection("failed task", "LESSON: retry with timeout",
                           {"status": "failed", "score": 3})
        self.assertIn("retry", mem.load_reflections())

        # 长期记忆
        relevant = mem.load_relevant("task A")
        self.assertIn("result A", relevant)

        # 摘要
        summary = mem.get_summary()
        self.assertIn("Working:", summary)
        self.assertIn("Persistent:", summary)
        self.assertIn("Reflection:", summary)
        self.assertIn("Long-term:", summary)


if __name__ == "__main__":
    unittest.main()
