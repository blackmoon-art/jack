"""
Reflexion 策略 — 自我反思 + 失败重试 + 教训学习。

流程:
  1. Attempt:  执行 agent loop 尝试解决问题
  2. Evaluate: LLM 自评结果是否合格
  3. Reflect:  如果失败/不完整，生成反思文本（错在哪、应该怎么改进）
  4. Retry:    将反思加入上下文，重新执行（最多 N 次）
  5. Memory:   反思存入持久记忆，跨任务复用教训

与普通重试的区别:
  - 不是盲目重试，而是先分析失败原因
  - 反思内容是结构化的：cause → fix → next attempt
  - 反思记忆跨任务累积（学到的东西下次能用）
"""

import json
import logging
from typing import Optional

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry
from .base import BaseStrategy

logger = logging.getLogger("nano_agent.strategies.reflexion")


class ReflexionStrategy(BaseStrategy):
    """Reflexion 推理策略 — 自我反思 + 智能重试。"""

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry,
                 max_retries: int = None):
        super().__init__(config, llm, tools)
        self.max_retries = max_retries if max_retries is not None else config.reflexion_max_retries
        self.reflection_memory: list[str] = []  # 当次会话的教训

        # 从文件加载历史教训
        self._load_historical_lessons()

    def evaluate_result(self, task: str, result: str) -> dict:
        """
        评估执行结果。

        Returns:
          {"status": "success"|"partial"|"failed",
           "reason": str,         // 为什么这样判断
           "missing": str,        // 缺少什么 (partial/failed 时)
           "score": int}          // 0-10 评分
        """
        messages = [{
            "role": "user",
            "content": (
                f"Evaluate whether this task was completed successfully.\n\n"
                f"Task: {task}\n\n"
                f"Result: {result[:3000]}\n\n"
                f"Respond with ONLY a JSON object:\n"
                f'{{"status": "success"|"partial"|"failed", '
                f'"reason": "why", "missing": "what is missing or wrong", '
                f'"score": 0-10}}'
            ),
        }]
        data = self._chat_json(messages)
        if data and isinstance(data, dict):
            return data
        return {"status": "success", "reason": "fallback", "missing": "", "score": 5}

    def generate_reflection(self, task: str, result: str, eval_result: dict,
                            attempt: int) -> str:
        """生成反思：分析失败原因 + 改进策略。"""
        context = "\n".join(
            f"- Lesson {i+1}: {r}"
            for i, r in enumerate(self.reflection_memory[-5:])
        )
        messages = [{
            "role": "user",
            "content": (
                f"You attempted to solve a task but the result was unsatisfactory. "
                f"Analyze the failure and propose a concrete improvement.\n\n"
                f"Task: {task}\n"
                f"Your result: {result[:2000]}\n"
                f"Evaluation: status={eval_result.get('status')}, "
                f"reason={eval_result.get('reason')}, "
                f"missing={eval_result.get('missing')}\n"
                f"Attempt: {attempt+1}/{self.max_retries}\n"
                f"{'Previous lessons: ' + context if context else ''}\n\n"
                f"Respond with a concise reflection in this format:\n"
                f"WHAT WENT WRONG: <1 sentence>\n"
                f"ROOT CAUSE: <1 sentence>\n"
                f"FIX: <what to do differently in the next attempt, be specific>\n"
                f"LESSON: <1 sentence general lesson for future tasks>"
            ),
        }]
        response = self.llm.chat(messages=messages, tools=[], system="")
        return response["text"].strip()

    def run(self, task: str, agent_loop_fn) -> str:
        """
        执行 Reflexion 策略。

        Args:
            task: 用户任务
            agent_loop_fn: 核心循环 f(messages, exclude_tools) -> (text, messages)
        """
        logger.info(f"{'='*60}")
        logger.info(f"[Reflexion] Task: {task}")
        logger.info(f"{'='*60}")

        best_result = ""
        best_score = -1
        all_reflections: list[str] = []

        for attempt in range(self.max_retries):
            logger.info(f"{'─'*40}")
            logger.info(f"[Attempt {attempt+1}/{self.max_retries}]")

            # 构建消息（包含历史反思）
            user_content = task
            if all_reflections:
                reflections_text = "\n\n".join(
                    f"### Lesson from previous attempt\n{r}"
                    for r in all_reflections
                )
                user_content = (
                    f"Previous attempts failed. Here are the reflections:\n\n"
                    f"{reflections_text}\n\n"
                    f"---\n\n"
                    f"Now retry the original task. Do NOT repeat the same mistakes:\n"
                    f"{task}"
                )

            messages = self.build_messages(user_content, include_memory=True)
            result, step_messages = agent_loop_fn(messages)

            # 评估
            eval_result = self.evaluate_result(task, result)
            score = eval_result.get("score", 5)

            logger.info(f"[Evaluate] status={eval_result['status']}, score={score}/10")
            logger.info(f"[Evaluate] reason={eval_result.get('reason', 'N/A')}")

            # 保留最佳结果
            if score > best_score:
                best_score = score
                best_result = result

            # 成功 → 结束
            if eval_result["status"] == "success" and score >= 7:
                logger.info(f"[Reflexion] Success! (attempt {attempt+1})")
                break

            # 最后一次不反思
            if attempt == self.max_retries - 1:
                logger.info(f"[Reflexion] Max retries reached. Returning best result.")
                break

            # 失败 → 反思
            logger.info(f"[Reflect] Generating reflection...")
            reflection = self.generate_reflection(task, result, eval_result, attempt)
            all_reflections.append(reflection)
            self.reflection_memory.append(reflection)
            logger.info(f"[Reflect] {reflection[:300]}...")

        # 保存反思教训到持久记忆
        if self.reflection_memory:
            self._save_lessons_to_file(task, all_reflections, best_score)

        return best_result

    def _load_historical_lessons(self):
        """从反思记忆文件加载历史教训。"""
        from ..memory import Memory
        # 临时 Memory 实例读取反思文件
        mem = Memory(file_path=None, reflection_path=self.config.reflection_file)
        content = mem.load_reflections()
        if content:
            # 提取 LESSON 或 Reflection 行
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("**LESSON:**"):
                    lesson = stripped.replace("**LESSON:**", "").strip()
                elif stripped.startswith("**Reflection:**"):
                    lesson = stripped.replace("**Reflection:**", "").strip()
                else:
                    continue
                if lesson:
                    self.reflection_memory.append(lesson)
            if self.reflection_memory:
                logger.info(f"[Reflexion] Loaded {len(self.reflection_memory)} historical lessons")

    def _save_lessons_to_file(self, task: str, all_reflections: list[str], best_score: int):
        """保存反思轨迹到文件。"""
        from ..memory import Memory
        mem = Memory(file_path=None, reflection_path=self.config.reflection_file)
        for reflection in all_reflections:
            eval_result = {"status": "retry", "score": best_score}
            mem.save_reflection(task, reflection, eval_result)

    def get_lessons(self) -> list[str]:
        """返回所有已学习的教训。"""
        return list(self.reflection_memory)

    def clear_lessons(self):
        """清除所有已学的教训。"""
        self.reflection_memory.clear()
