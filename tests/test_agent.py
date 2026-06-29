"""
Agent 核心循环单元测试 — 使用 Mock LLM，不调用真实 API。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import unittest
from unittest.mock import MagicMock, patch

from nano_agent.agent import Agent
from nano_agent.config import Config


class TestAgentLoop(unittest.TestCase):
    def setUp(self):
        # 构造内存态配置 (不读 .env)
        self.config = Config()
        self.config.provider = "openai"
        self.config.model = "test-model"
        self.config.work_dir = "/tmp"
        self.config.max_iterations = 5
        self.config.memory_window = 3
        self.config.memory_file = None
        self.config.rules_dir = None

    def _make_agent(self, mock_llm):
        """创建一个使用 mock LLM 的 Agent。"""
        # mock 需要 clean_json_response 和 format_tool_call_for_message
        mock_llm.clean_json_response = lambda text: text.strip()
        mock_llm.format_tool_call_for_message = lambda tc: {
            "id": tc.get("id", ""),
            "type": "function",
            "function": {
                "name": tc.get("name", ""),
                "arguments": str(tc.get("arguments", {})),
            },
        }
        agent = Agent(config=self.config)
        agent.llm = mock_llm
        return agent

    def test_simple_reply_no_tools(self):
        """LLM 直接回复文本，不调用工具。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "text": "Hello, I am an AI.",
            "tool_calls": [],
            "stop_reason": "stop",
        }

        agent = self._make_agent(mock_llm)
        result = agent.run("hi")

        self.assertEqual(result, "Hello, I am an AI.")
        self.assertEqual(mock_llm.chat.call_count, 1)

    def test_single_tool_call(self):
        """LLM 调用一个工具后结束。"""
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            {
                "text": "Let me check.",
                "tool_calls": [{"id": "tc-1", "name": "bash", "arguments": {"command": "pwd"}}],
                "stop_reason": "tool_calls",
            },
            {
                "text": "Your current directory is /tmp.",
                "tool_calls": [],
                "stop_reason": "stop",
            },
        ]

        agent = self._make_agent(mock_llm)
        result = agent.run("what is my directory?")

        self.assertIn("/tmp", result)
        self.assertEqual(mock_llm.chat.call_count, 2)

    def test_unknown_tool_error_feedback(self):
        """LLM 调用不存在的工具，错误返回给 LLM 让其重试。"""
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            {
                "text": "",
                "tool_calls": [{"id": "tc-1", "name": "nonexistent", "arguments": {}}],
                "stop_reason": "tool_calls",
            },
            {
                "text": "Sorry, I made a mistake. Let me try...",
                "tool_calls": [{"id": "tc-2", "name": "bash", "arguments": {"command": "pwd"}}],
                "stop_reason": "tool_calls",
            },
            {
                "text": "Done.",
                "tool_calls": [],
                "stop_reason": "stop",
            },
        ]

        agent = self._make_agent(mock_llm)
        result = agent.run("test")

        self.assertEqual(result, "Done.")
        self.assertEqual(mock_llm.chat.call_count, 3)

    def test_max_iterations_reached(self):
        """每次调用都返回工具调用，直到达到最大迭代。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = {
            "text": "",
            "tool_calls": [{"id": "tc-1", "name": "bash", "arguments": {"command": "pwd"}}],
            "stop_reason": "tool_calls",
        }

        agent = self._make_agent(mock_llm)
        result = agent.run("loop")

        self.assertIn("max iterations", result.lower())  # 达到最大迭代后返回终止提示
        self.assertEqual(mock_llm.chat.call_count, self.config.max_iterations)

    def test_memory_preserves_context(self):
        """连续两次任务，第二次应该包含第一次的上下文。"""
        captured_messages = []

        def record_chat(messages, tools, system="", model=None):
            captured_messages.append(list(messages))
            return {"text": "OK", "tool_calls": [], "stop_reason": "stop"}

        mock_llm = MagicMock()
        mock_llm.chat.side_effect = record_chat

        agent = self._make_agent(mock_llm)
        agent.run("task one")
        agent.run("task two")

        # task two 的消息应包含 task one 的历史
        self.assertGreaterEqual(len(captured_messages), 2)
        second_call_msgs = captured_messages[1]
        roles = [m["role"] for m in second_call_msgs]
        # 应有 user/assistant 来自 task one，再加 task two 的 user
        self.assertIn("user", roles)

    def test_plan_mode_creates_steps(self):
        """plan-execute 策略：第一次返回 plan，然后逐步执行。"""
        call_count = [0]

        def sequential_chat(messages, tools, system="", model=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "text": '{"steps": ["step a", "step b"]}',
                    "tool_calls": [],
                    "stop_reason": "stop",
                }
            else:
                return {
                    "text": f"executed step {call_count[0]}",
                    "tool_calls": [],
                    "stop_reason": "stop",
                }

        mock_llm = MagicMock()
        mock_llm.chat.side_effect = sequential_chat

        agent = self._make_agent(mock_llm)
        result = agent.run("do complex task", strategy="plan-execute")

        self.assertIn("executed", result)
        self.assertGreaterEqual(call_count[0], 3)  # 1 plan + 2 steps

    def test_system_prompt_includes_rules(self):
        """当 rules_dir 有 .md 文件时，system prompt 应包含规则内容。"""
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as rules_dir:
            rule_file = os.path.join(rules_dir, "test_rule.md")
            with open(rule_file, "w") as f:
                f.write("Always use Python 3.12 features.")

            self.config.rules_dir = rules_dir
            agent = Agent(config=self.config)
            prompt = agent._system_prompt()
            self.assertIn("Python 3.12", prompt)

    def test_system_prompt_empty_when_no_rules(self):
        self.config.rules_dir = "/nonexistent/dir"
        agent = Agent(config=self.config)
        prompt = agent._system_prompt()
        # 只有默认指令，没有规则
        self.assertNotIn("# Rules", prompt)


if __name__ == "__main__":
    unittest.main()
