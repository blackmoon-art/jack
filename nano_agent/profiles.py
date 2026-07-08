"""Agent Profiles — 按意图过滤工具集、追加 prompt、推荐策略。

每个 profile 定义:
  - excluded_tools: 需要从 schema 中移除的工具名集合
  - prompt_prefix:  注入 system prompt 的角色前缀
  - default_strategy: 该 profile 的默认策略
"""

from dataclasses import dataclass, field


@dataclass
class AgentProfile:
    name: str
    excluded_tools: set[str] = field(default_factory=set)
    prompt_prefix: str = ""
    default_strategy: str = "default"


# ── Profile 定义 ──────────────────────────────────────

AGENT_PROFILES: dict[str, AgentProfile] = {
    "qa": AgentProfile(
        name="qa",
        excluded_tools={
            # 无代码执行
            "bash",
            # 无文件写入
            "write", "edit",
            # 无电路工具
            "draw_circuit", "draw_logic", "draw_analog_svg",
            "draw_analog_spice", "draw_block", "draw_digital",
            "draw_analog", "design_circuit",
            "design_digital", "simulate_verilog", "synthesize_gates",
            "simulate_spice",
            # 无 PPT/Excel 生成
            "create_ppt", "create_excel",
        },
        prompt_prefix="You are a knowledgeable assistant. "
                       "Answer questions concisely and accurately. "
                       "Do NOT write code or execute commands.",
        default_strategy="default",
    ),
    "code": AgentProfile(
        name="code",
        excluded_tools={
            # 无电路仿真
            "draw_analog_svg", "draw_analog_spice",
            "draw_logic", "draw_block", "draw_digital", "draw_analog",
            "design_circuit", "design_digital",
            "simulate_verilog", "synthesize_gates", "simulate_spice",
        },
        prompt_prefix="You are a coding assistant. "
                       "Write, execute, and debug code. "
                       "Use tools to read, write, and run scripts.",
        default_strategy="auto",
    ),
    "circuit": AgentProfile(
        name="circuit",
        excluded_tools={
            # 只保留电路相关 + 基础工具
            "get_weather", "get_stock", "stock_chart",
            "create_ppt", "create_excel",
            "ai_image",
        },
        prompt_prefix="You are a circuit design assistant. "
                       "Design, simulate, and visualize electronic circuits. "
                       "Use SPICE simulation and schematic rendering tools.",
        default_strategy="meta",
    ),
}
