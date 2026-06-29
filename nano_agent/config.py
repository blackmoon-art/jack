"""
配置管理 — 从环境变量读取，提供合理默认值。
"""

import os
from dataclasses import dataclass, field, replace as _dc_replace
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 加载 .env（项目根目录优先, 其次是 ~/.claude/.env）
for env_path in [Path.cwd() / ".env", Path.home() / ".claude" / ".env"]:
    if env_path.exists():
        load_dotenv(env_path, override=True)


@dataclass
class Config:
    """Agent 全局配置"""

    # ── LLM 后端选择 ──
    provider: str = os.getenv("AGENT_PROVIDER", "anthropic")  # anthropic | openai | deepseek | openrouter

    # ── Anthropic 配置 ──
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    anthropic_base_url: Optional[str] = field(
        default_factory=lambda: os.getenv("ANTHROPIC_BASE_URL")
    )

    # ── OpenAI 兼容配置 (DeepSeek / OpenRouter / Ollama) ──
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
    )
    openai_base_url: str = field(
        default_factory=lambda: os.getenv(
            "OPENAI_BASE_URL",
            os.getenv("DEEPSEEK_BASE_URL", "https://api.openai.com/v1"),
        )
    )

    # ── 模型名称 ──
    model: str = field(
        default_factory=lambda: os.getenv("MODEL_NAME", os.getenv("MODEL_ID", "claude-sonnet-4-6"))
    )

    # ── Agent 行为 ──
    max_iterations: int = int(os.getenv("AGENT_MAX_ITERATIONS", "10"))
    max_tokens: int = int(os.getenv("AGENT_MAX_TOKENS", "8000"))
    bash_timeout: int = int(os.getenv("AGENT_BASH_TIMEOUT", "120"))
    work_dir: str = field(default_factory=lambda: os.getenv("AGENT_WORK_DIR", str(Path.cwd())))
    charts_dir: str = field(default_factory=lambda: os.getenv(
        "AGENT_CHARTS_DIR", str(Path(__file__).parent.parent / "web" / "static" / "charts")
    ))

    # ── 记忆 ──
    memory_window: int = int(os.getenv("AGENT_MEMORY_WINDOW", "10"))
    memory_file: Optional[str] = field(
        default_factory=lambda: os.getenv("AGENT_MEMORY_FILE") or None
    )
    reflection_file: Optional[str] = field(
        default_factory=lambda: os.getenv("AGENT_REFLECTION_FILE") or None
    )
    long_term_db: Optional[str] = field(
        default_factory=lambda: os.getenv("AGENT_LONG_TERM_DB") or None
    )
    reflexion_db: Optional[str] = field(
        default_factory=lambda: os.getenv("AGENT_REFLEXION_DB") or None
    )

    # ── 策略参数 ──
    react_max_steps: int = int(os.getenv("AGENT_REACT_MAX_STEPS", "10"))
    reflexion_max_retries: int = int(os.getenv("AGENT_REFLEXION_MAX_RETRIES", "3"))
    tot_num_candidates: int = int(os.getenv("AGENT_TOT_CANDIDATES", "3"))
    tot_score_threshold: int = int(os.getenv("AGENT_TOT_SCORE_THRESHOLD", "6"))

    # ── Orient 触发阈值 ──
    orient_min_chars: int = int(os.getenv("AGENT_ORIENT_MIN_CHARS", "200"))

    # ── 截断 / 限制 ──
    bash_output_limit: int = int(os.getenv("AGENT_BASH_OUTPUT_LIMIT", "50000"))
    memory_max_lines: int = int(os.getenv("AGENT_MEMORY_MAX_LINES", "200"))
    fetch_max_chars: int = int(os.getenv("AGENT_FETCH_MAX_CHARS", "8000"))

    # ── 自定义规则 / 技能 ──
    rules_dir: Optional[str] = field(
        default_factory=lambda: os.getenv("AGENT_RULES_DIR", ".agent/rules")
    )
    skills_dir: Optional[str] = field(
        default_factory=lambda: os.getenv("AGENT_SKILLS_DIR", ".agent/skills")
    )
    brave_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("BRAVE_SEARCH_API_KEY", "")
    )

    # ── 安全模式 ──
    public_mode: bool = field(
        default_factory=lambda: os.getenv("AGENT_PUBLIC_MODE", "").lower() in ("1", "true", "yes")
    )

    # ── 策略自动选择 ──
    strategy_simple_keywords: str = field(
        default_factory=lambda: os.getenv("AGENT_STRATEGY_SIMPLE_KW",
            "天气,气温,温度,汇率,股价,行情,大盘,指数,新闻,热搜,今天,查询,查一下,搜索,搜一下,"
            "多少,几度,几点,什么时候,是什么,什么是,who,what,when,"
            "计算,换算,翻译,帮我,告诉我,如何,怎么,为什么,怎样,why,how,"
            "攻略,技巧,教程,入门,推荐,建议,画,生成图,画图,绘图,作图,生成,画一只,画个")
    )
    strategy_complex_keywords: str = field(
        default_factory=lambda: os.getenv("AGENT_STRATEGY_COMPLEX_KW",
            "计划,规划,方案,对比,比较,分析报告,调研,多步骤,分步,项目,策划")
    )
    strategy_creative_keywords: str = field(
        default_factory=lambda: os.getenv("AGENT_STRATEGY_CREATIVE_KW",
            "头脑风暴,brainstorm,创意,多种方案,最优")
    )
    strategy_classify_prompt: str = field(
        default_factory=lambda: os.getenv("AGENT_STRATEGY_CLASSIFY_PROMPT",
            "Classify this task into exactly one strategy. Reply with ONLY the strategy name.\n\n"
            "Strategies:\n"
            "- default: simple Q&A, knowledge, calculation, chat\n"
            "- react: needs step-by-step visible reasoning, debugging, audit trail\n"
            "- plan-execute: complex multi-step task, project, report, analysis\n"
            "- reflexion: quality-critical, needs self-review, error-prone task\n"
            "- tree-of-thought: multiple valid approaches, creative brainstorming, optimization")
    )

    @property
    def is_anthropic(self) -> bool:
        return self.provider in ("anthropic", "claude")

    def with_overrides(self, **changes) -> "Config":
        """返回修改了指定字段的新 Config 实例（原始不变）。

        per-session 隔离用: session_config = config.with_overrides(work_dir=session_dir)
        """
        return _dc_replace(self, **changes)
