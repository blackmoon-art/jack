"""
配置管理 — 从环境变量读取，提供合理默认值。
"""

import os
import threading
from dataclasses import dataclass, field, replace as _dc_replace
from pathlib import Path
from typing import Optional

_dotenv_loaded = False
_dotenv_lock = threading.Lock()
_config_instance = None  # Config 单例缓存（延迟初始化，避免前向引用）


def _ensure_dotenv():
    """延迟加载 .env，避免 import 时文件 I/O 副作用。仅首次调用时执行。线程安全。"""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    with _dotenv_lock:
        if _dotenv_loaded:  # double-check inside lock
            return
        _dotenv_loaded = True
        from dotenv import load_dotenv
        for env_path in [Path.cwd() / ".env", Path.home() / ".claude" / ".env"]:
            if env_path.exists():
                load_dotenv(env_path, override=True)


def _env(key: str, default: str = "") -> str:
    """读取环境变量，首次调用时自动加载 .env。"""
    _ensure_dotenv()
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    return int(_env(key, str(default)))


def _env_bool(key: str) -> bool:
    return _env(key, "").lower() in ("1", "true", "yes")


@dataclass
class Config:
    """Agent 全局配置"""

    # ── LLM 后端选择 ──
    provider: str = field(default_factory=lambda: _env("AGENT_PROVIDER", "anthropic"))

    # ── Anthropic 配置 ──
    anthropic_api_key: str = field(
        default_factory=lambda: _env("ANTHROPIC_API_KEY", "")
    )
    anthropic_base_url: Optional[str] = field(
        default_factory=lambda: os.getenv("ANTHROPIC_BASE_URL") or None
    )

    # ── OpenAI 兼容配置 (DeepSeek / OpenRouter / Ollama) ──
    openai_api_key: str = field(
        default_factory=lambda: _env("OPENAI_API_KEY", _env("DEEPSEEK_API_KEY", ""))
    )
    openai_base_url: str = field(
        default_factory=lambda: os.getenv(
            "OPENAI_BASE_URL",
            os.getenv("DEEPSEEK_BASE_URL", "https://api.openai.com/v1"),
        )
    )

    # ── 模型名称 ──
    model: str = field(
        default_factory=lambda: _env("MODEL_NAME", _env("MODEL_ID", "claude-sonnet-4-6"))
    )

    # ── Agent 行为 ──
    max_iterations: int = field(default_factory=lambda: _env_int("AGENT_MAX_ITERATIONS", 10))
    max_tokens: int = field(default_factory=lambda: _env_int("AGENT_MAX_TOKENS", 8000))
    bash_timeout: int = field(default_factory=lambda: _env_int("AGENT_BASH_TIMEOUT", 120))
    work_dir: str = field(default_factory=lambda: _env("AGENT_WORK_DIR", str(Path.cwd())))
    charts_dir: str = field(default_factory=lambda: _env(
        "AGENT_CHARTS_DIR", str(Path(__file__).parent.parent / "web" / "static" / "charts")
    ))

    # ── 记忆 ──
    memory_window: int = field(default_factory=lambda: _env_int("AGENT_MEMORY_WINDOW", 10))
    memory_file: Optional[str] = field(
        default_factory=lambda: _env("AGENT_MEMORY_FILE") or None
    )
    reflection_file: Optional[str] = field(
        default_factory=lambda: _env("AGENT_REFLECTION_FILE") or None
    )
    long_term_db: Optional[str] = field(
        default_factory=lambda: _env("AGENT_LONG_TERM_DB") or None
    )
    reflexion_db: Optional[str] = field(
        default_factory=lambda: _env("AGENT_REFLEXION_DB") or None
    )

    # ── 策略参数 ──
    react_max_steps: int = field(default_factory=lambda: _env_int("AGENT_REACT_MAX_STEPS", 10))
    reflexion_max_retries: int = field(default_factory=lambda: _env_int("AGENT_REFLEXION_MAX_RETRIES", 3))
    tot_num_candidates: int = field(default_factory=lambda: _env_int("AGENT_TOT_CANDIDATES", 3))
    tot_score_threshold: int = field(default_factory=lambda: _env_int("AGENT_TOT_SCORE_THRESHOLD", 6))

    # ── Orient 触发阈值 ──
    orient_min_chars: int = field(default_factory=lambda: _env_int("AGENT_ORIENT_MIN_CHARS", 200))

    # ── 截断 / 限制 ──
    bash_output_limit: int = field(default_factory=lambda: _env_int("AGENT_BASH_OUTPUT_LIMIT", 50000))
    memory_max_lines: int = field(default_factory=lambda: _env_int("AGENT_MEMORY_MAX_LINES", 200))
    fetch_max_chars: int = field(default_factory=lambda: _env_int("AGENT_FETCH_MAX_CHARS", 8000))

    # ── 自定义规则 / 技能 ──
    rules_dir: Optional[str] = field(
        default_factory=lambda: _env("AGENT_RULES_DIR", ".agent/rules")
    )
    skills_dir: Optional[str] = field(
        default_factory=lambda: _env("AGENT_SKILLS_DIR", ".agent/skills")
    )
    brave_api_key: Optional[str] = field(
        default_factory=lambda: _env("BRAVE_SEARCH_API_KEY", "")
    )

    # ── 安全模式 ──
    public_mode: bool = field(default_factory=lambda: _env_bool("AGENT_PUBLIC_MODE"))

    # NOTE: 策略自动选择的关键词和 classify prompt 由各策略类的
    # auto_keywords 元数据和 Agent._auto_select_strategy 管理，
    # 不再在 Config 中重复定义。

    @property
    def is_anthropic(self) -> bool:
        return self.provider in ("anthropic", "claude")

    def with_overrides(self, **changes) -> "Config":
        """返回修改了指定字段的新 Config 实例（原始不变）。

        per-session 隔离用: session_config = config.with_overrides(work_dir=session_dir)
        """
        return _dc_replace(self, **changes)


def get_config() -> Config:
    """返回缓存的 Config 单例。

    首次调用时从环境变量构建，后续调用直接返回缓存实例。
    避免 server.py 每次请求重复 30+ 次 os.getenv。
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance
