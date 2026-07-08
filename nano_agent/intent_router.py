"""Intent Router — 用户意图识别，分发到 QA / Code / Circuit。

两层匹配：
  Layer 1: 关键词规则           覆盖 ~70% 请求，0 LLM 调用
  Layer 2: LLM 分类             覆盖 ~30% 请求，1 LLM 调用

返回: "qa" | "code" | "circuit"
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("nano_agent.intent_router")

# ── Layer 1: 关键词规则 ───────────────────────────────
# 注意：Circuit 和 Code 有重叠关键词时需要按优先级处理。
# 规则：Circuit 优先级 > Code > QA (fallback)

_CIRCUIT_KW_RE = re.compile(
    r"电路|滤波器|放大器|运放|逻辑门|门电路|半加器|全加器|触发器|计数器|"
    r"bode|spice|netlist|schematic|pcb|verilog|"
    r"分频器|译码器|多路复用|寄存器|锁存器|三态|alu|"
    r"振荡器|整流器|稳压器|偏置|分压器|"
    r"低通|高通|带通|带阻|陷波器|频率响应|幅频|相频|"
    r"共射|共集|共基|共源|共栅|cascode|电流镜|差分对|"
    r"sallen|butterworth|chebyshev|bessel|"
    r"rc.*filter|lc.*filter|rlc|"
    r"draw.*circuit|电路图|原理图|框图|接线图",
    re.IGNORECASE,
)

_CODE_KW_RE = re.compile(
    r"写代码|编程|写个|实现.*函数|开发|build|"
    r"写.*脚本|写.*程序|写.*爬虫|写.*工具|"
    r"debug|修复.*bug|重构|优化.*代码|写.*测试|"
    r"python|javascript|typescript|java|golang|rust|"
    r"\bapi\b|后端|前端|数据库|sql|"
    r"\bscript\b|\bcoding\b|编写|代码",
    re.IGNORECASE,
)


def _rule_classify(task: str) -> str | None:
    """Layer 1: 关键词规则匹配。返回 intent 或 None。"""
    # Circuit first (higher priority — "画个电路" not "写代码")
    if _CIRCUIT_KW_RE.search(task):
        return "circuit"
    if _CODE_KW_RE.search(task):
        return "code"
    return None


# ── Layer 2: LLM 分类 ─────────────────────────────────

_INTENT_CLASSIFY_PROMPT = (
    "Classify this user request into exactly one category. "
    "Reply with ONLY one word: qa, code, or circuit.\n\n"
    "- qa: knowledge questions, chat, explanations, analysis, translation, "
    "summarization, learning advice, general conversation\n"
    "- code: programming, coding, debugging, script writing, software development, "
    "algorithm implementation, code review, technical setup\n"
    "- circuit: electronic circuits, filters, amplifiers, logic gates, "
    "PCB design, SPICE simulation, digital logic, analog design\n\n"
    "Request: {task}\nCategory:"
)


def classify(task: str, llm=None) -> str:
    """主入口：返回 'qa' | 'code' | 'circuit'。

    Args:
        task: 用户输入的任务描述
        llm:  LLM 实例（Layer 2 分类时需要）。为 None 时只用规则。

    Returns:
        intent 字符串
    """
    # Layer 1: 规则匹配
    result = _rule_classify(task)
    if result:
        logger.debug(f"[Intent] Layer1 rule: '{task[:40]}' → {result}")
        return result

    # Layer 2: LLM 分类（仅任务足够长时调用，避免短任务浪费 token）
    if llm is not None and len(task.strip()) >= 15:
        try:
            prompt = _INTENT_CLASSIFY_PROMPT.format(task=task)
            resp = llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system="Reply with exactly one word: qa, code, or circuit.",
            )
            text = resp.get("text", "").strip().lower()
            if text in ("qa", "code", "circuit"):
                logger.debug(f"[Intent] Layer2 LLM: '{task[:40]}' → {text}")
                return text
        except Exception:
            logger.warning(f"[Intent] LLM classify failed, fallback to qa")

    # Fallback
    logger.debug(f"[Intent] Fallback: '{task[:40]}' → qa")
    return "qa"
