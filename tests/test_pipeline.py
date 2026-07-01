"""
Strategy Context 和 Pipeline State 测试 — 跨策略通信 + 线程安全。
"""

import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nano_agent.strategies.context import StrategyContext
from nano_agent.config import Config
from nano_agent.tools import ToolRegistry


class TestStrategyContextDefaults(unittest.TestCase):
    """StrategyContext 默认值和字段。"""

    def setUp(self):
        self.config = Config()
        self.tools = ToolRegistry("/tmp")

    def test_all_fields_defaultable(self):
        ctx = StrategyContext(config=self.config, llm=None, tools=self.tools)
        self.assertIsNotNone(ctx.config)
        self.assertIsNotNone(ctx.tools)
        self.assertIsNone(ctx.llm)
        self.assertIsNone(ctx.memory)
        self.assertIsNone(ctx.emit)
        self.assertIsNone(ctx.execute_tool)
        self.assertIsNone(ctx.agent_loop)

    def test_pipeline_state_defaults_to_empty_dict(self):
        ctx = StrategyContext(config=self.config, llm=None, tools=self.tools)
        self.assertEqual(ctx.pipeline_state, {})

    def test_pipeline_lock_is_threading_lock(self):
        ctx = StrategyContext(config=self.config, llm=None, tools=self.tools)
        self.assertIsInstance(ctx.pipeline_lock, type(threading.Lock()))

    def test_optional_fields_accept_none(self):
        ctx = StrategyContext(
            config=self.config, llm=None, tools=self.tools,
            memory=None, emit=None, execute_tool=None,
            agent_loop=None, orient_fn=None,
            model_override=None, system_prompt_fn=None,
        )
        self.assertIsNone(ctx.memory)
        self.assertIsNone(ctx.model_override)


class TestPipelineStateSharing(unittest.TestCase):
    """跨策略 pipeline_state 共享测试。"""

    def setUp(self):
        self.config = Config()
        self.tools = ToolRegistry("/tmp")

    def test_same_dict_reference(self):
        """多个 StrategyContext 共享 pipeline_state 时指向同一个 dict。"""
        shared_state = {"tot": {"candidates": []}}
        ctx1 = StrategyContext(
            config=self.config, llm=None, tools=self.tools,
            pipeline_state=shared_state,
        )
        ctx2 = StrategyContext(
            config=self.config, llm=None, tools=self.tools,
            pipeline_state=shared_state,
        )
        # 通过 ctx1 写入
        ctx1.pipeline_state["tot"]["candidates"].append({"approach": "A", "score": 9})
        # ctx2 能看到
        self.assertEqual(len(ctx2.pipeline_state["tot"]["candidates"]), 1)

    def test_independent_states_default(self):
        """没有显式共享时，每个 Context 有独立的 pipeline_state。"""
        ctx1 = StrategyContext(config=self.config, llm=None, tools=self.tools)
        ctx2 = StrategyContext(config=self.config, llm=None, tools=self.tools)

        ctx1.pipeline_state["tot"] = {"candidates": [{"score": 9}]}
        self.assertNotIn("tot", ctx2.pipeline_state)

    def test_tot_plan_reflexion_keys(self):
        """pipeline_state 按约定写入 ToT/Plan/Reflexion 数据。"""
        ctx = StrategyContext(config=self.config, llm=None, tools=self.tools)

        # ToT 写入
        with ctx.pipeline_lock:
            ctx.pipeline_state["tot"] = {
                "candidates": [{"approach": "A", "score": 9}],
                "best_index": 0,
            }
        # Plan 写入
        with ctx.pipeline_lock:
            ctx.pipeline_state["plan"] = {
                "steps": [{"step": "s1", "result": "ok"}],
                "completed_steps": 1,
            }
        # Reflexion 写入
        with ctx.pipeline_lock:
            ctx.pipeline_state["reflexion"] = {
                "lessons": ["always validate"],
            }

        self.assertIn("tot", ctx.pipeline_state)
        self.assertIn("plan", ctx.pipeline_state)
        self.assertIn("reflexion", ctx.pipeline_state)
        self.assertEqual(ctx.pipeline_state["tot"]["candidates"][0]["score"], 9)

    def test_meta_key_accumulates_attempts(self):
        """Meta 策略的 attempts 列表累加。"""
        ctx = StrategyContext(config=self.config, llm=None, tools=self.tools)
        ctx.pipeline_state["meta"] = {"attempts": []}

        for i in range(3):
            with ctx.pipeline_lock:
                ctx.pipeline_state["meta"]["attempts"].append({
                    "strategy": "react" if i < 2 else "reflexion",
                    "score": 4 + i * 2,
                })

        self.assertEqual(len(ctx.pipeline_state["meta"]["attempts"]), 3)
        self.assertEqual(
            ctx.pipeline_state["meta"]["attempts"][-1]["strategy"],
            "reflexion",
        )


class TestPipelineLockThreadSafety(unittest.TestCase):
    """pipeline_lock 线程安全测试。"""

    def setUp(self):
        self.config = Config()
        self.tools = ToolRegistry("/tmp")

    def test_concurrent_pipeline_writes_safe(self):
        """并发写入 pipeline_state 不会产生竞争条件。"""
        shared_state = {"tot": {"candidates": []}}
        ctx = StrategyContext(
            config=self.config, llm=None, tools=self.tools,
            pipeline_state=shared_state,
        )
        errors = []

        def writer(thread_id):
            try:
                for i in range(50):
                    with ctx.pipeline_lock:
                        ctx.pipeline_state["tot"]["candidates"].append({
                            "approach": f"thread_{thread_id}_approach_{i}",
                            "score": i % 10,
                        })
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(ctx.pipeline_state["tot"]["candidates"]), 200)  # 4 × 50


if __name__ == "__main__":
    unittest.main()
