"""
Plan-Execute 策略 — 将任务分解为步骤，逐步执行，动态调整。

流程:
  1. Plan:  LLM 分解任务为 3-5 个步骤
  2. Execute: 按顺序执行每个步骤 (调用 agent loop)
  3. Evaluate: 每步执行后评估结果，失败则修订后续计划
  4. Continue/Replan: 根据评估结果继续或重新规划剩余步骤

与简单 plan 模式的区别:
  - 每步后都有评估 → 失败不盲目继续
  - 上下文在步骤间传递 → 后续步骤知道前面做了什么
  - 支持动态重规划 → 遇到障碍能调整策略
"""

import json
import logging
from typing import Optional

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry

logger = logging.getLogger("nano_agent.strategies.plan_execute")


class PlanExecuteStrategy:
    """Plan-Execute 推理策略。"""

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry):
        self.config = config
        self.llm = llm
        self.tools = tools

    def create_plan(self, task: str) -> list[str]:
        """调用 LLM 将任务分解为有序步骤。"""
        messages = [{
            "role": "user",
            "content": (
                f"Break the following task into 3-5 simple, ordered, actionable steps. "
                f"Each step must be a concrete action that can be executed independently. "
                f"Return ONLY a JSON object with a 'steps' array of strings. "
                f"No markdown, no explanation.\n\nTask: {task}"
            ),
        }]
        response = self.llm.chat(messages=messages, tools=[], system="")
        text = self.llm.clean_json_response(response["text"])
        try:
            plan_data = json.loads(text)
            steps = plan_data.get("steps", [task])
        except json.JSONDecodeError:
            steps = [task]
        return [str(s) for s in steps] if isinstance(steps, list) and steps else [task]

    def evaluate_step(self, task: str, step: str, result: str) -> str:
        """评估单步执行是否成功。"""
        messages = [{
            "role": "user",
            "content": (
                f"Evaluate if the following step was completed successfully.\n\n"
                f"Original task: {task}\n"
                f"Step: {step}\n"
                f"Result: {result[:2000]}\n\n"
                f"Answer ONLY one word: 'success', 'partial', or 'failed'. "
                f"If 'partial' or 'failed', briefly explain why in a second sentence."
            ),
        }]
        response = self.llm.chat(messages=messages, tools=[], system="")
        return response["text"].strip()

    def revise_plan(self, task: str, remaining_steps: list[str], failure_reason: str) -> list[str]:
        """当某步失败时，修订剩余计划。"""
        messages = [{
            "role": "user",
            "content": (
                f"The following plan has encountered a failure. Revise the remaining steps.\n\n"
                f"Task: {task}\n"
                f"Failure: {failure_reason}\n"
                f"Remaining steps: {json.dumps(remaining_steps)}\n\n"
                f"Return ONLY a JSON object with a 'steps' array of revised steps. "
                f"If the task is now impossible, return an empty array."
            ),
        }]
        response = self.llm.chat(messages=messages, tools=[], system="")
        text = self.llm.clean_json_response(response["text"])
        try:
            return json.loads(text).get("steps", remaining_steps)
        except json.JSONDecodeError:
            return remaining_steps

    def run(self, task: str, agent_loop_fn) -> str:
        """
        执行 Plan-Execute 策略。

        Args:
            task: 用户任务
            agent_loop_fn: 核心循环函数 f(messages, exclude_tools) -> (text, messages)
        """
        logger.info(f"{'='*60}")
        logger.info(f"[Plan-Execute] Task: {task}")
        logger.info(f"{'='*60}")

        # Phase 1: Plan
        steps = self.create_plan(task)
        logger.info(f"[Plan] Created {len(steps)} steps:")
        for i, s in enumerate(steps, 1):
            logger.info(f"  {i}. {s}")

        # Phase 2: Execute with evaluation
        results: list[str] = []
        all_messages: list[dict] = []
        step_idx = 0

        while step_idx < len(steps):
            step = steps[step_idx]
            logger.info(f"[Step {step_idx+1}/{len(steps)}] {step}")

            # 执行当前步骤
            step_msg = [{"role": "user", "content": step}]
            step_result, step_messages = agent_loop_fn(step_msg)
            results.append(step_result)
            all_messages.extend(step_messages)
            logger.info(f"[Result] {step_result[:300]}...")

            # Phase 3: Evaluate
            if step_idx < len(steps) - 1 or len(steps) > 1:
                eval_result = self.evaluate_step(task, step, step_result)
                logger.info(f"[Evaluate] {eval_result}")

                if eval_result.lower().startswith("failed"):
                    # Phase 4: Revise plan
                    remaining = steps[step_idx + 1:]
                    logger.info(f"[Revise] Replanning remaining {len(remaining)} steps...")
                    revised = self.revise_plan(task, remaining, eval_result)
                    if not revised:
                        logger.info("[Revise] Task considered impossible, stopping.")
                        break
                    steps = steps[:step_idx + 1] + revised
                    logger.info(f"[Revise] Updated plan ({len(steps)} steps total)")
                elif eval_result.lower().startswith("partial"):
                    # 部分成功，在下一步前插入修正步骤
                    remaining = steps[step_idx + 1:]
                    revised = self.revise_plan(task, remaining, eval_result)
                    if revised:
                        steps = steps[:step_idx + 1] + revised
                        logger.info(f"[Revise] Adjusted remaining steps ({len(steps)} total)")

            step_idx += 1

        # Phase 5: Summarize
        final = "\n\n".join(f"Step {i+1}: {r[:500]}" for i, r in enumerate(results))
        logger.info(f"[Plan-Execute] Complete: {len(results)} steps executed.")
        return final
