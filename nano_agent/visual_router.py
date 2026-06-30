"""视觉工具智能路由层 — 规则匹配优先，LLM 兜底。

三层匹配策略：
  Layer 1: 精确关键词 → (工具, 参数)     覆盖 ~65% 请求，0 LLM 调用
  Layer 2: 意图推断（动词+名词模式）       覆盖 ~20% 请求，0 LLM 调用
  Layer 3: 返回 None → 交给 LLM 自然推理   覆盖 ~15% 请求，1 LLM 调用

命中后直接构造 tool_call，跳过 LLM 工具选择，节省 ~4000 tokens/次。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("nano_agent.visual_router")


# ── Layer 1: 精确关键词路由表 ──────────────────────────
# 格式: "关键词1|关键词2|..." → (工具名, 参数 dict)
# 匹配优先级按声明顺序（前面的先匹配）

_EXACT_ROUTES: list[tuple[str, str, dict]] = [
    # === generate_chart 子类型 ===
    # 等高线（放最前面，优先级高于其他含"图"的词）
    ("等高线|梯度下降|contour|loss surface|损失函数可视化",
     "generate_chart", {"chart_type": "contour", "data": "X**2+Y**2"}),
    # 波形（放前面，避免被"信号"等其他规则抢）
    ("波形|信号波|waveform|时钟脉冲|clock pulse|voltage wave",
     "generate_chart", {"chart_type": "waveform"}),
    # 回归
    ("回归|拟合|regression|线性回归|最小二乘",
     "generate_chart", {"chart_type": "regression"}),
    # 函数图
    (r"函数图|画函数|函数图像|function plot|画.*y\s*=|画.*f\(x\)",
     "generate_chart", {"chart_type": "function"}),
    # 几何证明
    ("证明.*定理|几何证明|勾股|pythagoras|相似三角形|全等三角形",
     "generate_chart", {"chart_type": "geometry"}),
    # 3D
    ("3d|三维|wireframe|立体图|3d模型",
     "generate_chart", {"chart_type": "wireframe"}),
    # 散点
    ("散点|scatter|相关性图|散点分布",
     "generate_chart", {"chart_type": "scatter"}),
    # 热力
    ("热力图|heatmap|热图",
     "generate_chart", {"chart_type": "heatmap"}),
    # 雷达
    ("雷达图|radar|蜘蛛图|spider chart",
     "generate_chart", {"chart_type": "radar"}),
    # 气泡
    ("气泡图|bubble",
     "generate_chart", {"chart_type": "bubble"}),
    # 面积
    ("面积图|area chart|区域图",
     "generate_chart", {"chart_type": "area"}),
    # 直方
    ("直方图|分布图|histogram|频次分布",
     "generate_chart", {"chart_type": "histogram"}),
    # 曲线
    ("曲线图|平滑曲线|curve|平滑图",
     "generate_chart", {"chart_type": "curve"}),
    # 柱状
    ("柱状|条形图|bar chart|柱形",
     "generate_chart", {"chart_type": "bar"}),
    # 饼图
    ("饼图|占比图|pie|扇形图|比例图",
     "generate_chart", {"chart_type": "pie"}),
    # 折线（放后面，"线"字容易被其他词包含）
    ("折线|折线图|line chart|line plot|走势图|趋势图",
     "generate_chart", {"chart_type": "line"}),

    # === mermaid_chart 子类型 ===
    # 时序图/交互时序（OCC 控制时序等）
    ("时序图|交互时序|sequence diagram|timing diagram|sequenceDiagram|"
     "时序流程|消息交互|组件交互",
     "mermaid_chart", {}),
    # 状态机
    ("状态机|state machine|stateDiagram|状态转换|状态转移",
     "mermaid_chart", {}),
    # 甘特图
    ("甘特图|gantt|进度图|项目排期",
     "mermaid_chart", {}),
    # 思维导图
    ("思维导图|mindmap|脑图|概念图",
     "mermaid_chart", {}),
    # 类图
    ("类图|class diagram|UML 类",
     "mermaid_chart", {}),
    # ER 图
    ("er图|实体关系图|entity relationship|e-r",
     "mermaid_chart", {}),
    # 流程图（放后面，"流程"较宽泛）
    ("流程图|flowchart|流程|工作流|workflow|泳道",
     "mermaid_chart", {}),
    # 架构图
    ("架构图|系统架构|architecture diagram|组件图|系统设计图",
     "mermaid_chart", {}),

    # === 其他画图工具 ===
    # 电路
    ("电路|原理图|schematic|circuit|电路图|接线图|电路设计",
     "draw_circuit", {}),
    # AI 图片
    ("照片|photo|艺术|art|画一只|画个猫|画只|画张|画一幅|"
     "realistic|digital art|油画|水彩|素描|卡通|anime|插画",
     "ai_image", {}),
    # PPT
    ("ppt|幻灯片|slides|演示文稿|presentation|powerpoint",
     "create_ppt", {}),
]


# ── Layer 2: 意图推断（动词+名词组合模式）──────────────
# 当 Layer 1 没命中精确关键词时，分析任务中的动词+名词组合

_INTENT_PATTERNS: list[tuple[str, str, str, dict]] = [
    # (动词模式,        名词模式,           工具名,           参数)
    (r"比较|对比|vs|versus|哪个好|哪个多",
     r"数据|销量|成绩|产量|收入|分数|指标|月份|季度|年份|城市",
     "generate_chart", {"chart_type": "bar"}),

    (r"变化|趋势|走势|增长|下降|波动|涨",
     r"温度|股价|数据|销量|指标|价格|人数|流量|收入",
     "generate_chart", {"chart_type": "line"}),

    (r"分布|占比|比例|构成|份额|百分比",
     r"数据|类型|类别|来源|年龄段|地区|部门|渠道",
     "generate_chart", {"chart_type": "pie"}),

    (r"关系|相关性|关联|影响",
     r"数据|变量|指标|因素|特征",
     "generate_chart", {"chart_type": "scatter"}),

    (r"证明|推导",
     r"定理|几何|勾股|相似|全等|面积|角度",
     "generate_chart", {"chart_type": "geometry"}),

    (r"画出|绘制|可视化|plot|graph",
     r"函数|方程|表达式|公式|y\s*=|f\(x\)|sin|cos|tan|exp|log|sqrt",
     "generate_chart", {"chart_type": "function"}),
]


# ── 统计 ──────────────────────────────────────────────

_stats = {
    "total": 0,        # 总路由请求
    "layer1_hit": 0,   # Layer 1 精确命中
    "layer2_hit": 0,   # Layer 2 意图命中
    "fallback": 0,     # 未命中，交给 LLM
}


def get_stats() -> dict:
    """获取路由统计。"""
    return dict(_stats)


def reset_stats():
    """重置统计（测试用）。"""
    for k in _stats:
        _stats[k] = 0


# ── 公共接口 ──────────────────────────────────────────

def route_visual(task: str) -> Optional[tuple[str, dict]]:
    """主入口：尝试匹配画图意图。

    Args:
        task: 用户任务文本

    Returns:
        (tool_name, params) 命中时返回工具名和参数
        None 未命中，调用方应 fallback 给 LLM 自然推理
    """
    _stats["total"] += 1
    task_lower = task.lower().strip()

    # Layer 1: 精确关键词匹配
    hit = _exact_match(task_lower)
    if hit:
        _stats["layer1_hit"] += 1
        logger.debug(f"[Router] Layer1 hit: '{task[:40]}' → {hit[0]}({hit[1]})")
        return hit

    # Layer 2: 意图推断
    hit = _intent_match(task, task_lower)
    if hit:
        _stats["layer2_hit"] += 1
        logger.debug(f"[Router] Layer2 hit: '{task[:40]}' → {hit[0]}({hit[1]})")
        return hit

    # Layer 3: 兜底
    _stats["fallback"] += 1
    logger.debug(f"[Router] No match, fallback to LLM: '{task[:40]}'")
    return None


def _ascii_word_match(kw: str, text: str) -> bool:
    """Match ASCII keyword, requiring no ASCII letter adjacent.

    Better than \\b for CJK+English mixed text: 'ppt' matches in
    '做个ppt' but 'pie' won't match inside 'empire'.
    """
    pattern = rf"(?<![a-zA-Z]){re.escape(kw)}(?![a-zA-Z])"
    return re.search(pattern, text) is not None


def _exact_match(task_lower: str) -> Optional[tuple[str, dict]]:
    """Layer 1: 精确关键词匹配。英文词加单词边界防子串误匹配。

    关键词分两类：
    - 纯文本（含中文）：子串匹配
    - 正则模式（含 \\, *, +, [, ( 等元字符）：re.search 匹配
    """
    # 硬件信号时序关键词 — 这些场景的"时序图"是信号波形，不是 sequenceDiagram
    _HW_TIMING_KW = re.compile(
        r"时钟|SPI|I2C|UART|CAN|USB|信号|电平|上升沿|下降沿|"
        r"高电平|低电平|电路|OCC|总线|晶振|脉冲|波特|probe|"
        r"oscilloscope|逻辑分析仪|trigger|edge",
        re.IGNORECASE
    )
    # 软件交互时序关键词 — 这些才是 sequenceDiagram
    _SW_SEQ_KW = re.compile(
        r"交互时序|sequence|消息交互|组件交互|用户.*时序|"
        r"API.*时序|登录.*流程|支付.*流程|请求.*响应"
    )
    _REGEX_META = set('\\*+?[](){}|^$.|')
    for keywords, tool_name, params in _EXACT_ROUTES:
        for kw in keywords.split("|"):
            kw = kw.strip()
            if not kw:
                continue
            # 含正则元字符的规则 → 用 re.search
            if any(c in _REGEX_META for c in kw):
                if re.search(kw, task_lower):
                    return tool_name, dict(params)
            # 纯 ASCII 关键词：前后不能紧邻 ASCII 字母
            elif kw.isascii():
                if _ascii_word_match(kw, task_lower):
                    return tool_name, dict(params)
            else:
                # 中文关键词：子串匹配
                if kw.lower() in task_lower:
                    # "时序图"歧义消解：硬件信号 vs 软件交互
                    if kw in ("时序图", "时序", "timing diagram", "timing"):
                        if _HW_TIMING_KW.search(task_lower):
                            # 硬件时序 → 波形图
                            return "generate_chart", {"chart_type": "waveform"}
                        # 无硬件上下文 → 默认 mermaid sequenceDiagram
                    return tool_name, dict(params)
    return None


def _intent_match(task: str, task_lower: str) -> Optional[tuple[str, dict]]:
    """Layer 2: 动词+名词组合模式匹配。"""
    for verb_pat, noun_pat, tool_name, params in _INTENT_PATTERNS:
        if re.search(verb_pat, task_lower) and re.search(noun_pat, task_lower):
            return tool_name, dict(params)
    return None


def is_visual_request(task: str) -> bool:
    """快速判断任务是否是画图请求（比 route_visual 更宽松）。

    用于替代 default.py 中的 _should_force_visual 关键词列表。
    ASCII 关键词使用 _ascii_word_match 防子串误匹配
    （如 'scatter' 不应匹配 'scattering', 'pie' 不应匹配 'empire'）。
    正则规则用 re.search 检测。
    """
    task_lower = task.lower()
    _REGEX_META = set('\\*+?[](){}|^$.|')
    # 含精确路由关键词
    for keywords, _, _ in _EXACT_ROUTES:
        for kw in keywords.split("|"):
            kw = kw.strip()
            if not kw:
                continue
            if any(c in _REGEX_META for c in kw):
                if re.search(kw, task_lower):
                    return True
            elif kw.isascii():
                if _ascii_word_match(kw, task_lower):
                    return True
            else:
                if kw.lower() in task_lower:
                    return True
    # 含意图模式动词
    for verb_pat, _, _, _ in _INTENT_PATTERNS:
        if re.search(verb_pat, task_lower):
            # 还需要含一个画图暗示词
            draw_hints = ("画|图|plot|chart|graph|draw|visual|可视化|展示")
            if re.search(draw_hints, task_lower):
                return True
    return False


# ── 关键词导出（供 default.py auto_keywords 复用）────────

def get_all_visual_keywords() -> tuple[str, ...]:
    """返回所有视觉路由关键词的扁平元组（单一来源，消除重复维护）。"""
    keywords = []
    for kws, _, _ in _EXACT_ROUTES:
        keywords.extend(kw.strip().lower() for kw in kws.split("|") if kw.strip())
    # 补充常见画图动词
    keywords.extend(["画图", "画一", "画个", "画只", "画张", "绘图", "作图", "图表", "生成图"])
    return tuple(dict.fromkeys(keywords))  # 去重保序
