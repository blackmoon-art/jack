"""
Default 策略 — 标准 agent loop，带流式快速路径。

这是最基础的策略：
  1. 流式调用 LLM，边生成边推送文本（纯知识问答秒出首 token）
  2. 如果 LLM 中途决定调工具 → 无缝切换到 agent_loop
  3. 纯文本回答时，智能检测是否需要画图 → 强制调绘图工具

适合日常简单任务。
"""

import logging
from typing import Optional

from ..config import Config
from ..llm import LLM
from ..tools import ToolRegistry
from .base import BaseStrategy

logger = logging.getLogger("nano_agent.strategies.default")

# ── 画图检测关键词 ──────────────────────────────────────
_DRAW_KW = ('画', '图', 'draw', '生成', '作图', '绘图', 'chart', 'diagram', 'graph')
_EDIT_KW = ('改', '换', '修改', '调整', '重画', '重新', '换成', '改成')
_ADD_KW  = ('加', '添加', '再加', '加上', '补充', '增加')
_QA_KW   = ('天气', '新闻', '计算', '翻译', '搜索', '查', '什么是', '怎么',
            '为什么', 'who', 'what', 'when', 'why', 'how', '解释',
            'hello', 'hi', 'hey', '你好', '谢谢', '再见', '帮助')


class DefaultStrategy(BaseStrategy):
    """默认推理策略 — 优先流式，必要时切 agent_loop，智能画图检测。"""

    uses_orient = False
    default_params = {}
    auto_keywords = ('天气', '气温', '温度', '汇率', '股价', '行情', '大盘', '指数',
                     '新闻', '热搜', '今天', '查询', '查一下', '搜索', '搜一下',
                     '多少', '几度', '几点', '什么时候', '是什么', '什么是',
                     '计算', '换算', '翻译', '帮我', '告诉我',
                     '如何', '怎么', '为什么', '怎样', '攻略', '技巧', '教程',
                     '入门', '推荐', '建议', '画', '生成图', '画图', '绘图',
                     '作图', '生成', '画一只', '画个')
    auto_priority = 0  # 最低优先级，兜底

    def run(self, task: str, agent_loop_fn) -> str:
        """
        执行默认策略：

        1. 流式 LLM 调用（带 tools）
        2. 纯文本 → 边生成边推送，检查是否需要画图
        3. LLM 要调工具 → 中断流式，切到 agent_loop 继续
        """
        logger.info(f"[Default] Task: {task}")
        messages = self.build_messages(task, include_memory=True)
        system_prompt = self._get_system_prompt()
        schemas = self.tools.get_schemas()

        # ── Phase 1: 流式调用 LLM ──
        full_text, tool_calls = self._stream_to_first_decision(
            messages, system_prompt, schemas
        )

        # ── Phase 2: LLM 要调工具 → 切到 agent_loop ──
        if tool_calls:
            assistant_msg = {"role": "assistant", "content": full_text, "tool_calls": [
                self.llm.format_tool_call_for_message(tc) for tc in tool_calls
            ]}
            messages.append(assistant_msg)

            if len(tool_calls) == 1:
                self.execute_tool(tool_calls[0], messages)
            else:
                self._execute_tools_parallel(tool_calls, messages)

            return agent_loop_fn(messages)[0]

        # ── Phase 3: 纯文本回答 — 检查是否需要画图 ──
        if self._should_force_visual(task, self.memory):
            return self._force_visual(task, full_text, messages, agent_loop_fn)

        if not full_text.strip():
            return agent_loop_fn(messages)[0]

        return full_text

    # ── 内部方法 ──────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        """获取 system prompt，优先使用 context 注入的函数。"""
        if self._system_prompt_fn:
            return self._system_prompt_fn()
        # 兜底：简单 prompt（直接 new 策略绕过 Agent 时）
        return (
            "You are Sleeping fox, an AI assistant. Be concise and helpful. "
            "Use tools when needed. When asked to draw/generate charts, "
            "MUST call the appropriate drawing tool."
        )

    def _stream_to_first_decision(self, messages: list, system_prompt: str,
                                  schemas: list) -> tuple[str, Optional[list]]:
        """流式调用 LLM，边生成边推送。返回 (累积文本, tool_calls 或 None)。"""
        full_text = ""
        model = self._model_override

        for chunk in self.llm.chat_stream(
            messages=messages, system=system_prompt,
            tools=schemas, model=model,
        ):
            if isinstance(chunk, dict) and chunk.get("type") == "tool_calls":
                return full_text, chunk["tool_calls"]
            if isinstance(chunk, str):
                full_text += chunk
                self.emit("text", {"text": full_text})

        return full_text, None

    @staticmethod
    def _should_force_visual(task: str, memory=None) -> bool:
        """检测任务是否需要视觉输出（画图/图表）。"""
        task_lower = task.lower()
        is_draw = any(k in task_lower for k in _DRAW_KW)
        is_edit = any(k in task_lower for k in _EDIT_KW)
        is_add  = any(k in task_lower for k in _ADD_KW)
        is_qa   = any(k in task_lower for k in _QA_KW)

        # 上下文：上一轮用户是否主动要求了视觉内容
        prev_visual = False
        if memory:
            msgs = memory.get_window_messages()
            for m in reversed(msgs):
                if m["role"] == "user":
                    prev_visual = any(
                        k in m["content"].lower()
                        for k in _DRAW_KW + _EDIT_KW + _ADD_KW
                    )
                    break

        return is_draw or is_edit or is_add or (not is_qa and prev_visual)

    def _force_visual(self, task: str, full_text: str, messages: list,
                      agent_loop_fn) -> str:
        """强制让 LLM 调用绘图工具。包含代码兜底。"""
        logger.info(f"VISUAL: '{task[:50]}'")
        self.emit("text", {"text": "🔧 正在生成图片..."})

        # 构建上下文
        ctx = ""
        if self.memory:
            for m in self.memory.get_window_messages()[-6:]:
                role = "用户" if m["role"] == "user" else "助手"
                txt = m["content"][:150].split("![")[0].strip()
                if txt:
                    ctx += f"[{role}]: {txt}\n"

        override = (
            "Call a drawing tool NOW. Context:\n{ctx}\n"
            "User said: '{task}'. You MUST call one of: mermaid_chart, "
            "generate_chart, draw_circuit."
        ).format(ctx=ctx.strip(), task=task[:200])

        messages.append({"role": "user", "content": override})
        result, msgs = agent_loop_fn(messages)

        # LLM 还是没调工具 → 代码兜底
        had_tool = any(
            m.get("role") == "assistant" and m.get("tool_calls")
            for m in msgs[-4:]
        )
        if not had_tool:
            try:
                from nano_agent.tools.diagram import Diagram
                d = Diagram(work_dir=self.config.work_dir,
                            charts_dir=self.config.charts_dir)
                img = d.mermaid_chart(
                    f"flowchart LR\n  A[{task[:40]}]-->B[Result]",
                    theme="dark",
                )
                return result + "\n\n" + img
            except Exception:
                pass

        return result

    def _execute_tools_parallel(self, tool_calls: list, messages: list):
        """并行执行多个独立的工具调用。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[int, dict] = {}

        def _run_one(idx: int, tc: dict):
            try:
                results[idx] = self.execute_tool(tc, [])
            except Exception as e:
                logger.warning(f"Parallel tool '{tc.get('name', '?')}' failed: {e}")
                results[idx] = {"name": tc.get("name", "?"), "result": f"Error: {e}", "success": False}

        with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
            futures = [executor.submit(_run_one, i, tc) for i, tc in enumerate(tool_calls)]
            for f in as_completed(futures):
                f.result()

        for i in sorted(results.keys()):
            info = results[i]
            tc = tool_calls[i]
            # 优先用 content（含 Orient 富化），回退到 raw result
            content = str(info.get("content") or info.get("result", ""))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": content,
            })
