"""LLM 模块测试 — 消息格式化、JSON 清理、重试逻辑。"""

import json
import unittest
from unittest.mock import MagicMock, patch

from nano_agent.llm import LLM, clean_json_response, format_tool_call_for_message
from nano_agent.config import Config


def _make_test_llm():
    """创建测试用 LLM，用 mock provider。"""
    config = Config()
    llm = LLM(config)
    llm._provider_override = "openai"  # 测试用 openai provider
    return llm


class TestCleanJsonResponse(unittest.TestCase):
    """clean_json_response 工具函数。"""

    def test_plain_json(self):
        self.assertEqual(clean_json_response('{"a": 1}'), '{"a": 1}')

    def test_markdown_fenced(self):
        result = clean_json_response('```json\n{"a": 1}\n```')
        self.assertEqual(json.loads(result), {"a": 1})

    def test_markdown_no_lang(self):
        result = clean_json_response('```\n{"a": 1}\n```')
        self.assertEqual(json.loads(result), {"a": 1})

    def test_nested_braces(self):
        """嵌套 JSON 不应被截断。"""
        original = '{"a": {"b": {"c": 1}}}'
        result = clean_json_response(original)
        self.assertEqual(json.loads(result), {"a": {"b": {"c": 1}}})


class TestFormatToolCall(unittest.TestCase):
    """format_tool_call_for_message 工具函数。"""

    def test_basic(self):
        tc = {"id": "call_1", "name": "bash", "arguments": {"command": "ls"}}
        result = format_tool_call_for_message(tc)
        self.assertEqual(result["id"], "call_1")
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "bash")
        args = json.loads(result["function"]["arguments"])
        self.assertEqual(args, {"command": "ls"})

    def test_arguments_as_string(self):
        """arguments 已经是 JSON 字符串时直接保留。"""
        tc = {"id": "c1", "name": "bash", "arguments": '{"command": "pwd"}'}
        result = format_tool_call_for_message(tc)
        self.assertEqual(result["function"]["arguments"], '{"command": "pwd"}')

    def test_missing_id(self):
        tc = {"name": "bash", "arguments": {}}
        result = format_tool_call_for_message(tc)
        self.assertEqual(result["id"], "")


class TestLLMRetry(unittest.TestCase):
    """LLM.chat 重试逻辑。"""

    @patch.object(LLM, '_chat_openai')
    def test_success_no_retry(self, mock_chat):
        mock_chat.return_value = {"text": "ok", "tool_calls": [], "stop_reason": "stop"}
        llm = _make_test_llm()
        result = llm.chat(messages=[], tools=[], system="")
        self.assertEqual(result["text"], "ok")
        self.assertEqual(mock_chat.call_count, 1)

    @patch.object(LLM, '_chat_openai')
    @patch('nano_agent.llm.time.sleep')
    def test_retry_on_429(self, mock_sleep, mock_chat):
        """429 应该触发重试。"""
        error = Exception("rate limit exceeded")
        error.status_code = 429
        mock_chat.side_effect = [error, {"text": "ok", "tool_calls": [], "stop_reason": "stop"}]
        llm = _make_test_llm()
        result = llm.chat(messages=[], tools=[], system="")
        self.assertEqual(result["text"], "ok")
        self.assertEqual(mock_chat.call_count, 2)

    @patch.object(LLM, '_chat_openai')
    @patch('nano_agent.llm.time.sleep')
    def test_retry_on_500(self, mock_sleep, mock_chat):
        """5xx 应该触发重试。"""
        error = Exception("server error")
        error.status_code = 503
        mock_chat.side_effect = [error, {"text": "ok", "tool_calls": [], "stop_reason": "stop"}]
        llm = _make_test_llm()
        result = llm.chat(messages=[], tools=[], system="")
        self.assertEqual(result["text"], "ok")

    @patch.object(LLM, '_chat_openai')
    @patch('nano_agent.llm.time.sleep')
    def test_no_retry_on_400(self, mock_sleep, mock_chat):
        """400 不应该重试。"""
        error = Exception("bad request")
        error.status_code = 400
        mock_chat.side_effect = error
        llm = _make_test_llm()
        with self.assertRaises(Exception):
            llm.chat(messages=[], tools=[], system="")
        self.assertEqual(mock_chat.call_count, 1)

    @patch.object(LLM, '_chat_openai')
    @patch('nano_agent.llm.time.sleep')
    def test_max_3_retries(self, mock_sleep, mock_chat):
        """最多重试 3 次。"""
        error = Exception("timeout")
        error.status_code = 504
        mock_chat.side_effect = error
        llm = _make_test_llm()
        with self.assertRaises(Exception):
            llm.chat(messages=[], tools=[], system="")
        self.assertEqual(mock_chat.call_count, 3)

    @patch.object(LLM, '_chat_openai')
    @patch('nano_agent.llm.time.sleep')
    def test_retry_after_header_respected(self, mock_sleep, mock_chat):
        """429 带 Retry-After header 时用 header 值。"""
        error = Exception("rate limited")
        error.status_code = 429
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "5"}
        error.response = mock_response
        mock_chat.side_effect = [error, {"text": "ok", "tool_calls": [], "stop_reason": "stop"}]
        llm = _make_test_llm()
        llm.chat(messages=[], tools=[], system="")
        mock_sleep.assert_called()


class TestChatJsonWithRetry(unittest.TestCase):
    """chat_json_with_retry 逻辑。"""

    @patch.object(LLM, 'chat')
    def test_valid_json_first_try(self, mock_chat):
        mock_chat.return_value = {"text": '{"key": "value"}'}
        llm = _make_test_llm()
        result = llm.chat_json_with_retry(messages=[])
        self.assertEqual(result, {"key": "value"})

    @patch.object(LLM, 'chat')
    def test_markdown_stripped(self, mock_chat):
        mock_chat.return_value = {"text": '```json\n{"key": "value"}\n```'}
        llm = _make_test_llm()
        result = llm.chat_json_with_retry(messages=[])
        self.assertEqual(result, {"key": "value"})

    @patch.object(LLM, 'chat')
    def test_retry_on_bad_json(self, mock_chat):
        mock_chat.side_effect = [
            {"text": "not json at all"},
            {"text": '{"key": "value"}'},
        ]
        llm = _make_test_llm()
        result = llm.chat_json_with_retry(messages=[], max_retries=1)
        self.assertEqual(result, {"key": "value"})

    @patch.object(LLM, 'chat')
    def test_returns_none_after_max_retries(self, mock_chat):
        mock_chat.return_value = {"text": "not json"}
        llm = _make_test_llm()
        result = llm.chat_json_with_retry(messages=[], max_retries=1)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
