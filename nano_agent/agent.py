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
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from .config import Config
from .llm import LLM
from .memory import Memory
from .orient import Orient
from .tools import ToolRegistry
from .strategies import STRATEGY_REGISTRY
from .strategies.context import StrategyContext

logger = logging.getLogger("nano_agent.agent")


class Agent:
    """通用 AI Agent，支持 O-O-D-A、多 LLM 后端、工具调用、多种推理策略和记忆。"""

    StrategyFn = Callable[..., str]

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.llm = LLM(self.config)
        self.tools = ToolRegistry(self.config.work_dir, self.config.bash_timeout,
                                   brave_api_key=self.config.brave_api_key or "",
                                   charts_dir=self.config.charts_dir,
                                   public_mode=self.config.public_mode,
                                   bash_output_limit=self.config.bash_output_limit,
                                   fetch_max_chars=self.config.fetch_max_chars)
        # 记忆路径：默认放在 work_dir 下，避免 CWD 差异导致多份文件
        _wd = Path(self.config.work_dir)
        mem_file = self.config.memory_file or str(_wd / "agent_memory.md")
        refl_file = self.config.reflection_file or str(_wd / "reflection_traces.md")
        lt_db = self.config.long_term_db or str(_wd / "long_term_memory.db")
        rx_db = self.config.reflexion_db or str(_wd / "reflexion_trace.db")
        self.memory = Memory(self.config.memory_window, mem_file,
                              refl_file, lt_db, rx_db,
                              max_lines=self.config.memory_max_lines)
        self.orient_engine = Orient(self.config, self.llm)
        self._strategy_instance = None
        self._last_orientation: Optional[dict] = None  # 最近一次 Orient 结果
        self._on_event = None
        self._current_orient_fn: Optional[Callable] = None  # 当前任务的 Orient 函数（绑定原始任务）

    # ── 主入口 ──────────────────────────────────────────

    def run(self, task: str, strategy: str = "default",
            on_event: Optional[Callable[[str, dict], None]] = None,
            model_override: Optional[str] = None,
            **strategy_kwargs) -> str:
        """
        执行用户任务。

        Args:
            task:     用户输入的任务描述
            strategy: 推理策略 (default | react | plan-execute | reflexion | tree-of-thought)
            on_event: 可选事件回调 f(event_type, data)
                      event_type: "text" | "tool_call" | "tool_result" | "orient" | "done"
            model_override: 单次请求的模型覆盖（不修改 Agent 实例状态，线程安全）
            **strategy_kwargs: 传递给策略的额外参数

        Returns:
            Agent 的最终回复文本
        """
        self._on_event = on_event
        self._model_override = model_override  # 请求级模型覆盖
        self._emit("text", {"text": f"Task: {task}\nStrategy: {strategy}"})

        # auto 模式：LLM 根据用户意图自动选策略
        if strategy == "auto":
            strategy = self._auto_select_strategy(task)
            self._emit("text", {"text": f"🤖 Auto-selected strategy: {strategy}"})

        strategy_cls = STRATEGY_REGISTRY.get(strategy)
        if not strategy_cls:
            raise ValueError(f"Unknown strategy: '{strategy}'. Available: {list(STRATEGY_REGISTRY.keys())}")

        # Orient: 从策略类元数据读取，不再硬编码策略名
        self._current_orient_fn = (
            (lambda obs: self._orient(obs, task=task)) if strategy_cls.uses_orient else None
        )

        # 所有策略统一走 _run_strategy → StrategyContext → strategy.run()。
        # 详见 README "决策 7：策略执行路径统一 vs 热路径特化"。
        defaults = self._strategy_defaults(strategy_cls)
        defaults.update(strategy_kwargs)
        final = self._run_strategy(strategy_cls, task, **defaults)

        self._emit("done", {"text": final})
        self.memory.save_context(task, final)
        self.memory.save_persistent(task, final)
        self._on_event = None
        self._current_orient_fn = None
        return final

    def _emit(self, event_type: str, data: dict):
        """发送事件给回调。"""
        if self._on_event:
            try:
                self._on_event(event_type, data)
            except Exception as e:
                logger.warning(f"Event callback error ({event_type}): {e}")

    # ── [已废弃] default 热路径：流式快速通道 ──────────────
    # 2026-06-29: 统一架构，所有策略走 _run_strategy。
    # 流式逻辑已移到 DefaultStrategy.run() (strategies/default.py)。
    # 保留此代码作为参考，如需恢复热路径优化，取消注释并恢复
    # Agent.run() 里的 `if strategy == "default"` 分支。
    #
    # def _run_stream_default(self, task: str) -> str:
    #     """Default 策略流式快速路径：带 tools 流式调用，纯知识问答即时输出，
    #     如果 LLM 要调工具则自动切换到 agent_loop。"""
    #     from .strategies.default import DefaultStrategy
    #     ctx = self._make_strategy_context()
    #     s = DefaultStrategy(context=ctx)
    #     self._strategy_instance = s
    #     messages = s.build_messages(task)
    #     system_prompt = self._system_prompt()
    #     schemas = self.tools.get_schemas()
    #
    #     full_text = ""
    #     tool_calls = None
    #     for chunk in self.llm.chat_stream(messages=messages, system=system_prompt, tools=schemas,
    #                                         model=self._model_override):
    #         if isinstance(chunk, dict) and chunk.get("type") == "tool_calls":
    #             tool_calls = chunk["tool_calls"]
    #             break
    #         if isinstance(chunk, str):
    #             full_text += chunk
    #             self._emit("text", {"text": full_text})
    #
    #     if tool_calls:
    #         assistant_msg = {"role": "assistant", "content": full_text, "tool_calls": [
    #             self.llm.format_tool_call_for_message(tc) for tc in tool_calls
    #         ]}
    #         messages.append(assistant_msg)
    #         if len(tool_calls) == 1:
    #             self.execute_tool(tool_calls[0], messages, orient_fn=self._current_orient_fn)
    #         else:
    #             self._execute_tools_parallel(tool_calls, messages)
    #         return self._agent_loop(messages)[0]
    #
    #     from .strategies.default import _DRAW_KW, _EDIT_KW, _ADD_KW, _QA_KW
    #     is_draw = any(k in task.lower() for k in _DRAW_KW)
    #     is_edit = any(k in task.lower() for k in _EDIT_KW)
    #     is_add  = any(k in task.lower() for k in _ADD_KW)
    #     is_qa   = any(k in task.lower() for k in _QA_KW)
    #
    #     prev_visual = False
    #     if self.memory:
    #         msgs = self.memory.get_window_messages()
    #         for m in reversed(msgs):
    #             if m["role"] == "user":
    #                 prev_visual = any(k in m["content"].lower()
    #                                   for k in _DRAW_KW + _EDIT_KW + _ADD_KW)
    #                 break
    #
    #     needs_visual = is_draw or is_edit or is_add or (not is_qa and prev_visual)
    #
    #     if not tool_calls and needs_visual:
    #         logger.info(f"VISUAL: '{task[:50]}' draw={is_draw} edit={is_edit} prev={prev_visual}")
    #         self._emit("text", {"text": "🔧 正在生成图片..."})
    #
    #         ctx = ""
    #         if self.memory:
    #             for m in self.memory.get_window_messages()[-6:]:
    #                 r = "用户" if m["role"] == "user" else "助手"
    #                 txt = m["content"][:150].split("![")[0].strip()
    #                 if txt: ctx += f"[{r}]: {txt}\n"
    #
    #         override = (
    #             "Call a drawing tool NOW. Context:\n{ctx}\n"
    #             "User said: '{task}'. You MUST call one of: mermaid_chart, generate_chart, draw_circuit."
    #         ).format(ctx=ctx.strip(), task=task[:200])
    #         messages.append({"role": "user", "content": override})
    #         result, msgs = self._agent_loop(messages)
    #
    #         had_tool = any(m.get("role") == "assistant" and m.get("tool_calls") for m in msgs[-4:])
    #         if not had_tool:
    #             try:
    #                 from nano_agent.tools.diagram import Diagram
    #                 d = Diagram(work_dir=self.config.work_dir, charts_dir=self.config.charts_dir)
    #                 img = d.mermaid_chart(f"flowchart LR\n  A[{task[:40]}]-->B[Result]", theme="dark")
    #                 return result + "\n\n" + img
    #             except Exception:
    #                 pass
    #         return result
    #
    #     if not full_text.strip():
    #         return self._agent_loop(messages)[0]
    #
    #     return full_text

    def _make_strategy_context(self) -> StrategyContext:
        """构建策略上下文，替代猴子补丁注入。"""
        return StrategyContext(
            config=self.config, llm=self.llm, tools=self.tools, memory=self.memory,
            emit=self._emit, execute_tool=self.execute_tool,
            agent_loop=self._agent_loop, orient_fn=self._current_orient_fn,
            model_override=self._model_override,
            system_prompt_fn=self._system_prompt,
        )

    def _run_strategy(self, strategy_cls, task: str, **kwargs) -> str:
        """通用策略执行：构建 StrategyContext → 实例化 → 运行。"""
        ctx = self._make_strategy_context()
        kwargs.setdefault("memory", self.memory)
        s = strategy_cls(ctx.config, ctx.llm, ctx.tools, context=ctx, **kwargs)
        self._strategy_instance = s
        return s.run(task, self._agent_loop)

    def _auto_select_strategy(self, task: str) -> str:
        """根据用户意图自动选择策略。

        按 auto_priority 降序遍历所有策略，用各策略类的 auto_keywords 匹配。
        新增策略只需在类上设 auto_keywords + auto_priority，无需改 Agent。
        无关键词匹配时走 LLM 分类。
        """
        task_lower = task.lower().strip()

        # 按优先级降序遍历所有策略，关键词匹配
        sorted_strategies = sorted(
            STRATEGY_REGISTRY.items(),
            key=lambda item: item[1].auto_priority,
            reverse=True,
        )
        for name, cls in sorted_strategies:
            if cls.auto_keywords and any(kw in task_lower for kw in cls.auto_keywords):
                # default 策略额外检查：短任务才匹配，避免长任务误判
                if name == "default" and len(task_lower) >= 80:
                    continue
                return name

        # 无关键词匹配 → LLM 分类
        prompt = (
            "Classify this task into exactly one strategy. Reply with ONLY the strategy name.\n\n"
            "Strategies:\n"
            "- default: simple Q&A, knowledge, calculation, chat\n"
            "- react: needs step-by-step visible reasoning, debugging, audit trail\n"
            "- plan-execute: complex multi-step task, project, report, analysis\n"
            "- reflexion: quality-critical, needs self-review, error-prone task\n"
            "- tree-of-thought: multiple valid approaches, creative brainstorming, optimization\n\n"
            f"Task: {task}\n\nStrategy:"
        )
        try:
            resp = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=[], system="Reply with only one word.",
                model=self._model_override,
            )
            name = resp["text"].strip().lower()
            if name in STRATEGY_REGISTRY:
                return name
        except Exception:
            pass
        return "default"

    def _strategy_defaults(self, strategy_cls) -> dict:
        """从策略类元数据 + Config 构建默认参数。

        策略类声明 default_params（硬编码默认值），Config 环境变量可覆盖。
        新增策略只需在类上设 default_params，无需改 Agent。
        """
        # 类默认值
        defaults = dict(strategy_cls.default_params)
        # Config 覆盖（按参数名映射到 Config 属性）
        _config_map = {
            "max_steps": self.config.react_max_steps,
            "max_retries": self.config.reflexion_max_retries,
            "num_candidates": self.config.tot_num_candidates,
            "score_threshold": self.config.tot_score_threshold,
        }
        for key, config_val in _config_map.items():
            if key in defaults:
                defaults[key] = config_val
        return defaults

    def execute_tool(self, tc: dict, messages: list, orient_fn=None,
                     enable_orient: bool = True):
        """执行单个工具调用并追加结果到 messages。

        orient_fn:       Orient 函数（覆盖 self._current_orient_fn）
        enable_orient:   是否执行 Orient。策略可按需关闭（如 plan 阶段）。
                         默认 True 保持向后兼容。
        """
        name = tc["name"]
        args = tc.get("arguments", {})
        # 兼容 LLM 返回 JSON 字符串参数
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}

        self._emit("tool_call", {"name": name, "args": args})
        logger.info(f"[Tool] {name}({json.dumps(args, ensure_ascii=False)[:200]})")
        observation = self.tools.execute(name, args)
        result_text = str(observation)
        is_success = observation.success
        self._emit("tool_result", {"name": name, "result": result_text, "success": is_success})
        logger.debug(f"[Tool Result] {result_text[:200]}")

        # Orient — 可通过 enable_orient 关闭
        content = result_text
        if enable_orient:
            _fn = orient_fn or self._current_orient_fn
            if _fn:
                orientation = _fn(result_text)
                if orientation:
                    self._emit("orient", orientation)
                    content = (
                        f"{result_text}\n\n"
                        f"[Orient] interpretation={orientation.get('interpretation', '')[:200]}\n"
                        f"[Orient] implication={orientation.get('implication', '')[:200]}"
                    )

        messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": content,
        })
        return {"name": name, "result": result_text, "content": content, "success": is_success}

    # ── 核心循环 (O-O-D-A) ──────────────────────────────

    def _agent_loop(self, messages: list, exclude_tools: Optional[list] = None,
                    system_prompt: Optional[str] = None,
                    step_callback: Optional[Callable[[str, list], Optional[str]]] = None,
                    tool_callback: Optional[Callable[[str, dict, str, bool], None]] = None,
                    ) -> tuple[str, list]:
        """
        Agent 核心循环：O-O-D-A

        Observe (工具返回) → Orient (解读) → Decide (LLM) → Act (执行工具)

        system_prompt: 覆盖默认 prompt。策略可通过此参数注入专属 prompt。

        step_callback:  每次 LLM 响应后调用 f(text, tool_calls) → 返回 str 则提前终止
                        并以此 str 作为最终回答。ReAct 用于检测 Final Answer。
        tool_callback:  每次工具执行后调用 f(tool_name, tool_args, result_text, is_success)。
                        ReAct 用于记录 observation 到 thought_trail。
        """
        schemas = self.tools.get_schemas()
        if exclude_tools:
            schemas = [s for s in schemas if s["function"]["name"] not in exclude_tools]

        # system_prompt — 允许策略覆盖
        prompt = system_prompt or self._system_prompt()
        response = None  # 防止 max_iterations=0 时 NameError

        for _ in range(self.config.max_iterations):
            # ── Decide: LLM 决策 ──
            response = self.llm.chat(
                messages=messages,
                tools=schemas,
                system=prompt,
                model=self._model_override,
            )

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": response["text"]}
            if response["tool_calls"]:
                assistant_msg["tool_calls"] = [
                    self.llm.format_tool_call_for_message(tc)
                    for tc in response["tool_calls"]
                ]
            messages.append(assistant_msg)

            # ── step_callback: 策略可拦截 LLM 响应，提前终止循环 ──
            if step_callback:
                early_stop = step_callback(response["text"], response["tool_calls"])
                if early_stop is not None:
                    return early_stop, messages

            # 无工具调用 → 结束
            if not response["tool_calls"]:
                return response["text"], messages

            # ── Act: 执行工具 ──
            if len(response["tool_calls"]) == 1:
                tc = response["tool_calls"][0]
                info = self.execute_tool(tc, messages,
                                         orient_fn=self._current_orient_fn)
                if tool_callback:
                    tool_callback(info["name"], tc.get("arguments", {}),
                                  info["result"], info["success"])
            else:
                # 并行执行：记录结果后在 tool_callback 中回调
                # 注意：并行执行时 Orient 在 execute_tool 内完成，
                # 但 execute_tool 会往传入的 messages list 追加 tool message。
                # 并行场景下不能传主 messages（线程不安全），传临时 list 收集后手动合并。
                results: dict[int, dict] = {}
                def _run_one(idx: int, tc: dict):
                    try:
                        # 用临时 list 收集 execute_tool 追加的 tool message
                        tmp_msgs: list = []
                        info = self.execute_tool(tc, tmp_msgs, orient_fn=self._current_orient_fn)
                        # 提取 execute_tool 追加的 tool message（含 Orient 富化后的 content）
                        if tmp_msgs:
                            info["_tool_msg"] = tmp_msgs[-1]
                    except Exception as e:
                        logger.warning(f"Parallel tool '{tc.get('name', '?')}' failed: {e}")
                        info = {"name": tc.get("name", "?"), "result": f"Error: {e}",
                                "content": f"Error: {e}", "success": False}
                    results[idx] = info

                with ThreadPoolExecutor(max_workers=len(response["tool_calls"])) as executor:
                    futures = [executor.submit(_run_one, i, tc)
                               for i, tc in enumerate(response["tool_calls"])]
                    for f in as_completed(futures):
                        f.result()

                for i in sorted(results.keys()):
                    info = results[i]
                    tc = response["tool_calls"][i]
                    # 优先用 execute_tool 生成的 tool message（含 Orient 富化）
                    if "_tool_msg" in info:
                        messages.append(info["_tool_msg"])
                    else:
                        content = str(info.get("content") or info.get("result", ""))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": content,
                        })
                    if tool_callback:
                        tool_callback(info["name"], tc.get("arguments", {}),
                                      info["result"], info["success"])

        logger.warning(f"Max iterations ({self.config.max_iterations}) reached, "
                        f"forcing stop. Last tool: "
                        f"{response.get('tool_calls', [{}])[0].get('name', 'N/A') if response.get('tool_calls') else 'N/A'}")
        return "I am tired... 😴 Let me rest a bit. Could you try again or rephrase?", messages

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
                model=self._model_override,
            )
            self._last_orientation = orientation
            return orientation
        except Exception as e:
            logger.warning(f"Orient failed: {e}")
            return None

    # ── 消息构建 ────────────────────────────────────────

    def _system_prompt(self) -> str:
        parts = [
            "You are Sleeping fox (睡狐), an AI assistant developed for this platform. "
            "You are powered by large language models and equipped with tools to help users. "
            "Be concise, helpful, and act decisively. When asked who you are, say you are Sleeping fox.",
            "Keep answers reasonably short — use bullet points and tables over long paragraphs. Don't over-explain, skip filler."
            "",
            "# Quick Rules",
            "- Simple knowledge/definition → answer directly, no tools. Be fast.",
            "- RULE #1: If the user says draw, 画, 生成, create, make, show, 图, chart, diagram, plot, 图, image → MUST call a tool. Text description is a VIOLATION. Every. Single. Time.",
            "- Pick the right tool based on its description. Trust the tool descriptions.",
            "- Always show images with ![title](url)",
            "- A股大盘用 stock_market，美股大盘用 stock_market_us，不要用 stock_info 逐个查询指数。",
            "",
            "# Math / Formula Writing",
            "- CRITICAL: ALL math MUST be inside $...$ (inline) or $$...$$ (block). Never write math in plain text.",
            "- Superscript: $x^2$ NOT x^2. Subscript: $x_1$ NOT x1. Fractions: $\\frac{a}{b}$ NOT a/b.",
            "- NEVER write raw expressions like x^2, 1/2, sqrt(x), a_1 — they look broken. Always KaTeX-wrap them.",
            "- Use \\text{...} for text inside formulas, never raw words in math mode.",
            "- Break long derivations into multiple display blocks, one step per block.",
            "- Write units with \\text{...} or \\mathrm{...}: $3.0 \\times 10^8 \\text{ m/s}$",
            "- Use \\frac, \\sqrt, \\sum, \\int with clear limits.",
            "- Align multi-line equations with \\begin{aligned} inside $$...$$.",
            "",
            "# File Operations",
            "- When you write a file, ALWAYS provide a download link: [下载 {filename}](/api/download/{filename})",
            "- Example: [下载 report.txt](/api/download/report.txt)",
            "",
            "# Chart / Drawing Rules — pick the right tool and chart_type based on the task",
            "- Read the tool's `chart_type` descriptions carefully before choosing. Each type has a distinct purpose.",
            "- NEVER output Chart.js/D3.js/HTML/SVG/JS code. The frontend cannot render them.",
            "- Always include the returned ![title](url) markdown in your response so users see the image.",
        ]
        rules = self.orient_engine.load_rules()
        if rules:
            parts.append(f"\n# Rules\n{rules}")
        persistent = self.memory.load_persistent()
        if persistent:
            parts.append(f"\n# Previous Context\n{persistent}")
        return "\n".join(parts)

    def _build_messages(self, task: str) -> list[dict]:
        """构建消息列表。委托给 BaseStrategy.build_messages，确保与策略层一致。"""
        from .strategies.base import BaseStrategy
        dummy = BaseStrategy(self.config, self.llm, self.tools, memory=self.memory)
        return dummy.build_messages(task, include_memory=True)

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
