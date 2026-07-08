# Nano Agent Plus — 架构文档

## 一、项目概览

Nano Agent Plus 是一个多策略 AI Agent 框架，融合 O-O-D-A 循环、六种推理策略、四级记忆系统和 20+ 工具，支持多 LLM 后端（Anthropic / OpenAI / DeepSeek / OpenRouter / Ollama / 智谱），提供 CLI 和 FastAPI + SSE 流式 Web UI（"Sleeping fox"）。

```
核心代码: ~8,400 行 Python
模块: 55 个
测试: 332 个
策略: Default / ReAct / PlanExecute / ToT / Reflexion / Meta
后端: Anthropic / OpenAI / DeepSeek / OpenRouter / Ollama / Zhipu
```

## 二、目录拓扑

```
nano_agent_plus/
│
├── run.py                    # CLI 入口（交互模式 + 单次任务）
├── start_server.py           # Web UI 启动脚本
│
├── nano_agent/               # 🧠 核心引擎
│   ├── agent.py              #   Agent 主类 + OODA 循环 + 路由管线
│   ├── config.py             #   配置管理（环境变量 → dataclass 单例）
│   ├── llm.py                #   LLM 抽象层（重试 + 流式 + JSON 解析）
│   ├── memory.py             #   四级记忆系统
│   ├── orient.py             #   OODA 的第二个 O：结构化解读
│   ├── intent_router.py      #   意图分类（qa / code / circuit）
│   ├── model_router.py       #   按复杂度选模型
│   ├── profiles.py           #   Agent 角色配置（工具过滤 + prompt）
│   ├── visual_router.py      #   图表工具预路由（省 token）
│   │
│   ├── strategies/           # 🎯 6 种推理策略
│   │   ├── base.py           #   策略基类 + 并行工具执行
│   │   ├── context.py        #   StrategyContext（依赖注入契约）
│   │   ├── default.py        #   默认策略（流式快速路径）
│   │   ├── react.py          #   ReAct（Thought → Action → Obs）
│   │   ├── plan_execute.py   #   规划→逐步执行→评估→重规划
│   │   ├── reflexion.py      #   自我反思 + 失败重试 + 教训学习
│   │   ├── tree_of_thought.py#   多路径探索 → 评估 → 回溯
│   │   └── meta.py           #   全自动流水线（分析→选策略→升级）
│   │
│   ├── tools/                # 🔧 20+ 工具
│   │   ├── __init__.py       #   ToolRegistry（自动注册 + schema 管理）
│   │   ├── shell.py          #   bash 执行（白名单安全模式）
│   │   ├── file_ops.py       #   文件读写（工作目录沙箱）
│   │   ├── sandbox.py        #   路径沙箱
│   │   ├── search.py         #   Web 搜索
│   │   ├── fetch.py          #   URL 抓取
│   │   ├── weather.py        #   天气查询
│   │   ├── stock_*.py        #   股票（行情/图表/指标/市场）
│   │   ├── chart/            #   matplotlib 图表（basic/advanced/special）
│   │   ├── diagram.py        #   Mermaid 流程图
│   │   ├── circuit.py        #   schemdraw 电路图（数字/模拟/框图）
│   │   ├── logic_svg.py      #   逻辑门 SVG
│   │   ├── analog/           #   模拟电路子系统
│   │   │   ├── analog_svg.py       # SVG 渲染引擎（105KB，最大文件）
│   │   │   ├── spice_simulator.py  # SPICE 仿真引擎
│   │   │   ├── spice_renderer.py   # 仿真结果可视化
│   │   │   └── spice_common.py     # 共享工具
│   │   ├── digital/          #   数字电路子系统
│   │   │   ├── digital_circuit.py  # NL→Verilog→Sim→Synth→SVG
│   │   │   ├── verilog_compiler.py # iverilog 编译
│   │   │   └── verilog_synthesizer.py # yosys 综合
│   │   ├── ppt.py            #   PowerPoint 生成
│   │   ├── excel.py          #   Excel 生成
│   │   ├── ai_image.py       #   AI 图像生成
│   │   ├── image_analyze.py  #   视觉模型图像分析
│   │   ├── document_parse.py #   文档解析
│   │   ├── safe_math.py      #   安全数学计算
│   │   └── observation.py    #   工具结果数据结构
│   │
│   └── providers/            # 🔌 LLM 后端
│       ├── base.py           #   BaseProvider + ProviderRegistry
│       ├── anthropic.py      #   Anthropic 原生 SDK
│       ├── openai.py         #   OpenAI 兼容协议（含 DeepSeek/OpenRouter/Ollama）
│       └── zhipu.py          #   智谱 GLM
│
├── web/                      # 🌐 Web UI（FastAPI + SSE）
│   ├── server.py             #   832 行：路由 + SSE 流式 + 会话管理
│   └── static/               #   前端静态资源 + KaTeX + charts 输出目录
│
├── tests/                    # 🧪 332 个测试
│   ├── test_strategies.py    #   40 个策略测试（全覆盖）
│   ├── test_agent.py         #   Agent 核心循环测试
│   ├── test_analog_circuit.py#   70 个模拟电路模板测试
│   ├── test_digital_circuit.py#  数字电路回归测试
│   └── ...                   #   config / llm / memory / orient / tools / web
│
├── rules/                    #   用户自定义规则（Markdown）
├── scripts/                  #   独立脚本（评估/演示/股票分析）
└── charts/                   #   图表输出目录
```

## 三、一次请求的完整生命周期

### ① Intent Router（意图分类）

```
intent_router.classify(task, llm)

Layer 1: 正则关键词（0 LLM, ~70% 命中）
  circuit:  电路|滤波器|放大器|运放|bode|spice|verilog|逻辑门...
  code:     写代码|编程|debug|python|javascript|修复|重构...
  → 命中则返回，否则进入 Layer 2

Layer 2: LLM 分类（1 LLM, ~30% 兜底）
  → "qa" | "code" | "circuit"
```

### ② Agent Profile（角色配置）

| Profile  | 排除的工具                                | Prompt 前缀       | 默认策略 |
|----------|------------------------------------------|-------------------|----------|
| `qa`     | bash, write, edit, 所有电路, PPT/Excel   | "知识助手"         | default  |
| `code`   | 电路仿真（analog/digital/spice）          | "编程助手"         | auto     |
| `circuit`| 天气, 股票, PPT, Excel, AI图像           | "电路设计助手"      | auto     |

### ③ Model Router（模型选择）

```
按复杂度（长任务/含分析关键词）+ profile 选模型:
  MODEL_QA_SIMPLE / MODEL_QA_COMPLEX / MODEL_CODE_SIMPLE
  / MODEL_CODE_COMPLEX / MODEL_CIRCUIT

未配置 → 用全局默认模型
```

### ④ 策略选择

```
如果 strategy 是具体策略名 → 直接使用

如果 strategy == "auto":
  Step A: profile.default_strategy != "auto" → 直接用

  Step B: 关键词匹配（按 auto_priority 降序，0 LLM）:
          Reflexion(4) > PlanExecute(3) = ToT(3) > ReAct(2) > Meta(1) > Default(0)
          Default 额外检查: 任务 ≥150字 且 不含QA关键词 → 跳过

  Step C: LLM 分类（关键词全不命中时，1 LLM）:
          传入 intent 上下文，LLM 知道当前领域特征再分类
```

### ⑤ 策略执行

| 策略 | 执行模式 | 特点 |
|------|---------|------|
| **Default** | 流式 LLM → 无工具直接返回 / 有工具切 agent_loop | 纯文本智能画图检测 |
| **ReAct** | Thought → Tool Call → Observation → ... → Final Answer | 显式可见推理链 |
| **PlanExecute** | 分解 3-5 步 → 逐步执行 → 评估 → 失败重规划 | pipeline_state 复用前序产物 |
| **Reflexion** | 执行 → 智能评估 → 反思 → 重试(带教训) | 简单任务短路 |
| **ToT** | 生成 N 候选 → 批量评分 → 最优优先 → 失败回溯 | 简单任务只走 1 条 |
| **Meta** | 分析 → 选子策略 → 执行 → 评估 → 升级重试 | 跨策略 pipeline_state 共享 |

### ⑥ 核心 O-O-D-A 循环

```
Visual Router 预检:
  can_direct → 跳过 LLM，直接构造 tool_call 执行
  need_llm   → 注入 hint + 裁剪 schema（省 ~1800 tokens）

for i in range(max_iterations):
  ┌─ Decide:  LLM.chat(messages, tools=schemas)
  │           返回 text 或 tool_calls
  │
  ├─ 无 tool_call → 返回 text，结束
  │
  ├─ Act:     单工具 → execute_tool
  │           多工具 → ThreadPoolExecutor 并行
  │           ├─ 图表视觉验证（AGENT_CHART_VERIFY）
  │           └─ 电路文本审查（LLM 检查 DSL/SPICE 匹配用户意图）
  │
  └─ Orient:  工具结果 ≥200 字 → LLM 结构化解读
              interpretation + association + implication + confidence

全局超时保护 + max_iterations 上限 + 事件推送到 Web UI
```

### ⑦ 记忆持久化

```
save_context()        → 窗口记忆（deque, 最近 N 轮）
save_persistent()     → 文件记忆（agent_memory.md）
save_reflection()     → 反思轨迹（reflexion_trace.db SQLite）
```

## 四、策略体系速查

### 元数据总表

| 策略 | priority | uses_orient | 关键词（精简） | 适用场景 |
|------|:---:|:---:|------|---------|
| **Default** | 0 | ❌ | 天气/计算/翻译/什么是/如何/为什么... | 简单QA、知识问答 |
| **Meta** | 1 | ✅ | 全自动/autopilot/电路设计/芯片设计 | 全自动复杂流水线 |
| **ReAct** | 2 | ❌ | 逐步/step by step/推理过程/审计 | 需可见推理链的任务 |
| **PlanExecute** | 3 | ❌ | 制定计划/项目规划/多步骤任务/分步执行 | 多步骤复杂任务 |
| **ToT** | 3 | ❌ | 头脑风暴/创意/多种方案/最优/探索 | 多方案探索优化 |
| **Reflexion** | 4 | ✅ | 调试/修复/bug/质量/审查/验证 | 质量关键、需自审查 |

### 路由决策示例

| 输入 | Intent | 关键词命中 | 策略 | 原因 |
|------|--------|-----------|------|------|
| `今天天气怎么样` | qa | `天气` → Default | **default** | 关键词直接命中 |
| `什么是傅里叶变换` | qa | `什么是` → Default | **default** | 关键词直接命中 |
| `帮我写一个Python爬虫` | code | 无命中 | **LLM分类** | code意图上下文 |
| `调试这个segfault` | code | `调试` → Reflexion | **reflexion** | 关键词命中 priority=4 |
| `逐步排查这个性能瓶颈` | code | `逐步` → ReAct | **react** | 关键词命中 priority=2 |
| `制定一个微服务迁移计划` | code | `制定计划` → PlanExecute | **plan-execute** | 关键词命中 priority=3 |
| `什么是运放` | circuit | `什么是` → Default | **default** | 关键词命中（不再强制Meta） |
| `设计一个低通滤波器` | circuit | 无命中 | **LLM分类** | circuit上下文→推荐meta/plan-execute |
| `电路设计一个两级运放` | circuit | `电路设计` → Meta | **meta** | 新增关键词触发 |

### 关键词冲突处理

当多个策略关键词同时命中时，**priority 高的赢**。同优先级按 registry 注册顺序。

## 五、Meta 策略内部路由

### 初始策略选择（select_strategy）

```
quality_critical 或 complexity ≥ 7  →  reflexion
steps ≥ 3 或 complexity ≥ 5:
    domain=code  →  react（需要可审计推理）
    其他         →  plan-execute
domain=creative  →  tree-of-thought
其他             →  default
```

### 失败升级链（_upgrade_strategy）

```
严格单向，永不回退：

score < 3:   任意策略 ──→ reflexion（直接跳到最强）
score 3-4:   default → react → plan-execute → tree-of-thought → reflexion → [停留]
score ≥ 5:   不变（当前策略仍有希望）

升级链：["default", "react", "plan-execute", "tree-of-thought", "reflexion"]
reflexion 到达后留在原地，通过累积教训 + max_retries 收敛，不再回退到 plan-execute
```

### 子策略间通信（pipeline_state）

```
StrategyContext.pipeline_state (dict) + pipeline_lock (threading.Lock)

约定（按策略名命名，避免冲突）:
  pipeline_state["tot"]       — Tree-of-Thought: candidates, explored_paths
  pipeline_state["plan"]      — PlanExecute: steps, step_results
  pipeline_state["reflexion"] — Reflexion: lessons
  pipeline_state["meta"]      — Meta: attempts 记录

PlanExecute/Reflexion 执行前从 pipeline_state 读取前序策略产物，
避免重复探索，节省 token。
```

## 六、四级记忆系统

```
┌─────────────┐  ┌──────────────┐  ┌─────────────────┐  ┌──────────────────┐
│ 窗口记忆     │  │ 持久记忆      │  │ 反思追踪         │  │ 长期记忆          │
│ deque(N)    │  │ .md 文件     │  │ SQLite DB       │  │ SQLite FTS5      │
│             │  │              │  │                 │  │                  │
│ 最近 N 轮   │  │ 文件形式      │  │ 结构化的尝试/    │  │ 全文搜索检索      │
│ 对话上下文   │  │ 持久化上下文   │  │ 评估/教训记录    │  │ 跨会话知识        │
│             │  │              │  │                 │  │                  │
│ 每次注入    │  │ system prompt│  │ Reflexion 策略   │  │ Reflexion 开启    │
│ messages   │  │ 拼接注入      │  │ 内部使用          │  │ load_relevant()  │
└─────────────┘  └──────────────┘  └─────────────────┘  └──────────────────┘
```

## 七、LLM Provider 架构

```
LLM.chat() / chat_stream()
    │
    ▼
ProviderRegistry.resolve_provider(provider_str, config)
    │
    ├── "anthropic" → AnthropicProvider  (原生 SDK, tool_use block)
    ├── "openai"    → OpenAIProvider     (chat/completions, native FC)
    ├── "deepseek"  → OpenAIProvider     (base_url 指向 DeepSeek)
    ├── "openrouter"→ OpenAIProvider     (base_url 指向 OpenRouter)
    ├── "ollama"    → OpenAIProvider     (base_url 指向本地 Ollama)
    └── "zhipu"    → ZhipuProvider      (智谱 GLM API)

自动模型→Provider 解析:
  claude-* / anthropic.*  →  AnthropicProvider
  gpt-* / o1-* / o3-*    →  OpenAIProvider
  deepseek-*              →  OpenAIProvider (DeepSeek base_url)
  glm-* / chatglm-*       →  ZhipuProvider

统一接口:  {text, tool_calls, usage}
重试策略:  429/5xx/rate_limit/timeout → 最多 3 次指数退避
流式:      chat_stream() → yield text chunk | tool_calls signal
```

## 八、工具注册机制

```
ToolRegistry.__init__()
    │
    ├── _MODULE_MAP 定义所有工具类 → 实例映射
    │
    ├── _auto_register(): 遍历实例，读 TOOLS 类属性
    │   TOOLS = [(name, desc, method, properties, required), ...]
    │
    │   条件注册:
    │     enable_analog_circuit → analog_svg / spice_renderer / spice_simulator
    │     enable_digital_circuit → digital_circuit
    │     public_mode → 过滤危险工具（bash/edit/write）
    │     Profile excluded_tools → 按领域过滤无关工具
    │
    └── _register_manual_overrides(): 复合工具（search_and_fetch）

    → self._cached_schemas (每次 LLM 调用复用，避免重复构建)
```

## 九、Web UI 架构

```
FastAPI + SSE Streaming（"Sleeping fox"）

POST /api/chat              ← 核心：SSE 流式对话
    │
    ├── get_or_create_session()
    │   ├── 内存 dict（会话 → Agent 实例 + 历史）
    │   ├── SQLite 持久化（session_history 表，WAL 模式）
    │   ├── Session 隔离：Config.with_overrides(work_dir=session_dir)
    │   ├── TTL 淘汰（2h）+ LRU 淘汰（100 上限）
    │   └── 锁外构建 Agent，锁内写 dict（精细锁粒度）
    │
    ├── 限流：Semaphore(10) 并发上限 + IP 每日限额
    │
    └── agent_stream() generator:
        ├── Queue 收集 Agent 事件
        ├── 后台 daemon 线程运行 agent.run()
        ├── 主循环阻塞读 Queue（1s timeout）
        ├── SSE 事件：text | tool_call | tool_result | orient | done | error
        ├── 心跳 2.5s 防浏览器超时断连
        ├── 取消机制：用户新消息 → set cancel_event → 线程检测退出
        └── GeneratorExit → join 线程（5s 超时）→ 清理

POST /api/digital-circuit   ← 数字电路独立流水线
POST /api/upload            ← 文件上传到 session 目录（50MB 上限）
POST /api/survey            ← 用户反馈（JSONL 持久化）
GET  /api/sessions/:id      ← 会话历史/清除
GET  /api/health            ← 健康检查 + chart 定期清理（200 上限）
GET  /api/download/:file    ← 跨 session 文件搜索下载
GET  /charts/:file          ← 静态资源（自动 MIME + 路径穿越防护）
```

## 十、关键设计决策

| 决策 | 说明 |
|------|------|
| **Orient 显式化** | Observe 和 Decide 之间插入 LLM 解读，理解层和行动层解耦 |
| **StrategyContext 依赖注入** | 替代猴子补丁，策略与引擎之间显式契约。支持新旧两种构造模式 |
| **Visual Router 预路由** | LLM 决策前拦截图表请求，命中则跳过 LLM 直接执行或注入 hint+裁剪 schema，省 4000+ tokens/次 |
| **Profile 工具过滤** | 按领域（qa/code/circuit）从 schema 移除无关工具，缩小 LLM 选择空间 |
| **Meta 线性升级链** | default→react→plan→tot→reflexion，严格单向不回退，消除 reflexion⇄plan-execute 死循环 |
| **pipeline_state 跨策略共享** | Meta 中所有子策略共享一个 StrategyContext，避免 ToT 探索过的候选被 PlanExecute 重复计算 |
| **threading.local 请求隔离** | 每请求独立的 model_override、excluded_tools、profile_prompt，多线程并发安全 |
| **策略元数据驱动** | auto_keywords + auto_priority + default_params + uses_orient 全在策略类上声明，新增策略只需注册 + 设元数据，无需改 Agent |
| **懒加载贯穿始终** | Config 单例 / dotenv / Provider 懒初始化 / 规则缓存，减少启动开销 |
| **智能评估跳过** | Reflexion/Meta 对结果做快速启发式判断（成功/失败信号词），明显时跳过 LLM 评估 |
| **简单任务短路** | Reflexion/ToT 检测到简单任务时跳过反思/多路径探索，直接执行一次 |
| **Intent 注入 LLM 分类** | _auto_select_strategy 将 Intent Router 的领域标签（qa/code/circuit）传递给策略 LLM 分类器，提升分类准确率 |

## 十一、数据流全链路

```
User Input (CLI/Web)
    │
    ▼
Agent.run(task, strategy)
    │
    ├── Intent Router ──→ intent (qa/code/circuit)
    ├── Agent Profile ──→ excluded_tools + prompt_prefix
    ├── Model Router  ──→ model_override
    ├── Strategy Select──→ strategy instance
    │
    └── Strategy.run(task, agent_loop_fn)
        │
        ├── build_messages() ──→ [window memory, long-term (optional), task]
        │
        └── agent_loop_fn(messages)
            │
            ├── Visual Router 预检 ──→ hint / schema 裁剪 / 直接执行
            │
            └── OODA Loop (× max_iterations)
                ├── Decide: LLM.chat(messages, tools, system_prompt)
                ├── Act:    ToolRegistry.execute(tool_name, args)
                │           ├── 图表视觉验证
                │           └── 电路文本审查
                └── Orient: Orient.orient(obs, task, memory, rules)
                    → [Orient] interpretation + implication
                → 追加到 messages，循环
            │
            └── 返回 (final_text, messages)

    ├── Memory.save_context(task, final)
    ├── Memory.save_persistent(task, final)
    └── SSE: "done" event → Web UI
```

## 十二、配置环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENT_PROVIDER` | `anthropic` | LLM 后端 |
| `MODEL_NAME` | `claude-sonnet-4-6` | 模型名称 |
| `ANTHROPIC_API_KEY` | — | Anthropic API Key |
| `OPENAI_API_KEY` | — | OpenAI/DeepSeek API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容 Base URL |
| `AGENT_MAX_ITERATIONS` | `10` | 最大工具调用轮数 |
| `AGENT_AGENT_TIMEOUT` | `300` | Agent 全局超时（秒） |
| `AGENT_MEMORY_WINDOW` | `10` | 窗口记忆轮数 |
| `AGENT_ENABLE_ANALOG_CIRCUIT` | `true` | 启用模拟电路工具 |
| `AGENT_ENABLE_DIGITAL_CIRCUIT` | `false` | 启用数字电路工具 |
| `AGENT_CHART_VERIFY` | `false` | 图表视觉验证 |
| `AGENT_PUBLIC_MODE` | `false` | 公网安全模式 |
| `AGENT_ORIENT_MIN_CHARS` | `200` | Orient 触发的最低结果长度 |
| `AGENT_REACT_MAX_STEPS` | `10` | ReAct 最大步数 |
| `AGENT_REFLEXION_MAX_RETRIES` | `3` | Reflexion 最大重试 |
| `AGENT_TOT_CANDIDATES` | `3` | ToT 候选数 |
| `AGENT_TOT_SCORE_THRESHOLD` | `6` | ToT 成功阈值 |
| `MODEL_QA_SIMPLE` | — | QA 简单任务模型 |
| `MODEL_QA_COMPLEX` | — | QA 复杂任务模型 |
| `MODEL_CODE_SIMPLE` | — | Code 简单任务模型 |
| `MODEL_CODE_COMPLEX` | — | Code 复杂任务模型 |
| `MODEL_CIRCUIT` | — | Circuit 任务模型 |
| `WEB_PORT` | `8080` | Web UI 端口 |
| `MAX_CONCURRENT_AGENTS` | `10` | 最大并发 Agent 数 |
| `DAILY_LIMIT_PER_USER` | `0`（不限） | 每日每 IP 限额 |
| `WEB_ACCESS_CODE` | — | Owner 密码（豁免限流） |
| `MAX_UPLOAD_SIZE` | `52428800`（50MB） | 上传文件大小上限 |
| `BRAVE_SEARCH_API_KEY` | — | Brave Search API Key |
