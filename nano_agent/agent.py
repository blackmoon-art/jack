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
import threading
import time as _time
from pathlib import Path
from typing import Any, Callable

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

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.llm = LLM(self.config)
        self.tools = ToolRegistry(self.config.work_dir, self.config.bash_timeout,
                                   brave_api_key=self.config.brave_api_key or "",
                                   charts_dir=self.config.charts_dir,
                                   public_mode=self.config.public_mode,
                                   bash_output_limit=self.config.bash_output_limit,
                                   fetch_max_chars=self.config.fetch_max_chars,
                                   enable_circuit=self.config.enable_circuit)
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
        self._emit_lock = threading.Lock()  # 保护 _emit 回调的线程安全
        self._local = threading.local()  # 每 request 独立的运行时状态
        self._local.on_event = None
        self._local.model_override = None
        self._local.strategy_instance = None
        self._local.visual_routed = False
        self._local.prompt_cache = None  # system prompt 缓存 (per-request)
        self._local.prompt_cache_key = ()
        self._local.last_orientation = None  # 最近一次 Orient 结果 (per-request)
        self._local.current_orient_fn = None  # 当前 Orient 函数 (per-request)
    # ── 主入口 ──────────────────────────────────────────

    def run(self, task: str, strategy: str = "default",
            on_event: Callable[[str, dict], None] | None = None,
            model_override: str | None = None,
            **strategy_kwargs) -> str:
        """
        执行用户任务。线程安全：同一 Agent 实例可被多线程并发调用。

        Args:
            task:     用户输入的任务描述
            strategy: 推理策略 (default | react | plan-execute | reflexion | tree-of-thought)
            on_event: 可选事件回调 f(event_type, data)
            model_override: 单次请求的模型覆盖
        """
        # 每 request 独立的状态，不修改实例属性
        # 用 threading.local 避免并发请求互相覆盖
        self._local.on_event = on_event
        self._local.model_override = model_override
        self._local.visual_routed = False
        self._local.prompt_cache = None  # reset per request
        self._local.prompt_cache_key = ()
        self._emit("text", {"text": f"Task: {task}\nStrategy: {strategy}"})

        # auto 模式：LLM 根据用户意图自动选策略
        if strategy == "auto":
            strategy = self._auto_select_strategy(task)
            self._emit("text", {"text": f"🤖 Auto-selected strategy: {strategy}"})

        strategy_cls = STRATEGY_REGISTRY.get(strategy)
        if not strategy_cls:
            raise ValueError(f"Unknown strategy: '{strategy}'. Available: {list(STRATEGY_REGISTRY.keys())}")

        # Orient: 从策略类元数据读取，不再硬编码策略名
        self._local.current_orient_fn = (
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
        self._local.on_event = None
        self._local.current_orient_fn = None
        return final

    def _emit(self, event_type: str, data: dict):
        """发送事件给回调。线程安全。"""
        on_event = getattr(self._local, 'on_event', None)
        if on_event:
            with self._emit_lock:
                try:
                    on_event(event_type, data)
                except Exception as e:
                    logger.warning(f"Event callback error ({event_type}): {e}")

    def _make_strategy_context(self) -> StrategyContext:
        """构建策略上下文，替代猴子补丁注入。"""
        return StrategyContext(
            config=self.config, llm=self.llm, tools=self.tools, memory=self.memory,
            emit=self._emit, execute_tool=self.execute_tool,
            agent_loop=self._agent_loop, orient_fn=getattr(self._local, 'current_orient_fn', None),
            model_override=getattr(self._local, 'model_override', None),
            system_prompt_fn=self._system_prompt,
        )

    def _run_strategy(self, strategy_cls, task: str, **kwargs) -> str:
        """通用策略执行：构建 StrategyContext → 实例化 → 运行。"""
        ctx = self._make_strategy_context()
        kwargs.setdefault("memory", self.memory)
        s = strategy_cls(ctx.config, ctx.llm, ctx.tools, context=ctx, **kwargs)
        self._local.strategy_instance = s
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
                model=getattr(self._local, "model_override", None),
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

        orient_fn:       Orient 函数（覆盖 per-request current_orient_fn）
        enable_orient:   是否执行 Orient。策略可按需关闭（如 plan 阶段）。
        """
        name = tc["name"]
        args = tc.get("arguments", {})
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

        # 图表视觉验证（可选，AGENT_CHART_VERIFY=true 启用）
        if self.config.chart_verify and is_success:
            verify = self._verify_chart_result(name, result_text)
            if verify:
                result_text = result_text + "\n" + verify

        # Orient 可在上层（_agent_loop）显式执行
        content = result_text
        if enable_orient:
            content = self._enrich_with_orient(result_text, orient_fn)

        messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": content,
        })
        return {"name": name, "result": result_text, "content": content, "success": is_success}

    def _enrich_with_orient(self, result_text: str, orient_fn=None) -> str:
        """对工具结果执行 Orient 解读，返回富化后的内容。OODA 的 Orient 阶段。"""
        _fn = orient_fn or getattr(self._local, 'current_orient_fn', None)
        if not _fn:
            return result_text
        orientation = _fn(result_text)
        if not orientation:
            return result_text
        self._emit("orient", orientation)
        return (
            f"{result_text}\n\n"
            f"[Orient] interpretation={orientation.get('interpretation', '')[:200]}\n"
            f"[Orient] implication={orientation.get('implication', '')[:200]}"
        )

    # ── 图表视觉验证 ────────────────────────────────────
    _CHART_TOOLS = {"generate_chart", "draw_circuit", "draw_digital",
                    "draw_analog", "draw_block", "mermaid_chart",
                    "stock_chart", "ai_image"}

    def _verify_chart_result(self, tool_name: str, result: str) -> str:
        """对图表工具输出做视觉验证。失败静默返回空字符串。"""
        if tool_name not in self._CHART_TOOLS:
            return ""

        # 从结果中提取图片路径
        import re as _re_verify
        m = _re_verify.search(r'/charts/[\w.-]+\.(?:png|jpg|jpeg|gif|webp|svg)', result)
        if not m:
            return ""
        img_path = m.group(0)

        try:
            question = (
                "Verify this chart: 1) Does it look correct and complete? "
                "2) Are all labels/axes readable? 3) Any visible errors? "
                "Answer briefly in one sentence. If OK, just say 'Chart looks correct.'"
            )
            verify_obs = self.tools.execute("analyze_image", {
                "path": img_path,
                "question": question,
            })
            verify_text = str(verify_obs)
            if verify_obs.success and verify_text and "Error" not in verify_text:
                v = verify_text.strip()[:300]
                return f"\n[Chart Verify] {v}"
        except Exception:
            pass
        return ""

    # ── 核心循环 (O-O-D-A) ─────────────────────────────

    def _try_visual_route(self, messages: list) -> tuple | None:
        """从消息列表中提取原始任务，尝试视觉路由。

        扫描 messages 找到最后的 user 消息内容，用 route_visual 匹配。
        """
        from .visual_router import route_visual
        # 找最后一个非 hint 的 user 消息
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                # 去掉注入的 hint 再匹配
                if "[Visual hint:" in content:
                    content = content.split("[Visual hint:")[0].strip()
                if content:
                    return route_visual(content)
        return None

    def _agent_loop(self, messages: list, exclude_tools: list | None = None,
                    system_prompt: str | None = None,
                    step_callback: Callable[[str, list], str | None] | None = None,
                    tool_callback: Callable[[str, dict, str, bool], None] | None = None,
                    ) -> tuple[str, list]:
        """
        Agent 核心循环：O-O-D-A

        Observe (工具返回) → Orient (解读) → Decide (LLM) → Act (执行工具)

        视觉路由：首次调用时检查 route_visual，命中则注入 hint 引导 LLM 选对工具。
        所有策略（Default/ReAct/Plan-Execute/Reflexion/ToT）均受益。

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
        response: dict[str, Any] | None = None  # 防止 max_iterations=0 时 NameError

        # ── 视觉路由预检：首次进入 agent_loop 时注入 hint ──
        route_exclude = []
        if not getattr(self._local, 'visual_routed', False):
            self._local.visual_routed = True
            route = self._try_visual_route(messages)
            if route:
                tool_name, tool_params = route
                # 直接可执行的 chart 类型（无需 data）
                _NO_DATA = {"geometry", "wireframe", "contour", "cat", "regression", "function"}
                can_direct = (
                    tool_name == "generate_chart"
                    and tool_params.get("chart_type", "") in _NO_DATA
                )
                if can_direct:
                    self._emit("text", {"text": "🎨 正在生成图表..."})
                    # geometry 类型需要 labels 关键词来选择演示图
                    if tool_params.get("chart_type") == "geometry" and "labels" not in tool_params:
                        # 从最后一条 user 消息提取任务文本作为 labels
                        last_user = next(
                            (m.get("content", "") for m in reversed(messages)
                             if m.get("role") == "user"), ""
                        )
                        if last_user:
                            tool_params["labels"] = last_user[:200]
                    import json as _json
                    tool_call = {"name": tool_name, "arguments": tool_params, "id": "routed_visual"}
                    messages.append({
                        "role": "assistant", "content": "",
                        "reasoning_content": "",  # DeepSeek 思考模式要求回传
                        "tool_calls": [{
                            "id": "routed_visual", "type": "function",
                            "function": {"name": tool_name,
                                          "arguments": _json.dumps(tool_params, ensure_ascii=False)},
                        }],
                    })
                    self.execute_tool(tool_call, messages)
                else:
                    # 需要 LLM 生成内容 → 注入 hint 到最后一个 user 消息
                    params_hint = f" (params hint: {tool_params})" if tool_params else ""
                    hint = (
                        f"\n[Visual hint: Use the '{tool_name}' tool{params_hint}."
                        f" Generate appropriate content.]"
                        f"\nIMPORTANT: Do NOT use generate_chart, mermaid_chart, draw_circuit,"
                        f" or any other visual tool for this task. Only use '{tool_name}'."
                    )
                    # 排除其他视觉工具，避免 LLM 选错
                    _ALL_VISUAL = {"generate_chart", "mermaid_chart", "draw_circuit",
                                   "create_ppt", "ai_image", "image_analyze"}
                    route_exclude = [t for t in _ALL_VISUAL if t != tool_name]
                    schemas = [s for s in schemas if s["function"]["name"] not in route_exclude]
                    if messages and messages[-1].get("role") == "user":
                        messages[-1]["content"] += hint

        loop_start = _time.monotonic()
        for _ in range(self.config.max_iterations):
            # ── 全局超时检查 ──
            if _time.monotonic() - loop_start > self.config.agent_timeout:
                logger.warning(
                    f"Agent timeout ({self.config.agent_timeout}s) reached after "
                    f"{_} iterations"
                )
                timeout_msg = (
                    f"⚠️ Agent timeout ({self.config.agent_timeout}s) reached. "
                    "Try simplifying the task or increasing AGENT_TIMEOUT."
                )
                if response and response.get("text"):
                    return f"{response['text'][:500].strip()}\n\n{timeout_msg}", messages
                return timeout_msg, messages

            # ── Decide: LLM 决策 ──
            response = self.llm.chat(
                messages=messages,
                tools=schemas,
                system=prompt,
                model=getattr(self._local, "model_override", None),
            )

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": response["text"]}
            # DeepSeek reasoner: 保留 reasoning_content（仅 DeepSeek 需要回传）
            if response.get("reasoning_content") and self.config.provider == "deepseek":
                assistant_msg["reasoning_content"] = response["reasoning_content"]
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
                # enable_orient=True: execute_tool 内部统一处理 Orient，
                # 不再在 _agent_loop 中硬编码 Orient 逻辑。
                info = self.execute_tool(tc, messages, enable_orient=True)
                if tool_callback:
                    tool_callback(info["name"], tc.get("arguments", {}),
                                  info["result"], info["success"])
            else:
                # 并行执行 — 委托给策略实例（BaseStrategy.execute_tools_parallel 是唯一实现）
                strategy_inst = self._local.strategy_instance
                if strategy_inst:
                    strategy_inst.execute_tools_parallel(
                        response["tool_calls"], messages,
                        tool_callback=tool_callback)
                else:
                    # 兜底：策略实例不存在时（不应该发生）
                    from .strategies.base import BaseStrategy
                    helper = BaseStrategy(context=self._make_strategy_context())
                    helper.execute_tools_parallel(
                        response["tool_calls"], messages,
                        tool_callback=tool_callback)


        if response is None:
            return "No iterations executed (max_iterations=0).", messages

        logger.warning(f"Max iterations ({self.config.max_iterations}) reached, "
                        f"forcing stop. Last tool: "
                        f"{response.get('tool_calls', [{}])[0].get('name', 'N/A') if response.get('tool_calls') else 'N/A'}")
        partial = response["text"][:500].strip() if response and response.get("text") else ""
        hint = (
            f"Reached max iterations ({self.config.max_iterations}). "
            "Try simplifying the task or breaking it into smaller steps."
        )
        if partial:
            return f"{partial}\n\n⚠️ {hint}", messages
        return hint, messages

    # ── Orient 阶段 ─────────────────────────────────────

    def _orient(self, observation: str, task: str = "") -> dict | None:
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
                model=getattr(self._local, "model_override", None),
            )
            self._local.last_orientation = orientation
            return orientation
        except Exception as e:
            logger.warning(f"Orient failed: {e}")
            return None

    # ── 消息构建 ────────────────────────────────────────

    def _load_builtin_rules(self) -> str:
        """加载内置规则文件（rules/system_rules.md）。缓存避免重复读盘。"""
        cache_attr = '_builtin_rules_cache_inst'
        cache_val = getattr(self, cache_attr, None)
        if cache_val is not None:
            return cache_val
        # 搜索项目根目录下的 rules/system_rules.md
        candidates = [
            Path(__file__).parent.parent / "rules" / "system_rules.md",  # 项目根/rules/
            Path.cwd() / "rules" / "system_rules.md",
        ]
        for p in candidates:
            if p.is_file():
                try:
                    setattr(self, cache_attr, p.read_text(encoding="utf-8").strip())
                    return getattr(self, cache_attr)
                except OSError:
                    pass
        setattr(self, cache_attr, "")
        return getattr(self, cache_attr)

    def _system_prompt(self) -> str:
        # Cache: rules 和 persistent 各自有缓存，这里缓存拼接结果避免重复构建
        rules = self.orient_engine.load_rules()
        persistent = self.memory.load_persistent()

        # 加载内置规则（rules/system_rules.md）
        builtin_rules = self._load_builtin_rules()

        cache_key = (hash(rules), hash(persistent), hash(builtin_rules))
        cached = getattr(self._local, 'prompt_cache', None)
        cached_key = getattr(self._local, 'prompt_cache_key', ())
        if cached is not None and cached_key == cache_key:
            return cached

        parts = [
            "You are Sleeping fox (睡狐), an AI assistant developed for this platform. "
            "You are powered by large language models and equipped with tools to help users. "
            "Be concise, helpful, and act decisively. When asked who you are, say you are Sleeping fox.",
            "Keep answers reasonably short — use bullet points and tables over long paragraphs. Don't over-explain, skip filler.",
            "",
            builtin_rules,
        ]
        if rules:
            parts.append(f"\n# Rules\n{rules}")
        if persistent:
            parts.append(f"\n# Previous Context\n{persistent}")
        result = "\n".join(parts)
        self._local.prompt_cache = result
        self._local.prompt_cache_key = cache_key
        return result

    # ── 便利方法 ────────────────────────────────────────

    def clear_memory(self):
        self.memory.clear()
        self._local.last_orientation = None
        logger.info("Memory cleared.")

    @property
    def memory_summary(self) -> str:
        return self.memory.get_summary()

    @property
    def last_orientation(self) -> dict | None:
        return getattr(self._local, 'last_orientation', None)
