"""
Agent 核心 — O-O-D-A 循环 + 五种推理策略 + 规则加载 + 记忆管理。

融合:
  - nanoAgent (agent-claudecode.py): 工具注册、规则加载、plan 模式、持久记忆
  - demo_2 (agent_react_v2.py):   类封装、窗口记忆、系统提示词构建

O-O-D-A 阶段:
  - Observe:  工具结果 / 用户输入
  - Orient:   显式解读观察 → 关联记忆 → 匹配规则 → 生成建议
  - Decide:   LLM 决定工具调用或结束
  - Act:      执行工具或返回文本

策略:
  - default:      标准 agent loop (隐式 Orient)
  - react:        ReAct — 显式 Thought → Action → Observation 循环
  - plan-execute: 规划 → 逐步执行 → 评估 → 必要时重规划
  - reflexion:    自我反思 + 失败重试 + 教训学习
  - tree-of-thought: 多路径探索 → 评估 → 选最优 → 回溯
"""

import json
import logging
from typing import Any, Callable, Optional

from .config import Config
from .llm import LLM
from .memory import Memory
from .orient import Orient
from .tools import ToolRegistry
from .strategies import STRATEGY_REGISTRY

logger = logging.getLogger("nano_agent.agent")


class Agent:
    """通用 AI Agent，支持 O-O-D-A、多 LLM 后端、工具调用、多种推理策略和记忆。"""

    StrategyFn = Callable[..., str]

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.llm = LLM(self.config)
        self.tools = ToolRegistry(self.config.work_dir, self.config.bash_timeout,
                                   brave_api_key=self.config.brave_api_key or "")
        self.memory = Memory(self.config.memory_window, self.config.memory_file,
                              self.config.reflection_file, self.config.long_term_db)
        self.orient_engine = Orient(self.config, self.llm)
        self._strategy_instance = None
        self._last_orientation: Optional[dict] = None  # 最近一次 Orient 结果
        self._on_event = None

    # ── 主入口 ──────────────────────────────────────────

    def run(self, task: str, strategy: str = "default",
            on_event: Optional[Callable[[str, dict], None]] = None,
            **strategy_kwargs) -> str:
        """
        执行用户任务。

        Args:
            task:     用户输入的任务描述
            strategy: 推理策略 (default | react | plan-execute | reflexion | tree-of-thought)
            on_event: 可选事件回调 f(event_type, data)
                      event_type: "text" | "tool_call" | "tool_result" | "orient" | "done"
            **strategy_kwargs: 传递给策略的额外参数

        Returns:
            Agent 的最终回复文本
        """
        self._on_event = on_event
        self._emit("text", {"text": f"Task: {task}\nStrategy: {strategy}"})

        base_messages = self._build_messages(task)

        strategy_cls = STRATEGY_REGISTRY.get(strategy)
        if not strategy_cls:
            raise ValueError(f"Unknown strategy: '{strategy}'. Available: {list(STRATEGY_REGISTRY.keys())}")

        defaults = self._strategy_defaults(strategy)
        defaults.update(strategy_kwargs)
        final = self._run_strategy(strategy_cls, task, **defaults)

        self._emit("done", {"text": final})
        self.memory.save_context(task, final)
        self.memory.save_persistent(task, final)
        self._on_event = None
        return final

    def _emit(self, event_type: str, data: dict):
        """发送事件给回调。"""
        if self._on_event:
            try:
                self._on_event(event_type, data)
            except Exception as e:
                logger.warning(f"Event callback error ({event_type}): {e}")

    # ── 策略实现 ────────────────────────────────────────

    def _run_strategy(self, strategy_cls, task: str, **kwargs) -> str:
        """通用策略执行：实例化 → 注入事件回调 → 缓存 → 运行。"""
        s = strategy_cls(self.config, self.llm, self.tools, **kwargs)
        s._emit = self._emit  # 透传事件回调，让策略能发事件
        self._strategy_instance = s
        return s.run(task, self._agent_loop)

    def _strategy_defaults(self, strategy: str) -> dict:
        """从 Config 获取策略默认参数。"""
        defaults = {}
        if strategy == "react":
            defaults["max_steps"] = self.config.react_max_steps
        elif strategy == "reflexion":
            defaults["max_retries"] = self.config.reflexion_max_retries
        elif strategy == "tree-of-thought":
            defaults["num_candidates"] = self.config.tot_num_candidates
            defaults["score_threshold"] = self.config.tot_score_threshold
        return defaults

    # ── 核心循环 (O-O-D-A) ──────────────────────────────

    def _agent_loop(self, messages: list, exclude_tools: Optional[list] = None) -> tuple[str, list]:
        """
        Agent 核心循环：O-O-D-A

        Observe (工具返回) → Orient (解读) → Decide (LLM) → Act (执行工具)
        """
        schemas = self.tools.get_schemas()
        if exclude_tools:
            schemas = [s for s in schemas if s["function"]["name"] not in exclude_tools]

        # system_prompt 在循环内不变，构建一次复用
        system_prompt = self._system_prompt()

        for _ in range(self.config.max_iterations):
            # ── Decide: LLM 决策 ──
            response = self.llm.chat(
                messages=messages,
                tools=schemas,
                system=system_prompt,
            )

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": response["text"]}
            if response["tool_calls"]:
                assistant_msg["tool_calls"] = [
                    self.llm.format_tool_call_for_message(tc)
                    for tc in response["tool_calls"]
                ]
            messages.append(assistant_msg)

            # 无工具调用 → 结束
            if not response["tool_calls"]:
                if response["text"]:
                    self._emit("text", {"text": response["text"]})
                return response["text"], messages

            # ── Act: 执行工具 ──
            for tc in response["tool_calls"]:
                name = tc["name"]
                args = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
                self._emit("tool_call", {"name": name, "args": args})
                logger.info(f"[Tool] {name}({json.dumps(args, ensure_ascii=False)[:200]})")

                # ── Act: 执行 ──
                raw_result = self.tools.execute(name, args)
                # Observation 兼容：success 从对象取，字符操作委托到 result
                is_success = raw_result.success if hasattr(raw_result, "success") else not str(raw_result).startswith("Error:")
                result_text = str(raw_result)
                self._emit("tool_result", {"name": name, "result": result_text,
                                            "success": is_success})
                logger.debug(f"[Tool Result] {result_text[:200]}")

                # ── Orient: 显式解读 ──
                orientation = self._orient(result_text, args.get("task", ""))
                if orientation:
                    self._emit("orient", orientation)
                    enriched = (
                        f"{result_text}\n\n"
                        f"[Orient] interpretation={orientation.get('interpretation', '')[:200]}\n"
                        f"[Orient] implication={orientation.get('implication', '')[:200]}"
                    )
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"], "content": enriched,
                    })
                else:
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"], "content": result_text,
                    })

        logger.warning(f"Max iterations ({self.config.max_iterations}) reached, "
                        f"forcing stop. Last tool: "
                        f"{response.get('tool_calls', [{}])[0].get('name', 'N/A') if response.get('tool_calls') else 'N/A'}")
        return "Max iterations reached.", messages

    # ── Orient 阶段 ─────────────────────────────────────

    def _orient(self, observation: str, task: str = "") -> Optional[dict]:
        """
        Orient 阶段：将工具结果转化为结构化理解。

        如果观察很短（如简单文本），跳过 LLM 调用以节省 token。
        """
        # 短结果跳过 Orient 以提高效率
        if len(observation) < self.config.orient_min_chars:
            return None

        try:
            memory_context = self.memory.load_persistent()
            rules = self.orient_engine.load_rules()
            orientation = self.orient_engine.orient(
                observation, task or "complete the current task",
                memory_context, rules,
            )
            self._last_orientation = orientation
            return orientation
        except Exception as e:
            logger.warning(f"Orient failed: {e}")
            return None

    # ── 消息构建 ────────────────────────────────────────

    def _system_prompt(self) -> str:
        parts = [
            "You are Sleeping Fox (睡狐), an AI assistant developed for this platform. "
            "You are powered by large language models and equipped with tools to help users. "
            "Be concise, helpful, and act decisively. When asked who you are, say you are Sleeping Fox."
        ]
        rules = self.orient_engine.load_rules()
        if rules:
            parts.append(f"\n# Rules\n{rules}")
        persistent = self.memory.load_persistent()
        if persistent:
            parts.append(f"\n# Previous Context\n{persistent}")
        # 如果有上一个 Orient 结论，注入
        if self._last_orientation:
            o = self._last_orientation
            parts.append(
                f"\n# Latest Orientation\n"
                f"Interpretation: {o.get('interpretation', '')[:300]}\n"
                f"Focus: {o.get('focus', '')[:200]}"
            )
        return "\n".join(parts)

    def _build_messages(self, task: str) -> list[dict]:
        messages = []
        for msg in self.memory.get_window_messages():
            messages.append(msg)
        # 注入长期记忆检索结果
        relevant = self.memory.load_relevant(task, top_k=3)
        if relevant:
            messages.append({
                "role": "system",
                "content": f"# Relevant Past Experience\n{relevant}"
            })
        messages.append({"role": "user", "content": task})
        return messages

    # ── 便利方法 ────────────────────────────────────────

    def clear_memory(self):
        self.memory.clear()
        self._last_orientation = None
        logger.info("Memory cleared.")

    @property
    def memory_summary(self) -> str:
        return self.memory.get_summary()

    @property
    def last_orientation(self) -> Optional[dict]:
        return self._last_orientation
