"""
ReAct 策略 — Reasoning + Acting：Function Calling + 显式 Thought。

与旧版的区别:
  - 旧版: tools=[], 纯文本正则解析 Action, 小模型格式不稳定
  - 新版: 传入 tools, 用 native FC 调工具（可靠）, content 中输出 Thought（可见）

流程:
  1. LLM 输出: content="Thought: ..." + tool_calls=[...]
  2. Thought 从 content 提取，Action 从 FC tool_calls 获取（可靠）
  3. Observation 是工具执行结果
  4. 循环回到 Thought，直到 LLM 无 tool_calls 且输出 "Final Answer:"
"""

import json
import logging
from typing import Optional

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry

logger = logging.getLogger("nano_agent.strategies.react")


class ReActStrategy:
    """
    ReAct 推理策略 — FC 驱动的显式思考 + 行动 + 观察循环。

    与 Reflexion / Tree-of-Thought 的区别:
      - ReAct: 直线推理链，Thought 是连续的
      - Reflexion: 有反思回路，失败了回头分析
      - Tree-of-Thought: 多分支并行探索
    """

    def __init__(self, config: Config, llm: LLM, tools: ToolRegistry,
                 max_steps: int = 10):
        self.config = config
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.thought_trail: list[dict] = []

    # ── ReAct 系统提示词 ─────────────────────────────────

    def _react_system_prompt(self) -> str:
        return """You are a reasoning agent that solves tasks step by step.

## YOUR RESPONSE FORMAT

Before using any tool, explain your reasoning in the content field using this format:

Thought: <your reasoning about what you need and why>

Then call the appropriate tool. After seeing the tool result, continue with another Thought.

When you have enough information to fully answer the user, respond with:

Thought: <your reasoning about why you can now answer>
Final Answer: <your complete answer to the user>

## RULES

1. ALWAYS start with "Thought:" in your content — explain what you're thinking BEFORE calling a tool
2. After a tool returns a result, continue with "Thought:" about what the result means
3. Only output "Final Answer:" when you are truly done and have everything you need
4. Be specific in your Thoughts — don't just say "I need to check"
5. If a tool returns an error, think about what went wrong and try a different approach

## EXAMPLE

User: What files are in the current directory?

Thought: The user wants to see the directory contents. I need to list files.
[Then call the bash tool with command="ls -la"]

The system will show: Observation: agent.py, README.md

Thought: I can see two files: agent.py and README.md. I have all the information needed.
Final Answer: The current directory contains 2 files: agent.py and README.md."""

    # ── 解析 ──────────────────────────────────────────────

    def _extract_thought(self, text: str) -> str:
        """从 LLM content 中提取 Thought。"""
        if "Thought:" in text:
            start = text.index("Thought:") + len("Thought:")
            if "Final Answer:" in text:
                end = text.index("Final Answer:")
                return text[start:end].strip()
            return text[start:].strip()
        return text[:300].strip()

    def _extract_final_answer(self, text: str) -> Optional[str]:
        """检查是否有 Final Answer。"""
        if "Final Answer:" in text:
            start = text.index("Final Answer:") + len("Final Answer:")
            return text[start:].strip()
        return None

    # ── 主循环 ──────────────────────────────────────────

    def run(self, task: str, agent_loop_fn) -> str:
        """
        执行 ReAct 循环（FC 版本）。

        工具调用使用 native Function Calling（可靠），
        Thought 从 LLM content 提取（可见）。
        """
        logger.info(f"{'='*60}")
        logger.info(f"[ReAct] Task: {task}")
        logger.info(f"{'='*60}")

        messages = [
            {"role": "system", "content": self._react_system_prompt()},
            {"role": "user", "content": task},
        ]
        self.thought_trail = []
        final_answer = ""
        tool_schemas = self.tools.get_schemas()

        for step in range(1, self.max_steps + 1):
            logger.info(f"{'─'*40}")
            logger.info(f"[ReAct Step {step}]")

            # 调用 LLM（传入 tools，使用 FC）
            response = self.llm.chat(
                messages=messages,
                tools=tool_schemas,
                system="",
            )
            llm_text = response["text"]
            tool_calls = response["tool_calls"]

            # 提取 Thought
            thought = self._extract_thought(llm_text)
            trail_entry: dict = {"step": step, "thought": thought}
            self.thought_trail.append(trail_entry)

            logger.info(f"💭 Thought: {thought[:300]}")

            # 检查 Final Answer
            final = self._extract_final_answer(llm_text)
            if final and not tool_calls:
                final_answer = final
                logger.info(f"✅ Final Answer: {final_answer[:300]}")
                messages.append({"role": "assistant", "content": llm_text})
                break

            # 有 Final Answer 也有 tool_calls → 优先 Final Answer
            if final and tool_calls:
                final_answer = final
                logger.info(f"✅ Final Answer: {final_answer[:300]}")
                messages.append({"role": "assistant", "content": llm_text})
                break

            # 有工具调用 → 执行
            if tool_calls:
                # 追加 assistant 消息（含 tool_calls）
                assistant_msg: dict = {"role": "assistant", "content": llm_text}
                assistant_msg["tool_calls"] = [
                    self.llm.format_tool_call_for_message(tc)
                    for tc in tool_calls
                ]
                messages.append(assistant_msg)

                for tc in tool_calls:
                    name = tc["name"]
                    args = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
                    logger.info(f"🔧 Action: {name}({json.dumps(args, ensure_ascii=False)[:200]})")

                    # 执行工具（使用 ToolRegistry，而非正则解析）
                    result = self.tools.execute(name, args)
                    logger.info(f"📤 Observation: {result[:300]}")

                    trail_entry["action"] = {"name": name, "args": args}
                    trail_entry["observation"] = result[:500]

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": f"Observation: {result}",
                    })

            else:
                # 无工具调用也无 Final Answer — 作为纯文本回复
                final_answer = llm_text
                messages.append({"role": "assistant", "content": llm_text})
                break

        if not final_answer:
            final_answer = "Max steps reached without final answer."
            logger.warning(final_answer)

        logger.info(f"[ReAct] Complete: {len(self.thought_trail)} steps, "
              f"{len([t for t in self.thought_trail if 'action' in t])} actions taken.")

        return final_answer

    def get_thought_trail(self) -> list[dict]:
        return list(self.thought_trail)

    def clear_trail(self):
        self.thought_trail.clear()
