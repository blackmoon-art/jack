"""
Tree-of-Thought 策略 — 多路径探索 + 评估 + 选择最优 + 回溯。

流程:
  1. Generate: 生成 N 个候选方案 (breadth)
  2. Lookahead: 对每个候选做前瞻评估 (depth-1 reasoning)
  3. Score: 对每个候选打分 (0-10)，按分数排序
  4. Execute: 执行最高分候选
  5. Evaluate: 评估实际结果
  6. Backtrack: 如果分数不够，回溯执行次优候选
  7. Continue: 直到成功或所有路径耗尽

与普通 agent loop 的区别:
  - 不在每个回合只信任 LLM 的第一个想法
  - 显式生成多个替代方案，比较后选择最优
  - 失败不重试同一思路，而是换一条路

变体实现:
  - 这里实现 "BFS-lite"：生成候选 → 并行浅评估 → 串行深执行
  - 追求实用而非完备性（不实现完整的 DFS + 剪枝）
"""

import json
from typing import Optional

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry


class TreeOfThoughtStrategy:
    """Tree-of-Thought 推理策略 — 多路径探索 + 评估选择 + 回溯。"""

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry,
                 num_candidates: int = 3, score_threshold: int = 6):
        self.config = config
        self.llm = llm
        self.tools = tools
        self.num_candidates = num_candidates     # 每层生成的候选数
        self.score_threshold = score_threshold   # 合格分数阈值
        self._explored_paths: list[dict] = []    # 记录探索路径

    def generate_candidates(self, task: str, context: str = "") -> list[dict]:
        """
        生成 N 个候选方案。

        Returns:
          [{"approach": str, "reasoning": str, "expected_outcome": str}, ...]
        """
        messages = [{
            "role": "user",
            "content": (
                f"Generate {self.num_candidates} different approaches to solve this task. "
                f"Each approach should be a different strategy or angle.\n\n"
                f"Task: {task}\n"
                f"{'Context/constraints: ' + context if context else ''}\n\n"
                f"Return ONLY a JSON object with a 'candidates' array. "
                f"Each candidate has: 'approach' (1 sentence), 'reasoning' (why this might work), "
                f"'expected_outcome' (what success looks like)."
            ),
        }]
        response = self.llm.chat(messages=messages, tools=[], system="")
        text = response["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        try:
            data = json.loads(text)
            candidates = data.get("candidates", [])
            if candidates and isinstance(candidates, list):
                return candidates[:self.num_candidates]
        except json.JSONDecodeError:
            pass
        # Fallback: single approach
        return [{"approach": task, "reasoning": "direct", "expected_outcome": "task completed"}]

    def score_candidates(self, task: str, candidates: list[dict]) -> list[dict]:
        """
        批量评估所有候选方案（一次 LLM 调用替代 N 次串行调用）。

        Returns:
          candidates with 'score', 'confidence', 'risks', 'verdict' fields added.
        """
        if not candidates:
            return candidates

        # 构建候选列表文本
        candidate_text = "\n\n".join(
            f"[{i+1}] {c['approach']}\n    Reasoning: {c.get('reasoning', '')}"
            for i, c in enumerate(candidates)
        )

        messages = [{
            "role": "user",
            "content": (
                f"Evaluate each approach below on a 0-10 scale. Be critical.\n\n"
                f"Task: {task}\n\n"
                f"{candidate_text}\n\n"
                f"Return ONLY a JSON array with one object per candidate (in order):\n"
                f'[{{"score": 0-10, "confidence": 0-10, '
                f'"risks": "potential issues", "verdict": "promising|risky|unlikely"}}, ...]\n'
                f"No markdown, no explanation — just the JSON array."
            ),
        }]
        response = self.llm.chat(messages=messages, tools=[], system="")
        text = response["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]

        # 解析批量评分
        try:
            scores = json.loads(text)
            if isinstance(scores, list):
                for i, c in enumerate(candidates):
                    if i < len(scores) and isinstance(scores[i], dict):
                        s = scores[i]
                        c["score"] = s.get("score", 5)
                        c["confidence"] = s.get("confidence", 5)
                        c["risks"] = s.get("risks", "")
                        c["verdict"] = s.get("verdict", "risky")
                    else:
                        c["score"] = 5
                        c["confidence"] = 5
                        c["risks"] = ""
                        c["verdict"] = "unknown"
        except (json.JSONDecodeError, TypeError):
            # 回退：所有候选默认中分
            for c in candidates:
                c["score"] = 5
                c["confidence"] = 5
                c["risks"] = ""
                c["verdict"] = "unknown"

        for i, c in enumerate(candidates):
            print(f"  [{i+1}] score={c['score']}/10 conf={c['confidence']} "
                  f"verdict={c['verdict']} — {c['approach'][:80]}")

        # 按 score * confidence 排序
        candidates.sort(key=lambda c: c.get("score", 0) * c.get("confidence", 0),
                        reverse=True)
        return candidates

    def evaluate_result(self, task: str, result: str, approach: str) -> dict:
        """评估实际执行结果。"""
        messages = [{
            "role": "user",
            "content": (
                f"Evaluate whether this approach solved the task.\n\n"
                f"Task: {task}\n"
                f"Approach: {approach}\n"
                f"Result: {result[:2000]}\n\n"
                f"Respond with ONLY a JSON object: "
                f'{{"score": <0-10>, "solved": <bool>, '
                f'"reason": "<1 sentence>"}}'
            ),
        }]
        response = self.llm.chat(messages=messages, tools=[], system="")
        text = response["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"score": 5, "solved": True, "reason": "fallback"}

    def run(self, task: str, agent_loop_fn) -> str:
        """
        执行 Tree-of-Thought 策略。

        Args:
            task: 用户任务
            agent_loop_fn: 核心循环 f(messages, exclude_tools) -> (text, messages)
        """
        print(f"\n{'='*60}")
        print(f"[Tree-of-Thought] Task: {task}")
        print(f"{'='*60}")

        # Phase 1: Generate candidates (breadth)
        print(f"\n[ToT:Generate] Creating {self.num_candidates} candidate approaches...")
        candidates = self.generate_candidates(task)
        print(f"[ToT:Generate] Got {len(candidates)} candidates")

        # Phase 2: Score candidates (lookahead evaluation)
        print(f"\n[ToT:Evaluate] Scoring candidates...")
        candidates = self.score_candidates(task, candidates)

        # Phase 3: Execute best-first with backtracking
        best_overall_result = ""
        best_overall_score = -1

        for attempt_idx, candidate in enumerate(candidates):
            if candidate.get("score", 0) < 3:
                print(f"\n[ToT:Skip] Candidate {attempt_idx+1} score too low, skipping.")
                continue

            print(f"\n{'─'*40}")
            print(f"[ToT:Execute] Path {attempt_idx+1}/{len(candidates)} "
                  f"(score={candidate['score']}, conf={candidate['confidence']})")
            print(f"[ToT:Execute] Approach: {candidate['approach']}")

            # 将候选方案转化为执行 prompt
            execution_prompt = (
                f"Task: {task}\n\n"
                f"Recommended approach: {candidate['approach']}\n"
                f"Reasoning: {candidate.get('reasoning', '')}\n"
                f"Risks to avoid: {candidate.get('risks', '')}\n\n"
                f"Execute this approach now."
            )
            messages = [{"role": "user", "content": execution_prompt}]
            result, step_messages = agent_loop_fn(messages)

            # Phase 4: Evaluate result
            eval_result = self.evaluate_result(task, result, candidate["approach"])
            actual_score = eval_result.get("score", 5)
            is_solved = eval_result.get("solved", False)

            print(f"[ToT:Result] score={actual_score}/10, solved={is_solved}")
            print(f"[ToT:Result] {eval_result.get('reason', '')}")

            # 记录路径
            path_record = {
                "approach": candidate["approach"],
                "expected_score": candidate["score"],
                "actual_score": actual_score,
                "solved": is_solved,
                "result": result[:500],
            }
            self._explored_paths.append(path_record)

            # 追踪最佳结果
            if actual_score > best_overall_score:
                best_overall_score = actual_score
                best_overall_result = result

            # 成功 → 结束
            if is_solved and actual_score >= self.score_threshold:
                print(f"[ToT:Done] Task solved via path {attempt_idx+1}!")
                break
            else:
                print(f"[ToT:Backtrack] Score {actual_score} < threshold "
                      f"{self.score_threshold}. Trying next candidate...")

        # Phase 5: Summary
        print(f"\n[ToT:Summary] Explored {len(self._explored_paths)} paths. "
              f"Best score: {best_overall_score}/10")
        return best_overall_result

    def get_explored_paths(self) -> list[dict]:
        """返回本次探索的所有路径记录。"""
        return list(self._explored_paths)

    def clear_paths(self):
        """清除路径记录。"""
        self._explored_paths.clear()
