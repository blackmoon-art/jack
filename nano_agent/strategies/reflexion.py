"""
Reflexion 策略 — 自我反思 + 失败重试 + 教训学习。

流程:
  1. Attempt:  执行 agent loop 尝试解决问题
  2. Evaluate: 评估结果（智能跳过：明显成功时不调 LLM 评估）
  3. Reflect:  如果失败/不完整，生成反思文本（错在哪、应该怎么改进）
  4. Retry:    将反思 + 上次工具历史加入上下文，重新执行（最多 N 次）
  5. Memory:   反思中的 LESSON 行存入持久记忆，跨任务复用

优化点（vs 原始版本）:
  - 重试时保留上次的 tool 历史（避免重复犯错）
  - 智能评估：结果正常时跳过 evaluate，省 1 次 LLM 调用
  - 早期退出：首次 score≥8 直接返回
  - 历史教训按相关性过滤（不用全加载）
  - 只存储 LESSON 行（精准教训，非整个反思）
  - best_result 兜底（全失败返回最后一次结果，不返回空字符串）
"""

import json
import logging

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry
from .base import BaseStrategy

logger = logging.getLogger("nano_agent.strategies.reflexion")

# 明显成功的信号词
_SUCCESS_SIGNALS = ("here is", "here are", "the answer is", "result:", "总结", "答案是", "结果如下")
# 明显失败的信号词
_FAILURE_SIGNALS = ("error:", "failed", "timeout", "unable to", "错误", "失败", "无法")


class ReflexionStrategy(BaseStrategy):
    """Reflexion 推理策略 — 自我反思 + 智能重试。"""

    uses_orient = True
    default_params = {"max_retries": 3}
    auto_keywords = ('调试', '修复', 'bug', 'fix', 'debug', '出错', '报错',
                     '质量', '审查', 'review', '检查', '验证', '确保')
    auto_priority = 2

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry,
                 max_retries: int = None, **kwargs):
        super().__init__(config, llm, tools, **kwargs)
        self.max_retries = max_retries if max_retries is not None else config.reflexion_max_retries
        self.lesson_memory: list[str] = []  # 精准教训（只有 LESSON 行）
        self._trace_id: int = 0  # 当前轨迹 ID

        # 从持久记忆加载历史教训（按相关性过滤）
        self._load_relevant_lessons()

    # ── 评估 ──────────────────────────────────────────────

    def _needs_evaluation(self, result: str) -> bool:
        """判断是否需要 LLM 评估。结果明显成功或失败时可跳过。"""
        result_lower = result.lower().strip()

        # 结果太短——可能没完成
        if len(result_lower) < 20:
            return True

        # 明显失败信号 → 需要评估（确认失败原因）
        if any(s in result_lower for s in _FAILURE_SIGNALS):
            return True

        # 明显成功信号 + 足够长 → 跳过评估
        if any(s in result_lower for s in _SUCCESS_SIGNALS) and len(result_lower) > 100:
            return False

        # 默认：需要评估
        return True

    def evaluate_result(self, task: str, result: str) -> dict:
        """
        评估执行结果。先快速判断，必要时才调 LLM。

        Returns:
          {"status": "success"|"partial"|"failed",
           "reason": str, "missing": str, "score": int}
        """
        result_lower = result.lower().strip()

        # 快速路径：明显失败
        if any(s in result_lower for s in _FAILURE_SIGNALS):
            return {"status": "failed", "reason": "result contains error signal",
                    "missing": "valid output", "score": 2}

        # 快速路径：明显成功（足够长 + 成功信号词）
        if any(s in result_lower for s in _SUCCESS_SIGNALS) and len(result_lower) > 100:
            return {"status": "success", "reason": "result contains success signals",
                    "missing": "", "score": 8}

        # 需要 LLM 评估
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

    # ── 反思 ──────────────────────────────────────────────

    def _extract_lesson(self, reflection: str) -> str:
        """从反思文本中提取 LESSON 行。

        多格式兼容：LESSON:, **LESSON:**, LESSON :, - LESSON:, ## LESSON
        """
        for line in reflection.split("\n"):
            stripped = line.strip()
            # 去除 markdown 前缀
            clean = stripped.lstrip("-*#>").strip()
            # 匹配各种 LESSON 前缀格式
            if clean.upper().startswith("LESSON:"):
                return clean[7:].strip().strip("**")
            if clean.upper().startswith("LESSON :"):
                return clean[8:].strip().strip("**")
            # 兼容 **LESSON:** 格式
            if clean.startswith("**LESSON"):
                # 移除 **LESSON:**  或 **LESSON**:
                after = clean.split("LESSON", 1)[-1]
                after = after.lstrip("*: ").strip()
                if after:
                    return after
        # 没有明确的 LESSON 行，取最后一行作为教训
        lines = [l.strip() for l in reflection.split("\n") if l.strip()]
        return lines[-1] if lines else reflection[:100]

    def generate_reflection(self, task: str, result: str, eval_result: dict,
                            attempt: int) -> str:
        """生成反思：分析失败原因 + 改进策略。

        返回结构化反思文本，包含 WHAT WENT WRONG / ROOT CAUSE / FIX / LESSON 字段。
        调用方可通过 parse_reflection() 获取结构化字段。
        """
        context = "\n".join(
            f"- Lesson {i+1}: {r}"
            for i, r in enumerate(self.lesson_memory[-5:])
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
        response = self.llm.chat(messages=messages, tools=[], system="",
                                  model=self._model_override)
        return response["text"].strip()

    @staticmethod
    def parse_reflection(reflection: str) -> dict:
        """将反思文本解析为结构化字段。

        Returns:
            {"what_went_wrong": str, "root_cause": str, "fix": str, "lesson": str}
            缺失字段为空字符串。
        """
        import re as _re
        fields = {"what_went_wrong": "", "root_cause": "", "fix": "", "lesson": ""}
        key_map = {
            "WHAT WENT WRONG": "what_went_wrong",
            "ROOT CAUSE": "root_cause",
            "FIX": "fix",
            "LESSON": "lesson",
        }
        pattern = _re.compile(
            r'(WHAT WENT WRONG|ROOT CAUSE|FIX|LESSON)\s*:\s*(.+?)'
            r'(?=\n(?:WHAT WENT WRONG|ROOT CAUSE|FIX|LESSON)\s*:|\Z)',
            _re.DOTALL,
        )
        for m in pattern.finditer(reflection):
            key = key_map.get(m.group(1).strip().upper())
            if key:
                fields[key] = m.group(2).strip()
        return fields

    # ── 主循环 ────────────────────────────────────────────

    def run(self, task: str, agent_loop_fn) -> str:
        """
        执行 Reflexion 策略。

        简单任务（纯推理/知识/计算）直接执行一次，跳过反思循环。
        """
        logger.info(f"{'='*60}")
        logger.info(f"[Reflexion] Task: {task}")
        logger.info(f"{'='*60}")

        # 简单任务短路：执行一次即可
        if self._is_simple_task(task):
            logger.info("[Reflexion] Simple task detected — skipping reflection loop")
            messages = self.build_messages(task, include_memory=True)
            result, _ = agent_loop_fn(messages)
            return result

        # 启动轨迹记录
        self._start_trace(task)

        best_result = ""
        best_score = -1
        all_reflections: list[str] = []
        last_step_messages: list[dict] = []  # 保留上次工具历史
        result = ""  # 初始化，防止 max_retries=0 时未绑定

        for attempt in range(self.max_retries):
            logger.info(f"{'─'*40}")
            logger.info(f"[Attempt {attempt+1}/{self.max_retries}]")

            # 构建消息：包含历史反思 + 上次工具历史
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

            messages = self.build_messages(user_content, include_memory=True, include_long_term=True)

            # #1 优化：重试时保留上次的 tool 历史
            # 让 agent 能看到之前调了什么工具、拿到了什么结果
            if last_step_messages and attempt > 0:
                tool_history = [
                    m for m in last_step_messages
                    if m.get("role") in ("tool", "assistant") and m.get("tool_calls")
                ]
                if tool_history:
                    # 插入到 task 消息之前
                    messages = messages[:-1] + tool_history + [messages[-1]]

            result, step_messages = agent_loop_fn(messages)
            last_step_messages = step_messages

            # #2 优化：智能评估——跳过不必要的 LLM 调用
            if not self._needs_evaluation(result):
                score = 8
                eval_result = {"status": "success", "reason": "skipped (signals OK)",
                               "missing": "", "score": score}
                logger.info(f"[Evaluate] Skipped — result looks successful")
            else:
                eval_result = self.evaluate_result(task, result)
                score = eval_result.get("score", 5)

            logger.info(f"[Evaluate] status={eval_result['status']}, score={score}/10")
            logger.info(f"[Evaluate] reason={eval_result.get('reason', 'N/A')}")

            # 保留最佳结果
            if score > best_score:
                best_score = score
                best_result = result

            # #3 优化：早期退出——首次高分直接返回
            if eval_result["status"] == "success" and score >= 8:
                logger.info(f"[Reflexion] Early exit — high score on attempt {attempt+1}")
                self._save_attempt(attempt, result, eval_result)
                break

            # 成功但中等分数（7分）也接受
            if eval_result["status"] == "success" and score >= 7:
                logger.info(f"[Reflexion] Success! (attempt {attempt+1})")
                self._save_attempt(attempt, result, eval_result)
                break

            # 最后一次不反思
            if attempt == self.max_retries - 1:
                logger.info(f"[Reflexion] Max retries reached. Returning best result.")
                self._save_attempt(attempt, result, eval_result)
                break

            # 失败 → 反思
            logger.info(f"[Reflect] Generating reflection...")
            reflection = self.generate_reflection(task, result, eval_result, attempt)
            parsed = self.parse_reflection(reflection)
            all_reflections.append(reflection)

            # #5 优化：只提取 LESSON 行作为教训
            lesson = parsed["lesson"] or self._extract_lesson(reflection)
            self.lesson_memory.append(lesson)
            self._save_lesson(lesson, trace_id=self._trace_id)
            self._save_attempt(attempt, result, eval_result, reflection=reflection, lesson=lesson)
            logger.info(f"[Reflect] Issue: {parsed.get('what_went_wrong', 'N/A')[:100]}")
            logger.info(f"[Reflect] Fix: {parsed.get('fix', 'N/A')[:100]}")
            logger.info(f"[Reflect] Lesson: {lesson[:200]}")

        # 保存反思教训到持久记忆
        if self.lesson_memory:
            self._save_lessons_to_file(task, all_reflections)

        # #6 优化：best_result 兜底——如果全失败，返回最后一次结果而非空字符串
        if not best_result and result:
            best_result = result

        return best_result

    # ── 历史教训 ──────────────────────────────────────────

    def _load_relevant_lessons(self):
        """从持久记忆加载与当前任务相关的教训。优先从 SQLite 轨迹加载。"""
        if not self.memory:
            return
        try:
            # 优先从 ReflexionTrace 加载
            trace = getattr(self.memory, '_reflexion_trace', None)
            if trace:
                lessons = trace.load_lessons(limit=20)
                if lessons:
                    self.lesson_memory.extend(lessons)
                    logger.info(f"[Reflexion] Loaded {len(lessons)} lessons from trace DB")
                    return

            # Fallback: 从文本文件加载
            content = self.memory.load_reflections()
            if content:
                for line in content.split("\n"):
                    stripped = line.strip()
                    # 委托给 _extract_lesson 统一解析
                    clean = stripped.lstrip("-*#>").strip()
                    if clean.upper().startswith("LESSON:") or clean.startswith("**LESSON"):
                        lesson = self._extract_lesson(stripped)
                        if lesson:
                            self.lesson_memory.append(lesson)
                if self.lesson_memory:
                    logger.info(f"[Reflexion] Loaded {len(self.lesson_memory)} historical lessons")
        except Exception as e:
            logger.warning(f"[Reflexion] Failed to load lessons: {e}")

    def _start_trace(self, task: str):
        """启动轨迹记录。"""
        trace = getattr(self.memory, '_reflexion_trace', None) if self.memory else None
        if trace:
            self._trace_id = trace.start_trace(task)
            if self._trace_id:
                logger.info(f"[Reflexion] Started trace #{self._trace_id}")

    def _save_attempt(self, attempt_num: int, result: str, eval_result: dict,
                      reflection: str = "", lesson: str = ""):
        """保存一次尝试到轨迹。"""
        trace = getattr(self.memory, '_reflexion_trace', None) if self.memory else None
        if trace and self._trace_id:
            trace.save_attempt(self._trace_id, attempt_num + 1, result,
                               eval_result, reflection, lesson)

    def _save_lesson(self, lesson: str, trace_id: int = 0):
        """保存教训到轨迹。"""
        trace = getattr(self.memory, '_reflexion_trace', None) if self.memory else None
        if trace:
            trace.save_lesson(lesson, trace_id)

    def _save_lessons_to_file(self, task: str, all_reflections: list[str]):
        """保存反思教训到持久记忆。复用 Agent 的 Memory 实例。"""
        if not self.memory or not self.memory.reflection_path:
            logger.debug("[Reflexion] reflection_path not configured, skipping file save")
            return
        for reflection in all_reflections:
            lesson = self._extract_lesson(reflection)
            eval_result = {"status": "retry", "score": 0}
            self.memory.save_reflection(task, f"LESSON: {lesson}", eval_result)

    def get_lessons(self) -> list[str]:
        """返回所有已学习的教训。"""
        return list(self.lesson_memory)

    def clear_lessons(self):
        """清除所有已学的教训。"""
        self.lesson_memory.clear()
