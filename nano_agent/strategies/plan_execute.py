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
from .base import BaseStrategy

logger = logging.getLogger("nano_agent.strategies.plan_execute")


class PlanExecuteStrategy(BaseStrategy):
    """Plan-Execute 推理策略。"""

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry, **kwargs):
        super().__init__(config, llm, tools, **kwargs)

    def create_plan(self, task: str) -> list[str]:
        """调用 LLM 将任务分解为有序步骤。简单任务返回 1 步。"""
        messages = [{
            "role": "user",
            "content": (
                f"Break the following task into ordered, actionable steps.\n\n"
                f"Rules:\n"
                f"- If the task is simple (single question, single action), return ONLY 1 step.\n"
                f"- If the task is complex (multiple sub-tasks, dependencies), return 3-5 steps.\n"
                f"- Each step must be concrete and independently executable.\n"
                f"- Return ONLY a JSON object with a 'steps' array of strings. No markdown, no explanation.\n\n"
                f"Task: {task}"
            ),
        }]
        plan_data = self._chat_json(messages)
        if plan_data and isinstance(plan_data, dict):
            steps = plan_data.get("steps", [task])
            return [str(s) for s in steps] if isinstance(steps, list) and steps else [task]
        return [task]

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
        response = self.llm.chat(messages=messages, tools=[], system="",
                                  model=self._model_override)
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
        data = self._chat_json(messages)
        if data and isinstance(data, dict):
            return data.get("steps", remaining_steps)
        return remaining_steps

    def run(self, task: str, agent_loop_fn) -> str:
        """
        执行 Plan-Execute 策略。

        改进:
        - 步骤间传递上下文（前面步骤的结果）
        - 最终输出由 LLM 整合，不是拼接
        - 执行过程发送事件（Web UI 可见）
        - 成功的步骤跳过评估，减少 LLM 调用
        """
        logger.info(f"{'='*60}")
        logger.info(f"[Plan-Execute] Task: {task}")
        logger.info(f"{'='*60}")

        # Phase 1: Plan
        steps = self.create_plan(task)
        logger.info(f"[Plan] Created {len(steps)} steps:")
        for i, s in enumerate(steps, 1):
            logger.info(f"  {i}. {s}")

        if self._emit:
            self._emit("text", {"text": f"📋 计划 ({len(steps)} 步):\n" + chr(10).join(f"  {i}. {s}" for i, s in enumerate(steps, 1))})

        # 简单任务短路：1 步直接执行
        if len(steps) == 1:
            logger.info("[Plan] Single step — executing directly")
            result, _ = agent_loop_fn([{"role": "user", "content": task}])
            return result

        # Phase 2: Execute with context passing
        results: list[str] = []
        step_idx = 0

        while step_idx < len(steps):
            step = steps[step_idx]
            logger.info(f"[Step {step_idx+1}/{len(steps)}] {step}")

            # 构建带上下文的消息：让后续步骤知道前面的结果
            context_parts = [f"Original task: {task}"]
            for i, (s, r) in enumerate(zip(steps[:step_idx], results)):
                context_parts.append(f"Step {i+1} ({s}): {r[:500]}")
            context_parts.append(f"\nNow execute Step {step_idx+1}: {step}")
            step_msg = [{"role": "user", "content": "\n".join(context_parts)}]

            step_result, step_messages = agent_loop_fn(step_msg)
            results.append(step_result)
            logger.info(f"[Result] {step_result[:300]}...")

            # Phase 3: Evaluate (只评估非最后一步，且只在可能失败时)
            if step_idx < len(steps) - 1:
                # 快速判断：如果结果包含错误信息，才评估
                needs_eval = (
                    "error" in step_result.lower()[:200]
                    or "failed" in step_result.lower()[:200]
                    or "timeout" in step_result.lower()[:200]
                    or len(step_result.strip()) < 10  # 结果太短可能失败
                )

                if needs_eval:
                    eval_result = self.evaluate_step(task, step, step_result)
                    logger.info(f"[Evaluate] {eval_result}")

                    if eval_result.lower().startswith("failed"):
                        remaining = steps[step_idx + 1:]
                        logger.info(f"[Revise] Replanning {len(remaining)} remaining steps...")
                        revised = self.revise_plan(task, remaining, eval_result)
                        if not revised:
                            logger.info("[Revise] Task impossible, stopping.")
                            break
                        steps = steps[:step_idx + 1] + revised
                        logger.info(f"[Revise] Updated plan ({len(steps)} steps)")
                    elif eval_result.lower().startswith("partial"):
                        remaining = steps[step_idx + 1:]
                        revised = self.revise_plan(task, remaining, eval_result)
                        if revised:
                            steps = steps[:step_idx + 1] + revised
                            logger.info(f"[Revise] Adjusted ({len(steps)} steps)")

            step_idx += 1

        # Phase 5: LLM 整合最终输出（不是拼接）
        summary_msg = [{
            "role": "user",
            "content": (
                f"Based on the following step results, provide a coherent final answer.\n"
                f"Do NOT just repeat the steps. Synthesize the information.\n\n"
                f"Original task: {task}\n\n"
                + "\n".join(f"Step {i+1} result: {r[:1000]}" for i, r in enumerate(results))
            ),
        }]
        response = self.llm.chat(messages=summary_msg, tools=[], system="Be concise and helpful.",
                                  model=self._model_override)
        final = response["text"].strip()
        logger.info(f"[Plan-Execute] Complete: {len(results)} steps, summary generated.")
        return final
