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

# ── 画图检测关键词（单一来源）──────────────────────────────
# 注意：避免单字 '画'（匹配 '动画','漫画','画面'）和 '图'（匹配 '图书馆'）
# 英文也用词组避免子串匹配（'draw' → 'drawer', 'graph' → 'paragraph'）
# auto_keywords 也引用这份列表，消除重复维护
_DRAW_KW = ('画图', '画一', '画个', '画只', '画张', '绘图', '作图', '图表', '生成图',
            '画流程', '画个流', '画一个', '画一幅',
            'draw a', 'draw an', 'draw it', 'drawing',
            'chart', 'diagram', 'graph', 'plot ',
            '画架', '画饼', '画曲', '画折', '画柱', '画散',
            '画架构', '画结构', '画系统', '画组件')
_EDIT_KW = ('重画', '重新画', '改图', '换图', '修改图', '调整图',
            '换成图', '改成图')
_ADD_KW  = ('加个图', '添加图', '再画')
_QA_KW   = ('天气', '新闻', '计算', '翻译', '搜索', '查', '什么是', '怎么',
            '为什么', 'who', 'what', 'when', 'why', 'how', '解释',
            'hello', 'hi', 'hey', '你好', '谢谢', '再见', '帮助')

# 所有视觉关键词合并，供 _should_force_visual 和 auto_keywords 复用
_VISUAL_KW = _DRAW_KW + _EDIT_KW + _ADD_KW

# 视觉工具名称前缀 — 用于动态匹配工具是否属于「画图类」
_VISUAL_TOOL_PREFIXES = (
    "mermaid", "generate_", "chart", "draw", "circuit", "diagram", "ai_image",
    "stock_chart",
)


class DefaultStrategy(BaseStrategy):
    """默认推理策略 — 优先流式，必要时切 agent_loop，智能画图检测。"""

    uses_orient = False
    default_params = {}
    auto_keywords = (
        # 画图关键词直接复用 _VISUAL_KW，不再重复维护
        *_VISUAL_KW,
        # QA / 日常关键词
        '天气', '气温', '温度', '汇率', '股价', '行情', '大盘', '指数',
        '新闻', '热搜', '今天', '查询', '查一下', '搜索', '搜一下',
        '多少', '几度', '几点', '什么时候', '是什么', '什么是',
        '计算', '换算', '翻译', '帮我', '告诉我',
        '如何', '怎么', '为什么', '怎样', '攻略', '技巧', '教程',
        '入门', '推荐', '建议',
    )
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
        """检测任务是否需要视觉输出（画图/图表）。

        优化：仅当前任务含视觉关键词时才触发，
        不再因上一轮的视觉请求误判当前非视觉任务。
        """
        task_lower = task.lower()
        is_visual = any(k in task_lower for k in _VISUAL_KW)

        if is_visual:
            return True

        # 仅在当前任务含“修改/编辑/调整”等词汇且上一轮是视觉任务时，
        # 才认为这是对已有图表的后续编辑
        edit_keywords = ("修改", "编辑", "调整", "重画", "改", "update", "edit", "modify", "redo", "rerun")
        is_edit = any(k in task_lower for k in edit_keywords)

        if is_edit and memory:
            msgs = memory.get_window_messages()
            for m in reversed(msgs):
                if m["role"] == "user":
                    prev_visual = any(
                        k in m["content"].lower()
                        for k in _VISUAL_KW
                    )
                    if prev_visual:
                        return True
                    break

        return False

    def _get_visual_tool_names(self) -> set[str]:
        """动态从 ToolRegistry 获取视觉工具名，不再硬编码。"""
        names = set()
        for name in self.tools._tools:
            if name.startswith(_VISUAL_TOOL_PREFIXES):
                names.add(name)
        return names

    def _force_visual(self, task: str, full_text: str, messages: list,
                      agent_loop_fn) -> str:
        """强制让 LLM 调用绘图工具。包含代码兜底。"""
        logger.info(f"VISUAL: '{task[:50]}'")
        self.emit("text", {"text": "🔧 正在生成图片..."})

        # 动态获取当前可用的视觉工具
        visual_tools = self._get_visual_tool_names()
        tool_hint = ", ".join(sorted(visual_tools)) if visual_tools else "generate_chart, mermaid_chart"

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
            "User said: '{task}'. You MUST call one of: {tools}."
        ).format(ctx=ctx.strip(), task=task[:200], tools=tool_hint)

        # step_callback: 画图工具调用后下一次 LLM 响应时终止循环，防止画两次
        _draw_done = [False]

        def _visual_step(text: str, tool_calls: list) -> str | None:
            if _draw_done[0]:
                return text  # 已经画过了，终止循环
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "")
                    if name in visual_tools:
                        _draw_done[0] = True
                        break
            return None  # 继续循环

        messages.append({"role": "user", "content": override})
        result, msgs = agent_loop_fn(messages, step_callback=_visual_step)

        # 兜底：agent_loop 可能因 max_iterations 或空响应返回空字符串
        if not result or not result.strip():
            result = (
                "抱歉，图片生成未能完成。请尝试更具体的描述，"
                "或直接指定图表类型（如折线图、饼图、流程图等）。"
            )
        return result

    def _execute_tools_parallel(self, tool_calls: list, messages: list):
        """并行执行多个独立的工具调用。

        委托给 BaseStrategy.execute_tools_parallel，避免代码重复。
        """
        self.execute_tools_parallel(tool_calls, messages)
