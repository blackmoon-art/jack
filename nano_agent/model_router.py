"""Model Router — 按任务复杂度和 profile 选择模型。

复杂度评估:
  - simple: 短任务（<30字），无技术关键词
  - complex: 长任务或包含技术关键词

模型配置:
  MODEL_QA_SIMPLE   — QA 简单任务（默认空：用全局 model）
  MODEL_QA_COMPLEX  — QA 复杂任务
  MODEL_CODE_SIMPLE — Code 简单任务
  MODEL_CODE_COMPLEX— Code 复杂任务
  MODEL_CIRCUIT     — Circuit 任务（不分简单/复杂，都需要精确推理）
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("nano_agent.model_router")

# 技术/复杂关键词 — 用于判断 QA 是否为复杂问题
_COMPLEX_KW_RE = re.compile(
    r"深入|详细|全面|分析|报告|总结|归纳|比较|对比|"
    r"原理|机制|源码|底层|实现|架构|设计|"
    r"数学|物理|化学|经济|金融|法律|医学|"
    r"论文|研究|文献|综述",
)


def _is_complex(task: str) -> bool:
    """简单启发式：长任务或含复杂关键词为 complex。"""
    if len(task) > 80:
        return True
    return bool(_COMPLEX_KW_RE.search(task))


def select(task: str, profile: str) -> str | None:
    """根据任务和 profile 选择模型。

    Returns:
        模型名（str），或 None（表示用默认模型）。
    """
    is_complex = _is_complex(task)

    env_key = None
    if profile == "qa":
        env_key = "MODEL_QA_COMPLEX" if is_complex else "MODEL_QA_SIMPLE"
    elif profile == "code":
        env_key = "MODEL_CODE_COMPLEX" if is_complex else "MODEL_CODE_SIMPLE"
    elif profile == "circuit":
        env_key = "MODEL_CIRCUIT"

    if env_key:
        model = os.environ.get(env_key, "")
        if model:
            logger.debug(f"[Model] {profile} {'complex' if is_complex else 'simple'} → {model}")
            return model

    return None  # 用默认模型
