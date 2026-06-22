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
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Config
from .llm import LLM
from .memory import Memory
from .orient import Orient
from .tools import ToolRegistry


class Agent:
    """通用 AI Agent，支持 O-O-D-A、多 LLM 后端、工具调用、多种推理策略和记忆。"""

    StrategyFn = Callable[..., str]

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.llm = LLM(self.config)
        self.tools = ToolRegistry(self.config.work_dir, self.config.bash_timeout)
        self.memory = Memory(self.config.memory_window, self.config.memory_file)
        self.orient_engine = Orient(self.config, self.llm)
        self._plan_steps: list[str] = []
        self._strategy_instance = None
        self._last_orientation: Optional[dict] = None  # 最近一次 Orient 结果

    # ── 主入口 ──────────────────────────────────────────

    def run(self, task: str, strategy: str = "default", **strategy_kwargs) -> str:
        """
        执行用户任务。

        Args:
            task:     用户输入的任务描述
            strategy: 推理策略 (default | react | plan-execute | reflexion | tree-of-thought)
            **strategy_kwargs: 传递给策略的额外参数

        Returns:
            Agent 的最终回复文本
        """
        base_messages = self._build_messages(task)

        if strategy == "react":
            final = self._run_react(task)
        elif strategy == "plan-execute":
            final = self._run_plan_execute(task)
        elif strategy == "reflexion":
            final = self._run_reflexion(task, **strategy_kwargs)
        elif strategy == "tree-of-thought":
            final = self._run_tree_of_thought(task, **strategy_kwargs)
        else:  # default
            final, _ = self._agent_loop(base_messages)

        self.memory.save_context(task, final)
        self.memory.save_persistent(task, final)
        return final

    # ── 策略实现 ────────────────────────────────────────

    def _run_react(self, task: str) -> str:
        from .strategies import ReActStrategy

        s = ReActStrategy(self.config, self.llm, self.tools)
        self._strategy_instance = s
        return s.run(task, self._agent_loop)

    def _run_plan_execute(self, task: str) -> str:
        from .strategies import PlanExecuteStrategy

        s = PlanExecuteStrategy(self.config, self.llm, self.tools)
        self._strategy_instance = s
        return s.run(task, self._agent_loop)

    def _run_reflexion(self, task: str, max_retries: int = 3) -> str:
        from .strategies import ReflexionStrategy

        s = ReflexionStrategy(self.config, self.llm, self.tools,
                              max_retries=max_retries)
        self._strategy_instance = s
        return s.run(task, self._agent_loop)

    def _run_tree_of_thought(self, task: str, num_candidates: int = 3,
                             score_threshold: int = 6) -> str:
        from .strategies import TreeOfThoughtStrategy

        s = TreeOfThoughtStrategy(self.config, self.llm, self.tools,
                                  num_candidates=num_candidates,
                                  score_threshold=score_threshold)
        self._strategy_instance = s
        return s.run(task, self._agent_loop)

    # ── 核心循环 (O-O-D-A) ──────────────────────────────

    def _agent_loop(self, messages: list, exclude_tools: Optional[list] = None) -> tuple[str, list]:
        """
        Agent 核心循环：O-O-D-A

        Observe (工具返回) → Orient (解读) → Decide (LLM) → Act (执行工具)
        """
        schemas = self.tools.get_schemas()
        if exclude_tools:
            schemas = [s for s in schemas if s["function"]["name"] not in exclude_tools]

        for _ in range(self.config.max_iterations):
            # ── Decide: LLM 决策 ──
            response = self.llm.chat(
                messages=messages,
                tools=schemas,
                system=self._system_prompt(),
            )

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": response["text"]}
            if response["tool_calls"]:
                # 转换为 OpenAI 兼容格式: type=function + function{name, arguments(str)}
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False) if isinstance(tc["arguments"], dict) else str(tc["arguments"]),
                        },
                    }
                    for tc in response["tool_calls"]
                ]
            messages.append(assistant_msg)

            # 无工具调用 → 结束
            if not response["tool_calls"]:
                return response["text"], messages

            # ── Act: 执行工具 ──
            for tc in response["tool_calls"]:
                name = tc["name"]
                args = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
                print(f"\033[33m[Tool] {name}({json.dumps(args, ensure_ascii=False)[:200]})\033[0m")

                if name == "plan":
                    plan_result = self._handle_plan(args.get("task", ""))
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"], "content": plan_result,
                    })
                    if self._plan_steps:
                        results = []
                        for i, step in enumerate(self._plan_steps, 1):
                            print(f"\n[Plan Step {i}/{len(self._plan_steps)}] {step}")
                            messages.append({"role": "user", "content": step})
                            r, messages = self._agent_loop(messages, exclude_tools=["plan"])
                            results.append(r)
                        self._plan_steps = []
                        return "\n".join(results), messages
                else:
                    # ── Act: 执行 ──
                    raw_result = self.tools.execute(name, args)
                    print(raw_result[:200])

                    # ── Orient: 显式解读 ──
                    orientation = self._orient(raw_result, args.get("task", ""))
                    if orientation:
                        enriched = (
                            f"{raw_result}\n\n"
                            f"[Orient] interpretation={orientation.get('interpretation', '')[:200]}\n"
                            f"[Orient] implication={orientation.get('implication', '')[:200]}"
                        )
                        messages.append({
                            "role": "tool", "tool_call_id": tc["id"], "content": enriched,
                        })
                    else:
                        messages.append({
                            "role": "tool", "tool_call_id": tc["id"], "content": raw_result,
                        })

        return "Max iterations reached.", messages

    # ── Orient 阶段 ─────────────────────────────────────

    def _orient(self, observation: str, task: str = "") -> Optional[dict]:
        """
        Orient 阶段：将工具结果转化为结构化理解。

        如果观察很短（如简单文本），跳过 LLM 调用以节省 token。
        """
        # 短结果跳过 Orient 以提高效率
        if len(observation) < 200:
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
        except Exception:
            return None

    # ── 计划 ─────────────────────────────────────────────

    def _create_plan(self, task: str) -> list[str]:
        print("[Plan] Breaking down task...")
        try:
            messages = [{
                "role": "user",
                "content": (
                    f"Break the following task into 3-5 simple, actionable steps. "
                    f"Return ONLY a JSON object with a 'steps' array. "
                    f"No markdown, no explanation.\n\nTask: {task}"
                ),
            }]
            response = self.llm.chat(messages=messages, tools=[], system="")
            text = response["text"].strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                if text.endswith("```"):
                    text = text[:-3]
            plan_data = json.loads(text)
            steps = plan_data.get("steps", [task])
            if not isinstance(steps, list) or not steps:
                steps = [task]
            print(f"[Plan] {len(steps)} steps created")
            for i, s in enumerate(steps, 1):
                print(f"  {i}. {s}")
            return [str(s) for s in steps]
        except Exception:
            print("[Plan] Failed, using raw task")
            return [task]

    def _handle_plan(self, task: str) -> str:
        if self._plan_steps:
            return "Error: Plan already in progress"
        self._plan_steps = self._create_plan(task)
        return f"Plan created with {len(self._plan_steps)} steps. Executing now..."

    # ── 消息构建 ────────────────────────────────────────

    def _system_prompt(self) -> str:
        parts = ["You are a coding agent. Use tools to solve tasks. Be concise and act."]
        rules = self._load_rules()
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
        messages.append({"role": "user", "content": task})
        return messages

    # ── 规则加载 ────────────────────────────────────────

    def _load_rules(self) -> str:
        rules_dir = self.config.rules_dir
        if not rules_dir or not Path(rules_dir).exists():
            return ""
        try:
            rules = []
            for rule_file in sorted(Path(rules_dir).glob("*.md")):
                content = rule_file.read_text(encoding="utf-8").strip()
                if content:
                    rules.append(f"## {rule_file.stem}\n{content}")
            return "\n\n".join(rules) if rules else ""
        except Exception:
            return ""

    # ── 便利方法 ────────────────────────────────────────

    def clear_memory(self):
        self.memory.clear()
        self._last_orientation = None
        print("Memory cleared.")

    @property
    def memory_summary(self) -> str:
        return self.memory.get_summary()

    @property
    def last_orientation(self) -> Optional[dict]:
        return self._last_orientation
