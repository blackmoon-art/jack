"""
Meta 策略 — 统一推理流水线：分析→选择→执行→反馈→调整。

流程:
  1. 任务分析:    LLM 结构化分析（领域、复杂度、是否需要工具）
  2. 复杂度评估:   1-10 分打分
  3. 选择推理深度: 根据分数选策略 + 参数
  4. 调用工具:     委托给 agent_loop_fn
  5. 失败重试:     自动评估结果，失败则调整策略重试
  6. 反思修复:     提取教训持久化

所有 6 步串联，不中断。
"""

import json
import logging
from typing import Optional

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry
from .base import BaseStrategy

logger = logging.getLogger("nano_agent.strategies.meta")


class MetaStrategy(BaseStrategy):
    """统一推理流水线策略 — 分析→选择→执行→反馈→调整。"""

    uses_orient = True
    default_params = {"max_retries": 3, "auto_upgrade": True}
    auto_keywords = ()  # 不参与关键词匹配，由 LLM 分类显式选择
    auto_priority = 0

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry,
                 max_retries: int = None, auto_upgrade: bool = True, **kwargs):
        super().__init__(config, llm, tools, **kwargs)
        self.max_retries = max_retries if max_retries is not None else config.reflexion_max_retries
        self.auto_upgrade = auto_upgrade

    # ── ① ② 任务分析 + 复杂度评估 ─────────────────────────

    def analyze_task(self, task: str) -> dict:
        """LLM 结构化分析任务。返回领域、复杂度、工具需求、质量要求。"""
        prompt = (
            "Analyze the following task and return ONLY a JSON object:\n\n"
            "{\n"
            '  "domain": "code"|"data"|"search"|"creative"|"knowledge"|"general",\n'
            '  "complexity": 1-10,\n'
            '  "needs_tools": true|false,\n'
            '  "quality_critical": true|false,\n'
            '  "estimated_steps": 1-10,\n'
            '  "reasoning": "brief explanation"\n'
            "}\n\n"
            f"Task: {task}"
        )
        messages = [{"role": "user", "content": prompt}]
        data = self._chat_json(messages)
        if data and isinstance(data, dict) and "complexity" in data:
            return data
        return {
            "domain": "general", "complexity": 5,
            "needs_tools": False, "quality_critical": False,
            "estimated_steps": 1, "reasoning": "fallback"
        }

    # ── ③ 选择推理深度 ────────────────────────────────────

    def select_strategy(self, analysis: dict) -> tuple[str, dict]:
        """根据复杂度评分选择策略和参数。"""
        score = analysis.get("complexity", 5)
        quality = analysis.get("quality_critical", False)
        steps = analysis.get("estimated_steps", 1)

        if quality or score >= 7:
            return "reflexion", {"max_retries": min(self.max_retries, 3)}
        elif steps >= 3 or score >= 5:
            return "plan-execute", {}
        elif analysis.get("domain") in ("creative",):
            return "tree-of-thought", {"num_candidates": 3}
        else:
            return "default", {}

    # ── ⑤ 反馈评估 ────────────────────────────────────────

    def evaluate_result(self, task: str, result: str, analysis: dict) -> dict:
        """LLM 评估执行结果。"""
        prompt = (
            "Evaluate the result of executing this task. Return ONLY a JSON object:\n\n"
            "{\n"
            '  "status": "success"|"partial"|"failed",\n'
            '  "score": 0-10,\n'
            '  "issues": ["..."],\n'
            '  "suggestion": "how to improve"\n'
            "}\n\n"
            f"Task: {task}\n"
            f"Expected complexity: {analysis.get('complexity', '?')}/10\n"
            f"Result: {result[:2000]}"
        )
        messages = [{"role": "user", "content": prompt}]
        data = self._chat_json(messages)
        if data and isinstance(data, dict) and "status" in data:
            return data
        return {"status": "success", "score": 7, "issues": [], "suggestion": ""}

    # ── ⑥ 反思修复 ────────────────────────────────────────

    def extract_lesson(self, task: str, result: str, evaluation: dict) -> str:
        """从失败中提取教训。"""
        if evaluation.get("status") == "success":
            return ""

        prompt = (
            "Based on the failed task, extract a general lesson. "
            "Respond with ONLY one line starting with 'LESSON: '\n\n"
            f"Task: {task}\n"
            f"Issues: {json.dumps(evaluation.get('issues', []))}\n"
            f"Suggestion: {evaluation.get('suggestion', '')}"
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            resp = self.llm.chat(messages=messages, tools=[], system="Be concise.",
                                   model=self._model_override)
            text = resp["text"].strip()
            if "LESSON:" in text:
                return text
        except Exception:
            pass
        return ""

    # ── 主流水线 ──────────────────────────────────────────

    def run(self, task: str, agent_loop_fn) -> str:
        """执行统一推理流水线。"""
        logger.info(f"{'='*60}")
        logger.info(f"[Meta] Task: {task}")
        logger.info(f"{'='*60}")

        # ── ① ② 任务分析 + 复杂度评估 ──
        self.emit("text", {"text": "🔍 正在分析任务..."})
        analysis = self.analyze_task(task)
        logger.info(f"[Meta] Analysis: complexity={analysis['complexity']}/10, "
                    f"domain={analysis['domain']}, quality={analysis['quality_critical']}")

        # ── ③ 选择推理深度 ──
        strategy_name, strategy_params = self.select_strategy(analysis)
        logger.info(f"[Meta] Selected: {strategy_name} {strategy_params}")
        self.emit("text", {"text": f"📊 复杂度: {analysis['complexity']}/10 → 策略: {strategy_name}"})

        # ── ④ ⑤ 执行 + 失败重试 ──
        best_result = ""
        best_score = -1
        current_strategy = strategy_name
        current_params = strategy_params
        last_eval = {"status": "unknown", "score": 0, "issues": [], "suggestion": ""}

        for attempt in range(self.max_retries):
            logger.info(f"[Meta Attempt {attempt+1}/{self.max_retries}] strategy={current_strategy}")

            # 重试时注入前序失败教训
            sub_task = task
            if attempt > 0 and last_eval.get("issues"):
                sub_task = (
                    f"{task}\n\n"
                    f"[Previous attempt scored {best_score}/10. "
                    f"Issues: {json.dumps(last_eval.get('issues', []))}. "
                    f"Suggestion: {last_eval.get('suggestion', '')} "
                    f"Please try a different approach.]"
                )

            # 实例化并执行子策略（而非直接调 agent_loop_fn）
            result = self._dispatch_sub_strategy(
                current_strategy, sub_task, agent_loop_fn, **current_params)
            logger.info(f"[Meta Result] {result[:300]}...")

            # ── ⑤ 反馈评估 ──
            evaluation = self.evaluate_result(task, result, analysis)
            score = evaluation.get("score", 5)
            logger.info(f"[Meta Eval] status={evaluation['status']} score={score}/10")

            # 保留最佳
            if score > best_score:
                best_score = score
                best_result = result
            last_eval = evaluation

            # 成功 → 结束
            if evaluation["status"] == "success" and score >= 7:
                logger.info(f"[Meta] Success on attempt {attempt+1}")
                break

            # 失败 → 尝试升级策略
            if self.auto_upgrade and attempt < self.max_retries - 1:
                old = current_strategy
                current_strategy = self._upgrade_strategy(current_strategy, score)
                if current_strategy != old:
                    _, current_params = self.select_strategy({
                        "complexity": score, "quality_critical": True,
                        "estimated_steps": 3,
                    })
                    logger.info(f"[Meta] Upgraded: {old} → {current_strategy}")
                    self.emit("text", {"text": f"🔄 升级策略: {old} → {current_strategy}"})

        # ── ⑥ 反思修复 ──
        lesson = self.extract_lesson(task, best_result, last_eval)
        if lesson and self.memory:
            try:
                self.memory.save_reflection(task, lesson, last_eval)
                logger.info(f"[Meta Lesson] {lesson[:200]}")
            except Exception:
                pass

        logger.info(f"[Meta] Complete: {analysis['complexity']}/10 → {current_strategy} "
                    f"→ score {best_score}/10")
        return best_result

    def _dispatch_sub_strategy(self, strategy_name: str, task: str,
                                agent_loop_fn, **params) -> str:
        """实例化并运行子策略，替代直接调 agent_loop_fn。"""
        from . import STRATEGY_REGISTRY
        from .context import StrategyContext

        sub_cls = STRATEGY_REGISTRY.get(strategy_name, STRATEGY_REGISTRY["default"])
        # 防止无限递归：Meta → Meta
        if sub_cls is type(self):
            sub_cls = STRATEGY_REGISTRY["default"]

        ctx = StrategyContext(
            config=self.config, llm=self.llm, tools=self.tools,
            memory=self.memory, emit=self._emit,
            execute_tool=self._execute_tool, agent_loop=self._agent_loop,
            orient_fn=self._orient_fn, model_override=self._model_override,
            system_prompt_fn=self._system_prompt_fn,
        )
        kwargs = dict(sub_cls.default_params)
        kwargs.update(params)
        kwargs["memory"] = self.memory
        sub = sub_cls(ctx.config, ctx.llm, ctx.tools, context=ctx, **kwargs)
        return sub.run(task, agent_loop_fn)

    @staticmethod
    def _upgrade_strategy(current: str, score: int) -> str:
        """根据失败分数升级策略。"""
        if score < 3:
            return "reflexion"  # 严重失败 → 反思重试
        if score < 5:
            if current == "default":
                return "react"
            if current == "react":
                return "plan-execute"
        return current  # 不变
