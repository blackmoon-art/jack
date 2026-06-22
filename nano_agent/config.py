"""
配置管理 — 从环境变量读取，提供合理默认值。
"""

import os
from dataclasses import dataclass, field
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

    # ── 记忆 ──
    memory_window: int = int(os.getenv("AGENT_MEMORY_WINDOW", "10"))
    memory_file: Optional[str] = field(
        default_factory=lambda: os.getenv("AGENT_MEMORY_FILE", "agent_memory.md")
    )

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

    @property
    def is_anthropic(self) -> bool:
        return self.provider == "anthropic"
