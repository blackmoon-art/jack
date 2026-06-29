"""
Evaluation 模块单元测试 — 使用 Mock LLM，不调用真实 API。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluation import Benchmark, TaskCase, TaskResult, EvalReport
from nano_agent.config import Config


class TestTaskCase(unittest.TestCase):
    """测试 TaskCase 数据结构。"""

    def test_create_task(self):
        t = TaskCase(id="test", description="do something",
                     expected_keywords=["hello"])
        self.assertEqual(t.id, "test")
        self.assertEqual(t.category, "general")
        self.assertEqual(t.difficulty, "easy")
        self.assertEqual(t.expected_keywords, ["hello"])

    def test_defaults(self):
        t = TaskCase(id="x", description="x")
        self.assertEqual(t.category, "general")
        self.assertEqual(t.difficulty, "easy")
        self.assertEqual(t.expected_keywords, [])
        self.assertEqual(t.expected_tool, "")
        self.assertEqual(t.timeout, 60)


class TestEvalReport(unittest.TestCase):
    """测试 EvalReport 统计。"""

    def _make_results(self, successes):
        return [
            TaskResult(task_id=f"task{i}", strategy="default",
                       success=s, response="ok", duration_s=1.0 * (i + 1),
                       tool_calls=i)
            for i, s in enumerate(successes)
        ]

    def test_success_rate_all_pass(self):
        r = EvalReport(strategy="default", results=self._make_results([True, True, True]))
        self.assertEqual(r.total, 3)
        self.assertEqual(r.passed_count, 3)
        self.assertAlmostEqual(r.success_rate, 1.0)

    def test_success_rate_partial(self):
        r = EvalReport(strategy="default", results=self._make_results([True, False, True]))
        self.assertEqual(r.passed_count, 2)
        self.assertAlmostEqual(r.success_rate, 2 / 3)

    def test_success_rate_empty(self):
        r = EvalReport(strategy="default", results=[])
        self.assertEqual(r.total, 0)
        self.assertAlmostEqual(r.success_rate, 0.0)

    def test_avg_duration(self):
        r = EvalReport(strategy="default", results=self._make_results([True, True]))
        self.assertAlmostEqual(r.avg_duration, 1.5)

    def test_avg_tool_calls(self):
        r = EvalReport(strategy="default", results=self._make_results([True, True]))
        self.assertAlmostEqual(r.avg_tool_calls, 0.5)

    def test_summary_text(self):
        r = EvalReport(strategy="react", results=self._make_results([True, False]))
        s = r.summary()
        self.assertIn("react", s)
        self.assertIn("50.0%", s)

    def test_to_dict(self):
        r = EvalReport(strategy="default", results=self._make_results([True]))
        d = r.to_dict()
        self.assertEqual(d["strategy"], "default")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["passed"], 1)


class TestBenchmark(unittest.TestCase):
    """测试 Benchmark 运行器。"""

    def test_default_tasks_loaded(self):
        bench = Benchmark()
        self.assertGreater(len(bench.tasks), 0)
        ids = [t.id for t in bench.tasks]
        self.assertIn("calc", ids)

    def test_custom_tasks_filter(self):
        bench = Benchmark()
        # 过滤不存在的 task_ids
        filtered = [t for t in bench.tasks if t.id in ["calc"]]
        self.assertEqual(len(filtered), 1)

    def test_evaluate_keywords_pass(self):
        bench = Benchmark()
        task = TaskCase(id="t", description="t", expected_keywords=["hello"])
        self.assertTrue(bench._evaluate(task, "world hello!", 0))

    def test_evaluate_keywords_fail(self):
        bench = Benchmark()
        task = TaskCase(id="t", description="t", expected_keywords=["hello"])
        self.assertFalse(bench._evaluate(task, "world only", 0))

    def test_evaluate_case_insensitive(self):
        bench = Benchmark()
        task = TaskCase(id="t", description="t", expected_keywords=["Hello"])
        self.assertTrue(bench._evaluate(task, "this is hello world", 0))

    def test_evaluate_tool_required(self):
        bench = Benchmark()
        task = TaskCase(id="t", description="t", expected_tool="bash", expected_keywords=[])
        self.assertFalse(bench._evaluate(task, "done", 0))
        self.assertTrue(bench._evaluate(task, "done", 1))

    def test_evaluate_max_iterations(self):
        bench = Benchmark()
        task = TaskCase(id="t", description="t", expected_keywords=[])
        self.assertFalse(bench._evaluate(task, "Max iterations reached.", 5))

    def test_evaluate_no_keywords_no_tool(self):
        bench = Benchmark()
        task = TaskCase(id="t", description="t")
        # 无关键词要求 + 无工具要求 → 只要不是 max iterations 就算通过
        self.assertTrue(bench._evaluate(task, "any response", 0))

    def test_compare_output(self):
        bench = Benchmark()
        r1 = EvalReport(strategy="default", results=[
            TaskResult(task_id="a", strategy="default", success=True,
                       response="ok", duration_s=1.0, tool_calls=2)
        ])
        r2 = EvalReport(strategy="react", results=[
            TaskResult(task_id="a", strategy="react", success=False,
                       response="fail", duration_s=2.0, tool_calls=3)
        ])
        out = bench.compare([r1, r2])
        self.assertIn("default", out)
        self.assertIn("react", out)
        self.assertIn("100.0%", out)
        self.assertIn("0.0%", out)


class TestBenchmarkRun(unittest.TestCase):
    """测试 Benchmark.run() 使用 mock Agent。"""

    @patch("scripts.evaluation.Agent")
    def test_run_single_strategy(self, MockAgent):
        # 配置 mock agent
        mock_instance = MockAgent.return_value
        mock_instance.run.return_value = "The answer is 56877"
        mock_instance._emit = None

        bench = Benchmark()
        # calc 任务有 expected_tool="calculate", mock 不调工具会失败
        # 用一个没有 expected_tool 的任务测试
        bench.tasks = [TaskCase(id="test1", description="test",
                                expected_keywords=["56877"])]
        reports = bench.run(strategies=["default"], task_ids=["test1"], verbose=False)

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].strategy, "default")
        self.assertEqual(len(reports[0].results), 1)
        # 56877 在 response 中 → 通过
        self.assertTrue(reports[0].results[0].success)

    @patch("scripts.evaluation.Agent")
    def test_run_multiple_strategies(self, MockAgent):
        mock_instance = MockAgent.return_value
        mock_instance.run.return_value = "56877"
        mock_instance._emit = None

        bench = Benchmark()
        bench.tasks = [TaskCase(id="test1", description="test",
                                expected_keywords=["56877"])]
        reports = bench.run(
            strategies=["default", "react"],
            task_ids=["test1"],
            verbose=False
        )
        self.assertEqual(len(reports), 2)

    @patch("scripts.evaluation.Agent")
    def test_run_with_error(self, MockAgent):
        mock_instance = MockAgent.return_value
        mock_instance.run.side_effect = Exception("API error")
        mock_instance._emit = None

        bench = Benchmark()
        bench.tasks = [TaskCase(id="test1", description="test",
                                expected_keywords=[])]
        reports = bench.run(strategies=["default"], task_ids=["test1"], verbose=False)

        self.assertFalse(reports[0].results[0].success)
        self.assertIn("API error", reports[0].results[0].error)


if __name__ == "__main__":
    unittest.main()
