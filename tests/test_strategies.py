"""
推理策略单元测试 — 使用 Mock LLM，不调用真实 API。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import unittest
from unittest.mock import MagicMock

from nano_agent.config import Config
from nano_agent.tools import ToolRegistry
from nano_agent.strategies.plan_execute import PlanExecuteStrategy
from nano_agent.strategies.react import ReActStrategy
from nano_agent.strategies.reflexion import ReflexionStrategy
from nano_agent.strategies.tree_of_thought import TreeOfThoughtStrategy


# ── helpers ────────────────────────────────────────────

class MockLLM:
    """
    智能 Mock LLM：按顺序返回预设响应，耗尽后返回默认响应。
    避免 side_effect StopIteration 问题。
    """

    def __init__(self, responses: list[dict] = None):
        self._responses = list(responses) if responses else []
        self._idx = 0
        self._default = {"text": "", "tool_calls": [], "stop_reason": "stop"}
        self.call_count = 0

    def chat(self, messages, tools, system="", model=None):
        self.call_count += 1
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return dict(self._default)

    def chat_json_with_retry(self, messages, max_retries=2, system="",
                              model=None, tools=None):
        """委托给 chat() + clean_json_response()，与 LLM.chat_json_with_retry 行为一致。"""
        for attempt in range(max_retries + 1):
            response = self.chat(messages=messages, tools=tools or [], system=system,
                                  model=model)
            text = self.clean_json_response(response["text"])
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if attempt >= max_retries:
                    return None

    @staticmethod
    def clean_json_response(text: str) -> str:
        """清理 markdown 代码块包裹。"""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        return text.strip()

    @staticmethod
    def format_tool_call_for_message(tc):
        return {
            "id": tc.get("id", ""),
            "type": "function",
            "function": {
                "name": tc.get("name", ""),
                "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
            },
        }


def _make_llm(responses: list[dict] = None):
    """创建一个按顺序返回预设响应的 mock LLM。"""
    return MockLLM(responses)


def _simple_loop(result_text="done"):
    """返回一个总是返回固定结果的 agent_loop_fn。"""
    def fn(messages, exclude_tools=None):
        messages_out = list(messages) + [
            {"role": "assistant", "content": result_text}
        ]
        return result_text, messages_out
    return fn


def _fake_agent_loop(llm, tools, max_steps=10):
    """返回一个模拟 agent_loop_fn，用于 ReAct 测试。
    行为与 Agent._agent_loop 一致：调 LLM → step_callback → 执行工具 → tool_callback → 循环。"""
    def fn(messages, system_prompt="", step_callback=None, tool_callback=None,
           exclude_tools=None, **kwargs):
        schemas = tools.get_schemas()
        for _ in range(max_steps):
            response = llm.chat(messages=messages, tools=schemas, system=system_prompt)
            text = response["text"]
            tool_calls = response["tool_calls"]

            if step_callback:
                early = step_callback(text, tool_calls)
                if early is not None:
                    messages.append({"role": "assistant", "content": text})
                    return early, messages

            if not tool_calls:
                messages.append({"role": "assistant", "content": text})
                return text, messages

            assistant_msg = {"role": "assistant", "content": text,
                             "tool_calls": [llm.format_tool_call_for_message(tc)
                                            for tc in tool_calls]}
            messages.append(assistant_msg)
            for tc in tool_calls:
                obs = tools.execute(tc["name"], tc.get("arguments", {}))
                result_text = str(obs)
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": result_text})
                if tool_callback:
                    tool_callback(tc["name"], tc.get("arguments", {}),
                                  result_text, obs.success)
        return "Max steps reached", messages
    return fn


def _make_config():
    cfg = Config()
    cfg.provider = "openai"
    cfg.model = "test-model"
    cfg.work_dir = "/tmp"
    return cfg


def _plan_json(steps: list[str]) -> dict:
    return {
        "text": json.dumps({"steps": steps}),
        "tool_calls": [],
        "stop_reason": "stop",
    }


def _eval_json(status: str, score: int = None) -> dict:
    s = score if score is not None else (8 if status == "success" else 4)
    return {
        "text": json.dumps({"status": status, "reason": "test", "missing": "", "score": s}),
        "tool_calls": [],
        "stop_reason": "stop",
    }


def _text_response(text: str) -> dict:
    return {"text": text, "tool_calls": [], "stop_reason": "stop"}


# ── Plan-Execute Tests ─────────────────────────────────

class TestPlanExecuteStrategy(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()
        self.tools = ToolRegistry(self.config.work_dir)

    def test_plan_parses_steps_correctly(self):
        llm = _make_llm([_plan_json(["step a", "step b", "step c"])])
        s = PlanExecuteStrategy(self.config, llm, self.tools)
        steps = s.create_plan("do something complex")
        self.assertEqual(steps, ["step a", "step b", "step c"])

    def test_plan_fallback_single_step(self):
        llm = _make_llm([_text_response("not json")])
        s = PlanExecuteStrategy(self.config, llm, self.tools)
        steps = s.create_plan("task")
        self.assertEqual(steps, ["task"])

    def test_evaluate_step_returns_status(self):
        llm = _make_llm([_text_response(json.dumps({"status": "success", "reason": "ok"}))])
        s = PlanExecuteStrategy(self.config, llm, self.tools)
        result = s.evaluate_step("task", "step", "step output")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "success")

    def test_revise_plan_returns_new_steps(self):
        llm = _make_llm([_plan_json(["fixed step x", "fixed step y"])])
        s = PlanExecuteStrategy(self.config, llm, self.tools)
        revised = s.revise_plan("task", ["old step"], "it failed")
        self.assertIn("fixed", revised[0])

    def test_run_successful_plan(self):
        """所有步骤成功，无重规划。"""
        llm = _make_llm([
            _plan_json(["step 1", "step 2"]),
            _text_response("Final answer: step result"),  # LLM 整合最终输出
        ])
        s = PlanExecuteStrategy(self.config, llm, self.tools)
        result = s.run("test task", _simple_loop("step result"))
        self.assertIn("step result", result)

    def test_run_with_failure_triggers_revision(self):
        """某步失败后触发重规划。"""
        # 第一步返回 error，第二步返回 fixed
        call_count = [0]
        def _failing_loop(messages, exclude_tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                text = "Error: step 1 failed"
            else:
                text = "fixed result"
            return text, [{"role": "assistant", "content": text}]

        llm = _make_llm([
            _plan_json(["step 1", "step 2"]),      # 1. create_plan
            _text_response("failed: crashed"),       # 2. evaluate step 1 (error → needs_eval)
            _plan_json(["step 1 retry"]),            # 3. revise_plan
            # step 1 retry → "fixed result" (no error, no eval)
            # step 2 → "fixed result" (no error, no eval)
            _text_response("Final: fixed result"),   # 4. LLM 整合
        ])
        s = PlanExecuteStrategy(self.config, llm, self.tools)
        result = s.run("test task", _failing_loop)
        self.assertIn("fixed", result)


# ── Reflexion Tests ────────────────────────────────────

class TestReflexionStrategy(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()
        self.tools = ToolRegistry(self.config.work_dir)

    def test_evaluate_result_success(self):
        llm = _make_llm([_eval_json("success")])
        s = ReflexionStrategy(self.config, llm, self.tools)
        result = s.evaluate_result("task", "great output")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["score"], 8)

    def test_evaluate_result_failed(self):
        llm = _make_llm([
            {"text": json.dumps({"status": "failed", "reason": "wrong approach",
                                  "missing": "correct answer", "score": 2}),
             "tool_calls": [], "stop_reason": "stop"},
        ])
        s = ReflexionStrategy(self.config, llm, self.tools)
        result = s.evaluate_result("task", "bad output")
        self.assertEqual(result["status"], "failed")

    def test_generate_reflection(self):
        llm = _make_llm([
            _text_response("WHAT WENT WRONG: wrong tool\nROOT CAUSE: missing info\n"
                           "FIX: use grep first\nLESSON: search before write"),
        ])
        s = ReflexionStrategy(self.config, llm, self.tools)
        reflection = s.generate_reflection(
            "task", "bad output",
            {"status": "failed", "reason": "x", "missing": "y", "score": 2},
            0
        )
        self.assertIn("WRONG", reflection)
        self.assertIn("LESSON", reflection)

    def test_run_stops_on_success(self):
        """首次成功即停止。"""
        llm = _make_llm([_eval_json("success")])
        s = ReflexionStrategy(self.config, llm, self.tools, max_retries=3)
        result = s.run("task", _simple_loop("good result"))
        self.assertEqual(result, "good result")
        self.assertEqual(llm.call_count, 1)

    def test_run_retries_on_failure(self):
        """失败后反思并重试。"""
        llm = _make_llm([
            _eval_json("failed"),   # evaluate attempt 1
            _text_response("LESSON: be better"),  # generate_reflection
            _eval_json("success"),  # evaluate attempt 2
        ])
        s = ReflexionStrategy(self.config, llm, self.tools, max_retries=2)
        result = s.run("task", _simple_loop("improved result"))
        self.assertEqual(result, "improved result")
        self.assertGreaterEqual(llm.call_count, 2)

    def test_lessons_accumulate(self):
        """反思教训应累积。"""
        llm = _make_llm([
            _eval_json("failed"),
            _text_response("LESSON: lesson 1"),
            _eval_json("failed"),
            _text_response("LESSON: lesson 2"),
            _eval_json("success"),
        ])
        s = ReflexionStrategy(self.config, llm, self.tools, max_retries=3)
        s.run("task", _simple_loop("done"))
        self.assertGreaterEqual(len(s.get_lessons()), 1)


# ── Tree-of-Thought Tests ──────────────────────────────

class TestTreeOfThoughtStrategy(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()
        self.tools = ToolRegistry(self.config.work_dir)

    def _candidates_json(self, approaches: list[str]) -> dict:
        candidates = [
            {"approach": a, "reasoning": "test", "expected_outcome": "works"}
            for a in approaches
        ]
        return {"text": json.dumps({"candidates": candidates}),
                "tool_calls": [], "stop_reason": "stop"}

    def _batch_score_json(self, scores: list[int]) -> dict:
        """批量评分响应：一次 LLM 调用返回所有候选的评分数组。"""
        arr = [{"score": s, "confidence": 8, "risks": "none",
                "verdict": "promising" if s >= 5 else "unlikely"}
               for s in scores]
        return {
            "text": json.dumps(arr),
            "tool_calls": [], "stop_reason": "stop",
        }

    def _eval_json(self, score: int, solved: bool = True) -> dict:
        return {
            "text": json.dumps({"score": score, "solved": solved, "reason": "test"}),
            "tool_calls": [], "stop_reason": "stop",
        }

    def test_generate_candidates_returns_list(self):
        llm = _make_llm([self._candidates_json(["approach a", "approach b"])])
        s = TreeOfThoughtStrategy(self.config, llm, self.tools, num_candidates=3)
        candidates = s.generate_candidates("task")
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["approach"], "approach a")

    def test_score_candidates_sorts_by_score(self):
        # 批量评分：一次 LLM 调用返回三个候选的分数
        llm = _make_llm([self._batch_score_json([7, 9, 5])])
        s = TreeOfThoughtStrategy(self.config, llm, self.tools)
        candidates = [
            {"approach": "a", "reasoning": ""},
            {"approach": "b", "reasoning": ""},
            {"approach": "c", "reasoning": ""},
        ]
        scored = s.score_candidates("task", candidates)
        # 最高分应排第一
        self.assertEqual(scored[0]["score"], 9)

    def test_run_picks_best_candidate(self):
        """批量评分 → 执行最佳 → 成功。"""
        llm = _make_llm([
            self._candidates_json(["approach x", "approach y"]),
            self._batch_score_json([6, 9]),   # 一次调用返回两个评分
            self._eval_json(9, True),
        ])
        s = TreeOfThoughtStrategy(self.config, llm, self.tools, num_candidates=2)
        result = s.run("task", _simple_loop("best result"))
        self.assertEqual(result, "best result")

    def test_run_backtracks_on_failure(self):
        """最佳候选失败，回溯到次优。"""
        llm = _make_llm([
            self._candidates_json(["path a", "path b"]),
            self._batch_score_json([9, 7]),   # path a=9, path b=7
            self._eval_json(3, False),
            self._eval_json(8, True),
        ])
        s = TreeOfThoughtStrategy(self.config, llm, self.tools, num_candidates=2,
                                  score_threshold=6)
        result = s.run("task", _simple_loop("path b result"))
        self.assertEqual(result, "path b result")

    def test_explored_paths_recorded(self):
        llm = _make_llm([
            self._candidates_json(["only path"]),
            self._batch_score_json([9]),
            self._eval_json(9, True),
        ])
        s = TreeOfThoughtStrategy(self.config, llm, self.tools)
        s.run("task", _simple_loop("done"))
        paths = s.get_explored_paths()
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0]["solved"])

    def test_low_score_candidates_skipped(self):
        """低分候选被跳过。"""
        llm = _make_llm([
            self._candidates_json(["bad", "ok"]),
            self._batch_score_json([2, 8]),   # bad=2 (跳过), ok=8
            self._eval_json(8, True),
        ])
        s = TreeOfThoughtStrategy(self.config, llm, self.tools, num_candidates=2)
        s.run("task", _simple_loop("done"))
        paths = s.get_explored_paths()
        self.assertEqual(len(paths), 1)


# ── ReAct Tests (FC version) ─────────────────────────────

class TestReActStrategy(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()
        self.tools = ToolRegistry(self.config.work_dir)

    # ── Thought / Final Answer extraction ───────────────

    def test_extract_thought_from_content(self):
        s = ReActStrategy(self.config, MagicMock(), self.tools)
        text = "Thought: I need to check the directory\nSome other text"
        self.assertIn("need to check", s._extract_thought(text))

    def test_extract_thought_before_final_answer(self):
        s = ReActStrategy(self.config, MagicMock(), self.tools)
        text = "Thought: I have enough info\nFinal Answer: Done."
        self.assertIn("enough info", s._extract_thought(text))
        self.assertNotIn("Done", s._extract_thought(text))

    def test_extract_final_answer(self):
        s = ReActStrategy(self.config, MagicMock(), self.tools)
        text = "Thought: done\nFinal Answer: The result is 42."
        self.assertEqual(s._extract_final_answer(text), "The result is 42.")

    def test_extract_final_answer_none(self):
        s = ReActStrategy(self.config, MagicMock(), self.tools)
        self.assertIsNone(s._extract_final_answer("Thought: still thinking..."))

    # ── FC-based react loop tests ───────────────────────

    def test_run_with_fc_tool_call(self):
        """FC based ReAct: LLM returns tool_calls + Thought in content."""
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            {
                "text": "Thought: need to list files",
                "tool_calls": [{"id": "t1", "name": "bash",
                                "arguments": {"command": "ls"}}],
                "stop_reason": "tool_calls",
            },
            {
                "text": "Thought: files listed\nFinal Answer: found agent.py",
                "tool_calls": [],
                "stop_reason": "stop",
            },
        ]
        mock_llm.format_tool_call_for_message = MockLLM.format_tool_call_for_message
        s = ReActStrategy(self.config, mock_llm, self.tools, max_steps=5)
        result = s.run("what files?", _fake_agent_loop(mock_llm, self.tools))
        self.assertIn("agent.py", result)

    def test_run_final_answer_no_tools(self):
        """Direct Final Answer, no tool calls."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "text": "Thought: this is simple\nFinal Answer: Hello!",
            "tool_calls": [],
            "stop_reason": "stop",
        }
        s = ReActStrategy(self.config, mock_llm, self.tools, max_steps=5)
        self.assertEqual(s.run("hi", _fake_agent_loop(mock_llm, self.tools)), "Hello!")

    def test_thought_trail_recorded(self):
        mock_llm = MockLLM([{
            "text": "Thought: done\nFinal Answer: done.",
            "tool_calls": [],
            "stop_reason": "stop",
        }])
        s = ReActStrategy(self.config, mock_llm, self.tools, max_steps=5)
        s.run("task", _fake_agent_loop(mock_llm, self.tools))
        trail = s.get_thought_trail()
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["thought"], "done")

    def test_max_steps_reached(self):
        """Always returns tool_calls but never finishes."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "text": "Thought: still going",
            "tool_calls": [{"id": "t1", "name": "bash",
                            "arguments": {"command": "pwd"}}],
            "stop_reason": "tool_calls",
        }
        mock_llm.format_tool_call_for_message = MockLLM.format_tool_call_for_message
        s = ReActStrategy(self.config, mock_llm, self.tools, max_steps=3)
        result = s.run("task", _fake_agent_loop(mock_llm, self.tools, max_steps=3))
        self.assertIn("Max steps", result)
        self.assertEqual(len(s.get_thought_trail()), 3)

    def test_action_and_observation_recorded(self):
        """Tool call and result recorded in thought_trail."""
        mock_llm = MockLLM([
            {
                "text": "Thought: list dir",
                "tool_calls": [{"id": "t1", "name": "bash",
                                "arguments": {"command": "pwd"}}],
                "stop_reason": "tool_calls",
            },
            {
                "text": "Thought: got it\nFinal Answer: /tmp",
                "tool_calls": [],
                "stop_reason": "stop",
            },
        ])
        s = ReActStrategy(self.config, mock_llm, self.tools, max_steps=5)
        s.run("pwd?", _fake_agent_loop(mock_llm, self.tools))
        trail = s.get_thought_trail()
        self.assertEqual(len(trail), 2)
        self.assertIn("actions", trail[0])
        self.assertEqual(trail[0]["actions"][0]["name"], "bash")


if __name__ == "__main__":
    unittest.main()
