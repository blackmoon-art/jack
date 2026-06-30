# Nano Agent Plus

融合 5 个项目的精华，从零构建的安全、可测试、多策略 AI Agent 框架。

## 项目定位

```
demo_1          demo_2          shared_AI       nanoAgent
(规则→LLM)     (FC→ReAct→记忆) (多后端实战)    (生产力工具)
    │               │               │               │
    └───────────────┴───────┬───────┴───────────────┘
                            │
                    ▼
              nano_agent_plus
              ───────────────
              工程化融合版本
```
## 初始架构
                    ┌─────────────────────────┐
                    │        run.py            │  CLI 入口
                    │  --strategy plan|ref|tot │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │        Agent             │  策略注册表路由
                    │  run(task, strategy)     │
                    └──┬────────┬──────────┬──┘
                       │        │          │
              ┌────────▼─┐ ┌───▼────┐ ┌───▼────────┐
              │ Default  │ │Plan-   │ │Reflexion   │  Tree-of-
              │ Loop     │ │Execute │ │(反思重试)  │  Thought
              └──────────┘ └────────┘ └────────────┘
                       │
         ┌─────────────┼─────────────┐
         │             │             │
    ┌────▼────┐  ┌────▼────┐  ┌─────▼─────┐
    │   LLM   │  │  Tools  │  │  Memory   │
    │ 多后端  │  │ 21个工具 │  │ 窗口+持久 │
    └─────────┘  └─────────┘  └───────────┘
## 改进后的架构

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              NANO AGENT PLUS                                     │
│                           "Sleeping Fox" AI Agent                                │
└─────────────────────────────────────────────────────────────────────────────────┘

                              入口层 (Entry Points)
┌──────────────────────────────┐  ┌──────────────────────────────────────┐
│         run.py (CLI)         │  │         web/server.py (FastAPI)       │
│  • 单次任务模式               │  │  • SSE 流式输出                       │
│  • 交互 REPL 模式             │  │  • Session 管理 (LRU, 2h TTL)        │
│  • --strategy 策略选择        │  │  • SQLite 历史持久化                  │
│  • 策略别名 (plan/ref/tot)    │  │  • 文件上传/下载 + Chart 静态服务      │
└──────────────┬───────────────┘  └──────────────────┬───────────────────┘
               │                                     │
               └──────────────┬──────────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │    Agent (核心)     │
                    │  OODA 循环编排器    │
                    └─────────┬──────────┘
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
  ┌────▼─────┐   ┌───────────▼──────────┐   ┌───────▼───────┐
  │ 策略注册表 │   │  Visual Router      │   │  Auto Select  │
  │ Registry  │   │  视觉请求路由         │   │  自动策略选择  │
  └────┬─────┘   │  关键词→意图→LLM     │   │ 关键词→LLM分类 │
       │         └──────────────────────┘   └───────────────┘
       │
       │        策略层 (Strategies)
       │   ┌──────────────────────────────────────────┐
       │   │  BaseStrategy (抽象基类)                   │
       │   │  • build_messages()   • execute_tool()    │
       │   │  • execute_tools_parallel()               │
       │   │  • 元数据: auto_keywords, auto_priority    │
       │   └──────────────────────────────────────────┘
       │
       │   ┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
       ├──►│  Default     │  ReAct        │ Plan-Execute │ Reflexion    │ Tree-of-     │  Meta
       │   │  (优先级0)   │  (优先级1)    │ (优先级3)    │ (优先级2)    │ Thought(优先2)│ (元策略)
       │   │              │               │              │              │              │
       │   │ 流式+可视检测│ Thought→Act  │ 计划→执行→   │ 尝试→评估→   │ 候选→评分→   │ 分析→选子
       │   │              │ →Obs循环     │ 评估→修正    │ 反思→重试    │ 择优执行     │ 策略→评估
       │   └──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
       │
       │        依赖层 (Core Services)
       │   ┌──────────────────────────────────────────────────────────────────────────┐
       │   │                                                                          │
       │   │  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
       │   │  │   LLM    │  │   Tools      │  │   Memory     │  │    Orient        │ │
       │   │  │ 多后端   │  │  21+ 工具    │  │  4 层记忆    │  │   理解引擎       │ │
       │   │  │          │  │              │  │              │  │                  │ │
       │   │  │•Anthropic│  │•Shell/Bash   │  │①Working Mem │  │ interpretation   │ │
       │   │  │•OpenAI   │  │•FileOps R/W  │  │ (窗口,FIFO) │  │ association      │ │
       │   │  │•DeepSeek │  │•Search/Fetch │  │              │  │ implication      │ │
       │   │  │•Ollama   │  │•Chart(14种)  │  │②Persistent   │  │ confidence       │ │
       │   │  │•OpenRoute│  │•Diagram      │  │ (Markdown)   │  │ focus            │ │
       │   │  │          │  │•Stock(5模块) │  │              │  │                  │ │
       │   │  │JSON Retry│  │•Weather      │  │③Long-Term    │  │ Rules 规则匹配   │ │
       │   │  │Streaming │  │•AIImage/Circ │  │ (SQLite FTS5)│  │ CJK Bigram分词  │ │
       │   │  │Retry     │  │•PPT          │  │              │  │                  │ │
       │   │  │          │  │•ImageAnalyze │  │④ReflexionTr  │  │                  │ │
       │   │  │          │  │•DocParse     │  │ (SQLite)     │  │                  │ │
       │   │  └──────────┘  └──────────────┘  └──────────────┘  └──────────────────┘ │
       │   │                                                                          │
       │   └──────────────────────────────────────────────────────────────────────────┘
       │
       │        配置层
       │   ┌──────────────────────────────────────────────────────────────┐
       │   │  Config (@dataclass, 从 .env 加载)                            │
       │   │  • 模型/Provider/API Key  • 策略参数  • 内存路径/窗口大小     │
       │   │  • 安全模式 (public_mode) • 限制参数  • with_overrides()     │
       │   └──────────────────────────────────────────────────────────────┘
       │
       │        数据层
       │   ┌──────────────────────────────────────────────────────────────┐
       │   │  SQLite DBs:  long_term_memory.db  |  reflexion_trace.db     │
       │   │              sessions.db (Web)                                │
       │   │  Files:      agent_memory.md  |  reflection_traces.md        │
       │   │  Charts:     charts/*.png (matplotlib 生成)                   │
       │   └──────────────────────────────────────────────────────────────┘
```

### 数据流（一次典型请求）

```
用户输入 task
      │
      ▼
┌──────────┐    ┌──────────────┐    ┌──────────────┐
│ ① Auto   │───►│ ② Strategy   │───►│ ③ OODA Loop  │
│ Select   │    │    Init       │    │              │
│ Strategy │    │ build_msgs()  │    │ LLM.chat()   │
│          │    │ load_memory() │    │   ↓          │
└──────────┘    └──────────────┘    │ tool_calls?  │
                                    │   ↓ YES      │
                                    │ execute()    │
                                    │   ↓          │
                                    │ orient()?    │
                                    │   ↓          │
                                    │ 循环/终止    │
                                    └──────────────┘
                                          │
                                    ┌─────▼─────┐
                                    │ ④ 返回结果 │
                                    │ save_mem() │
                                    └───────────┘
```

## 与 Claude Code 的对比

为什么有了 Claude Code 还要自己写 Agent 框架？两者在架构层面有本质区别。

### 对比总览

| | Claude Code | nano_agent_plus |
|---|:---:|:---:|
| **核心循环** | 单一 ReAct 循环 | OODA 循环（ReAct + Orient 结构化理解层） |
| **推理策略** | 1 种，统一处理 | 6 种，按任务难度自动匹配 |
| **推理可见性** | Thinking 对用户不可见 | ReAct 策略显式输出 Thought，完全可审计 |
| **工具结果处理** | 原始文本喂给模型 | Orient 解读后注入：`{interpretation, association, implication, confidence}` |
| **失败恢复** | 重试靠模型重新想出方案 | Reflexion 反思重试 + ToT 多路探索 + Meta 自动升级 |
| **记忆** | 上下文窗口 + 持久笔记 | 4 层：窗口(FIFO) / 持久文件 / FTS5 关键词检索 / 反思轨迹 |
| **策略间通信** | 不存在（单一策略） | `pipeline_state` 共享字典：前序策略的探索成果不丢弃 |
| **任务匹配** | 所有任务同一路径 | auto 模式：关键词 + LLM 分类，简单任务流式秒出 |
| **安全** | 沙箱执行 | 三道防线：`shlex+白名单` / 路径沙箱 / `ast` 安全解析 |
| **部署形态** | 终端 CLI + IDE 插件 | FastAPI + SSE 流式 + Web 聊天界面 |

### 同一个任务，不同的执行路径

```
任务: "设计一个高性能缓存方案"

Claude Code (单兵):
  [思考] → [搜索方案] → [写代码] → [返回]
  一条路走到底

nano_agent_plus (策略总部):
  ① Meta 分析: complexity=7, domain=creative → 选 ToT
  ② ToT: 生成 3 个候选 → 评分
      A: LRU+TTL 两级缓存 (9分)
      B: Redis 分布式缓存 (7分)
      C: Bloom Filter (3分) → 跳过
  ③ 执行 A → 不够好 → 升级到 PlanExecute
  ④ PlanExecute: 从 pipeline_state 读到 A 方案
      → 以 A 为基础做细粒度执行
      → 不重复探索
```

### 差异化价值

Claude Code 是**单兵作战的高手** — 效率高、擅长专注地搞定一件事。

nano_agent_plus 是**策略总部的参谋** — 动手前先问"这个任务值得多深地思考？"：
- 简单问答 → 流式秒出，不浪费
- 多步任务 → 分解执行，不乱抓
- 质量关键 → 反思重试，不妥协
- 方案不确定 → 多路探索，不回退同一思路
- 策略失灵 → Meta 自动升级，不撞南墙

**一句话：Claude Code 是万能扳手，nano_agent_plus 是一套可组合的工具箱。**

## 快速开始

```bash
cd nano_agent_plus
pip install -r requirements.txt

# 配置（Ollama 本地模型示例）ncat > .env << 'EOF'
AGENT_PROVIDER=openai
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
MODEL_NAME=qwen2.5:7b
EOF

# 交互模式
python run.py

# 单次任务
python run.py "列出当前目录的 Python 文件"

# 使用推理策略
python run.py --strategy plan "用 Python 写一个 Web 服务器"
python run.py --strategy reflexion "调试这个内存泄漏 bug"
python run.py --strategy tot "设计一个高性能缓存方案"
```

## Web 界面

```bash
pip install fastapi uvicorn
python web/server.py
# → http://localhost:8080
```

一个干净的聊天界面，支持 SSE 流式输出：

```
┌──────────────────────────────────────────────┐
│ 🤖 Nano Agent Plus   [策略▼]                 │
├──────────────────────────────────────────────┤
│ 🔧 bash({"command":"ls"})          ← 蓝色    │
│ agent.py config.py llm.py         ← 绿色     │
│ 💡 列出了3个文件                    ← 灰色    │
│ 当前目录包含 agent.py...           ← 最终回答 │
├──────────────────────────────────────────────┤
│ [输入任务...]                        [发送]  │
└──────────────────────────────────────────────┘
```

实时显示每一步：工具调用（蓝色）、工具结果（绿色）、Orient 解读（灰色）。

### 会话持久化

会话历史通过 SQLite 持久化到 `web/sessions.db`：

- **实时写入**：每条消息（user + assistant）即时存入 SQLite
- **重启恢复**：服务重启后，前端 localStorage 保存的 session ID 仍可匹配，后端自动从 DB 恢复历史
- **清除**：`DELETE /api/sessions/:id` 同时清内存和 DB

### 访问控制

```bash
# 设置访问码（可选）
echo "WEB_ACCESS_CODE=你的密码" >> .env
```

设置后，打开网页需要输入密码。不设则无限制。

### 分享给局域网内其他人

```
http://192.168.x.x:8080    ← 你的局域网 IP
```

### 分享给外网（需要 ngrok）

```bash
brew install ngrok
ngrok http 8080
# → https://xxx.ngrok-free.app → 发给任何人
```

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 聊天界面 |
| `POST` | `/api/chat` | SSE 流式聊天 `{"message","strategy","session_id","code?"}` |
| `GET` | `/api/health` | 健康检查 + 会话数 + Token 消耗 |
| `DELETE` | `/api/sessions/:id` | 清除会话记忆 |

### 使用次数限制

默认每人每天 20 次，数据持久化到 `web/usage.json`（已加入 .gitignore）：

```bash
# 自定义限制
DAILY_LIMIT_PER_USER=20    # 每人每天 20 次
# DAILY_LIMIT_PER_USER=0   # 不限

# 查看用量
curl http://localhost:8080/api/health
# {"today_usage": {"abc123": "5/20", "def456": "12/20"}, ...}
```

每天 0 点自动重置。

### 自动启动（macOS）

```bash
# 已配置 launchd 托管：开机自启 + 崩溃自动重启
launchctl list | grep nanoagent
tail -f /tmp/nano_agent_web.log    # 查看日志
```

### 架构细节

Agent 层新增 `on_event` 回调，Web 层通过事件队列 + 后台线程实现 SSE 流式推送，不改核心循环逻辑：

```
用户消息 → POST /api/chat
          → Agent.run(task, on_event=queue.put)
          → 后台线程运行 agent
          → 主线程从 queue 取事件 → SSE 发送到浏览器
```

## 推理策略

| 策略 | 命令 | 流程 | 适用场景 |
|------|------|------|---------|
| **Default** | `--strategy default` | LLM ⇄ Tools 循环直到完成 | 日常任务 |
| **ReAct** | `--strategy react` | Thought → FC Action → Observation → 循环, 推理完全可见 | 需要审计推理过程的任务 |
| **Plan-Execute** | `--strategy plan` | 分解→逐步执行→评估→必要时重规划 | 多步骤复杂任务 |
| **Reflexion** | `--strategy reflexion` | 执行→自评→反思→重试→教训累积 | 需要质量保证的试错型任务 |
| **Tree-of-Thought** | `--strategy tot` | 生成N候选→打分→执行最优→失败回溯 | 有多种解法的不确定任务 |

> **视觉路由下沉**: `Agent._agent_loop` 在 LLM 调用前自动检查视觉路由器 (`route_visual`)。命中时直接构造 tool_call (geometry/cat 等无需数据的类型) 或注入 hint 引导 LLM 选对工具。所有策略统一受益。
>
> **简单任务短路**: Reflexion/ToT 检测到纯推理/知识类任务 (<100 字 + 关键词匹配) 时跳过多轮循环，1 次 LLM 调用完成。

### 策略细节

**ReAct (FC 驱动版)：**
```
Thought: "需要列出文件" → [FC: bash(ls)] → Observation: "agent.py, README.md"
→ Thought: "已看到文件列表" → Final Answer: "目录包含2个文件"
```
使用 Native Function Calling 执行工具调用（可靠），Thought 在 content 中显式输出（可见）。
不再依赖正则文本解析，任何支持 FC 的模型都能稳定运行。

**Plan-Execute：**
```
Task → [Plan] → Step1 → Eval → Step2 → Eval(fail) → Revise → Step2a → Eval → Done
```

**Reflexion：**
```
Attempt1 → fail → Reflect("wrong approach, should try X") → Attempt2(with lesson) → success
```
失败时自动产生结构化反思：`WHAT WENT WRONG / ROOT CAUSE / FIX / LESSON`，教训跨任务累积。

**Tree-of-Thought：**
```
Generate 3 approaches → Score: A(9) B(7) C(3)
→ Execute A → fail(score 3) → Backtrack
→ Execute B → success(score 8) → Done
```

## 工具清单

| 工具 | 功能 | 安全设计 |
|------|------|---------|
| `bash` | 执行 shell 命令 | `shell=False` + `shlex` + 命令白名单前缀 |
| `read` | 读取文件（带行号） | 路径沙箱 (`realpath` 越界拒绝) |
| `write` | 写入文件 | 路径沙箱 |
| `edit` | 精确字符串替换（单次匹配） | 路径沙箱, 要求唯一匹配 |
| `glob` | 文件名模糊搜索 | 限于工作目录内 |
| `grep` | 正则搜索文件内容 | `shell=False` |
| `web_search` | Brave→DDG→Bing→SearXNG→Wikipedia 五级降级搜索 | SSL 完整验证 |
| `fetch_url` | 抓取网页全文，去标签提取文本 | 15s 超时，去 script/style，截断 8000 字符 |
| `search_and_fetch` | 搜索 + 自动抓取首个结果内容 | 复用 web_search + fetch_url |
| `calculate` | 数学表达式求值 | `ast` 安全解析，无 `eval` |
| `get_weather` | 查询城市实时天气 (Open-Meteo API) | 免费，无需 API Key |
| `stock_info` | 股票实时行情 (A股腾讯/Yahoo/腾讯fallback) | 免费，无需 API Key，国内可达 |
| `stock_history` | 股票历史日K数据 (A股 akshare / 美股 yfinance) | 免费，无需 API Key |
| `stock_chart` | 生成股票走势图/K线图 PNG (matplotlib) | 同股票同周期自动缓存 |
| `mermaid_chart` | 生成 Mermaid 图表 (流程图/时序图/类图等) | 输出 SVG，不执行 JS |
| `generate_chart` | 生成数学/数据图表 (折线/柱状/散点/等高线/波形等) | 无外部依赖 |
| `draw_shape` | 几何形状/简笔画 (cat/waveform/geometry) | 无外部依赖 |
| `circuit_diagram` | 电路原理图 (CircuitMacros 渲染) | 沙箱执行 |
| `diagram_fetch` | 抓取在线图解 (PlantUML/WebSequenceDiagrams) | URL 白名单 |
| `ai_image` | AI 图像生成 (本地/远程模型) | 模型路径沙箱 |
| `ppt_create` | 生成 PPT 演示文稿 (python-pptx) | 文件沙箱 |

工具返回 `Observation` 结构化对象（`tool_name`, `success`, `result`, `args`, `metadata`），Agent 可判断工具执行是否成功。同时兼容字符串操作（`__str__`/`__contains__`/`startswith` 委托到 `result`）。

## 记忆系统

```
┌─ Working Memory (内存) ──────────┐
│ 保留最近 N 轮对话                │
│ 自动淘汰旧消息                   │
│ 用于：上下文连贯性               │
└──────────────────────────────────┘
                +
┌─ Persistent Memory (文件) ───────┐
│ 追加写入 agent_memory.md         │
│ 跨会话保留                       │
│ 自动轮转 (上限 200 行)           │
│ 用于：长期知识累积               │
└──────────────────────────────────┘
                +
┌─ Reflection Memory (文件) ───────┐
│ 追加写入 reflection_traces.md    │
│ 跨会话保留                       │
│ 自动轮转 (上限 200 行)           │
│ 用于：反思教训复用               │
└──────────────────────────────────┘
                +
┌─ Long-Term Memory (SQLite) ───────┐
│ SQLite FTS5 全文搜索引擎          │
│ 持久化到 long_term_memory.db      │
│ CJK 二字滑窗分词 + BM25 排序      │
│ 用于：跨会话精确召回历史经验      │
└──────────────────────────────────┘
```

四层记忆由 `Memory` 门面统一管理，策略和 Agent 不感知存储细节。`load_relevant(query)` 方法自动从长期记忆中语义检索最相关的历史经验。

## 配置项

```bash
# ── LLM 后端 ──
AGENT_PROVIDER=anthropic          # anthropic | openai | deepseek | openrouter
ANTHROPIC_API_KEY=sk-ant-xxx
OPENAI_API_KEY=sk-xxx             # 也用于 DeepSeek/OpenRouter/Ollama
OPENAI_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-flash

# ── Agent 行为 ──
AGENT_MAX_ITERATIONS=10           # 最大工具调用轮数
AGENT_MAX_TOKENS=8000             # LLM 输出 token 上限
AGENT_BASH_TIMEOUT=120            # bash 命令超时 (秒)
AGENT_WORK_DIR=/your/project      # 工作目录（文件操作的根）
AGENT_ORIENT_MIN_CHARS=200        # Orient 触发阈值 (工具结果字符数)

# ── 记忆 ──
AGENT_MEMORY_WINDOW=10            # 会话窗口保留轮数
AGENT_MEMORY_FILE=agent_memory.md # 持久记忆文件路径
AGENT_REFLECTION_FILE=reflection_traces.md # 反思记忆文件路径

# ── 策略参数 ──
AGENT_REACT_MAX_STEPS=10          # ReAct 最大步数
AGENT_REFLEXION_MAX_RETRIES=3     # Reflexion 最大重试次数
AGENT_TOT_CANDIDATES=3            # ToT 候选方案数
AGENT_TOT_SCORE_THRESHOLD=6       # ToT 合格分数阈值
AGENT_RULES_DIR=.agent/rules      # 自定义规则 .md 目录

# ── 日志 ──
AGENT_LOG_LEVEL=INFO              # DEBUG|INFO|WARNING|ERROR
AGENT_LOG_FILE=                   # 可选: 日志文件路径 (默认仅 stderr)

# ── 搜索 ──
BRAVE_SEARCH_API_KEY=             # Brave Search API key (可选, 无则降级到免费源)

# ── Web ──
WEB_PORT=8080                     # Web 服务端口
WEB_ACCESS_CODE=                  # 访问码 (可选, 不设则无限制)
DAILY_LIMIT_PER_USER=20           # 每人每天使用次数 (0=不限)
```

## 安全设计（vs 源项目）

| 问题 | nanoAgent | demo_1 | demo_2 | shared_AI | **nano_agent_plus** |
|------|:---:|:---:|:---:|:---:|:---:|
| shell 注入 | 🔴 shell=True | 🔴 shell=True | 🔴 shell=True | 🔴 shell=True | 🟢 shell=False + 白名单 |
| 任意文件读写 | 🔴 无限制 | — | — | — | 🟢 路径沙箱 |
| eval() 执行 | — | 🔴 eval | 🔴 eval | — | 🟢 ast 解析 |
| API Key 泄露 | 🔴 run.sh | 🔴 3个文件 | 🟡 .env | 🔴 源码 | 🟢 .env + .gitignore |
| SSL 绕过 | — | — | 🔴 CERT_NONE | 🔴 CERT_NONE | 🟢 完整验证 |
| 命令黑名单 | — | 🟡 可绕过 | 🟡 可绕过 | 🟡 可绕过 | 🟢 白名单模式 |

## 运行测试

```bash
python -m pytest tests -v

# 输出：
# 143 passed in 12.29s
```

测试分布：

| 文件 | 测试数 | 覆盖内容 |
|------|:---:|------|
| `tests/test_tools.py` | 26 | bash 安全/超时, 路径沙箱, 文件读写/编辑, glob, grep, calculate, web_search |
| `tests/test_memory.py` | 16 | 窗口/持久/反思/LongTermMemory (含 CJK 分词) |
| `tests/test_agent.py` | 8 | Agent 循环, 未知工具回退, 最大迭代, 记忆集成, 规则加载 |
| `tests/test_orient.py` | 8 | Orient 解读, 规则加载/缓存/匹配 |
| `tests/test_strategies.py` | 27 | Plan-Execute(6), ReAct(8), Reflexion(6), Tree-of-Thought(7) |
| `tests/test_llm.py` | 17 | clean_json, format_tool_call, retry (429/500/400), chat_json_with_retry |
| `tests/test_config.py` | 8 | 默认值, env override, with_overrides, dotenv 线程安全, 单例 |
| `tests/test_visual_router.py` | 34 | 三层路由匹配, 关键词, 工具参数 |

全部使用 Mock LLM，不依赖真实 API，可在 CI 运行。

```bash
python -m pytest tests -v
# 143 passed in 12.29s
```

## 基准测试

```bash
python run_eval.py --strategies default,react,reflexion --tasks all
# default:    成功率 85%  平均 3.2 步  平均 2.1s
# react:      成功率 78%  平均 4.1 步  平均 2.8s
# reflexion:  成功率 92%  平均 5.3 步  平均 4.5s
```

| 组件 | 说明 |
|------|------|
| `TaskCase` | 测试用例 (id, 描述, 期望关键词) |
| `TaskResult` | 单次结果 (通过/失败, 耗时, 工具调用数) |
| `EvalReport` | 汇总统计 (成功率, 平均耗时) |
| `Benchmark` | 批量运行 + 策略对比 + 报告保存 |

## 设计逻辑

### 决策 1：每个模块只做一件事

```
一个 agent.py 1000 行 → 改工具可能崩全局
        vs
独立模块 + tools/ 包 → 改工具不改 LLM，改策略不改核心循环，加测试不改配置
```

### 决策 2：LLM 层统一返回格式

Anthropic 返回 content blocks (`{"type": "tool_use", ...}`)，OpenAI 返回 `tool_calls`。
`llm.py` 将两者统一为 `{"text", "tool_calls": [{id,name,arguments}], "stop_reason"}`。
上层代码（Agent Loop、所有策略）不感知后端差异。

消息格式转换：内部使用 OpenAI 风格格式，`_chat_anthropic()` 调用前通过
`_convert_messages_for_anthropic()` 自动转为 Anthropic content blocks：

```
内部格式 (OpenAI 风格)              Anthropic 格式
─────────────────────              ──────────────
{"role":"assistant",               {"role":"assistant",
 "tool_calls":[{...}]}              "content":[
                                      {"type":"tool_use",...}]}

{"role":"tool",                    {"role":"user",
 "tool_call_id":"...",              "content":[
 "content":"result"}                 {"type":"tool_result",...}]}
```

### 决策 3：安全纵深防御（三道防线）

```
bash:    shlex.split() → 白名单前缀 → shell=False
文件:    Path.resolve() → .relative_to(work_dir) → PermissionError
计算:    ast.parse() → 白名单运算符 → 无 eval
```

任何一道防线被绕过，下一道还能拦住。错误以**字符串返回**而非抛异常——
LLM 看到 `"Error: Timeout"` 会自己调整策略，而不是整个 Agent Loop 崩溃。

### 决策 4：策略是可插拔的控制流

5 种策略共用 `_agent_loop` 和 `ToolRegistry`，区别只有控制流。所有策略继承 `BaseStrategy` 基类，统一了接口约束（`run()` 方法签名）、事件回调（`self.emit()`）和 JSON 解析重试（`self._chat_json()`）。`Agent.run()` 通过 `STRATEGY_REGISTRY` 字典查表分发，新增策略只需实现类 + 在注册表加一行，不改 `agent.py` 核心循环：

| 策略 | 控制流 | 一句话 |
|------|--------|--------|
| default | LLM→tool→LLM→tool→...→done | 最快，推理隐式 |
| react | Thought→FC→Obs→Thought→...→Final Answer | 推理完全可见 |
| plan-execute | Plan→Step(eval)→Step(eval)→[失败Replan]→done | 多步复杂任务 |
| reflexion | Attempt→eval(fail)→Reflect→Attempt(带教训)→done | 质量敏感 |
| tree-of-thought | Candidates→Score→Best→[失败Backtrack]→done | 不确定任务 |

加新策略只需加文件，不改 `agent.py` 核心循环。

### 决策 5：Orient——在 Observe 和 Decide 之间插一层理解

```
没有 Orient:  工具结果 → LLM 自己理解
有 Orient:    工具结果 → Orient解读(interpretation/association/implication)
                       → LLM 带着结构化的理解做决策，更快更准
```

短结果 (<200字符) 跳过 Orient 以节省 token。

### 决策 6：Memory 四层门面设计

```
窗口记忆: 保留最近N轮对话(FIFO淘汰)，进程内，会话结束即丢 → 当前上下文连贯
持久记忆: 追加写入 agent_memory.md，跨会话保留，自动轮转(上限200行) → 长期知识累积
反思记忆: 追加写入 reflection_traces.md，跨会话保留，自动轮转 → Reflexion 教训复用
长期记忆: SQLite FTS5 全文检索，跨会话语义匹配 → 精确召回历史经验
```

### 决策 7：策略执行路径统一 vs 热路径特化

这是一个典型的**"干净架构 vs 实用性能"**的权衡。

**旧设计（2026-06-23 之前）：** Agent 对 default 策略走特殊路径 `_run_stream_default`——
Agent 亲自执行流式调用、画图关键词检测、LLM override 兜底，100 行逻辑全在 agent.py，
而 `DefaultStrategy.run()` 只有 4 行空壳。其他 4 个策略走 `_run_strategy` 统一路径。

这么做的原因是 default 占了 90%+ 调用量，流式快速路径让纯知识问答秒出首 token，
是一个合理的热路径优化。

**新设计（2026-06-29）：** 删除 `_run_stream_default`，所有策略无差别走 `_run_strategy`。

```
旧: Agent.run()
    ├─ if strategy == "default" → _run_stream_default()  ← Agent 亲自下场
    └─ else → _run_strategy()                             ← 策略自己跑

新: Agent.run()
    └─ _run_strategy()  ← 所有策略统一，Agent 只做编排
```

**为什么改？**

旧方式的代价随功能增长而累积：

1. **逻辑分裂**：default 的行为分散在两个文件——流式在 agent.py，策略定义在 default.py
2. **认知负担**：加新功能（token 统计、错误追踪）要在 Agent 和策略类两处实现
3. **对称性缺失**：Agent 对 4 个策略说"你自己跑"，对 default 说"你别动我帮你跑"

新方式牺牲了热路径优化的便利（DefaultStrategy 从 34 行涨到 155 行，本质是搬家不是删代码），
换来的是：**所有策略的完整逻辑都在各自的 `run()` 里，Agent 只是工具箱管理员，不知道也不关心每种策略内部怎么跑。**

**定性：** 以这个项目的体量（~3000 行），统一路径的收益大于热路径特化的性能收益。
旧方式的 100 行 Agent 代码 + 4 行策略空壳是最坏的中间状态——
要么回到旧方式但明确注释为热路径优化，要么往前走让所有策略公平对待。
选择往前走，因为 5 个策略已经足够多，架构的一致性优先级高于单个策略的微优化。

### 一次请求的完整流转

```
Agent.run(task, strategy)
├─ Memory.get_window_messages()    ← 加载会话历史
├─ _system_prompt()                ← 规则 + 持久记忆 (循环前构建一次)
│
├─ 策略路由 (统一走 _run_strategy)
│   └─ StrategyContext(config, llm, tools, memory,
│       │              emit, execute_tool, agent_loop,
│       │              orient_fn, system_prompt_fn)
│       │
│       └─ strategy_cls(context=ctx).run(task, agent_loop_fn)
│           │
│           ├─ DefaultStrategy      ← 流式→切agent_loop / 画图检测
│           ├─ ReActStrategy        ← step_callback提取Thought + Final Answer
│           ├─ PlanExecuteStrategy  ← 规划→执行→评估→重规划
│           ├─ ReflexionStrategy    ← 自评→反思→重试 (启用Orient)
│           └─ TreeOfThoughtStrategy ← 多候选→打分→回溯
│           │
│           └─ agent_loop_fn(messages, system_prompt=...,
│                            step_callback=..., tool_callback=...)
│               │
│               ┌────────────────────────────────────────┐
│               │  Decide: LLM.chat(messages, tools)     │
│               │    → step_callback(text, tool_calls)   │  ← 策略可拦截
│               │                                        │
│               │  Act:    tools.execute(name, args)     │
│               │    → tool_callback(name, result, ok)   │  ← 策略可观察
│               │                                        │
│               │  Orient: orient_engine.orient(result)  │
│               │    → 结构化解读注入 messages             │
│               │                                        │
│               │  Loop:  回到 Decide                    │
│               └────────────────────────────────────────┘
│
├─ Memory.save_context(task, result)         ← 存会话记忆
├─ Memory.save_persistent(task, result)      ← 存持久记忆
└─ (Reflexion 策略额外存 Memory.save_reflection()) ← 存反思记忆
```

## 阅读指南

按 5 层递进阅读，每层读懂再进下一层。遇到看不懂的先标记，后面会回来。

### 第 1 层：地基（~30 分钟）

```
1. config.py        (71行)  ← 最早读
   看: @dataclass Config, 13个环境变量
   问: 换一个模型改哪里？

2. memory.py        (120行)
   看: 三层记忆 — save_context(), load_persistent(), save_reflection()
   看: _append_with_rotation() 统一文件轮转
   问: 进程重启后窗口记忆还在吗？持久记忆和反思记忆呢？
```

### 第 2 层：LLM 抽象（~20 分钟）

```
3. llm.py           (202行)
   第1遍: chat() → 统一入口 + 3次重试
   第2遍: _chat_openai() + _chat_anthropic() → 两个后端怎么差异
   第3遍: _convert_messages_for_anthropic() → 消息格式转换
   问: 加一个新后端 (Gemini) 要改哪些？
```

### 第 3 层：工具系统（~40 分钟）← 模块化拆分, 按子模块读

```
4. tools/                       (1000行, 分 7 个子模块)
   a) sandbox.py    (19行)  → resolve() + relative_to() 怎么防越界
   b) file_ops.py   (91行)  → read/write/edit/glob/grep, 每步 sandbox.safe_path()
   c) shell.py      (106行) → bash: shlex → 白名单 → shell=False 三道防线
                              calculate: ast.parse 递归遍历, 无 eval
   d) search.py     (317行) → web_search 五级降级链 + fetch_url + search_and_fetch
   e) weather.py    (81行)  → geocode → forecast, 两次 HTTP
   f) stock.py      (212行) → stock_info/history/chart, Yahoo Finance + akshare + matplotlib
   g) __init__.py   (174行) → ToolRegistry 注册表 + __getattr__ 兼容委托
   问: LLM 让我执行 "rm -rf /"，哪道防线先拦住？
```

### 第 4 层：Agent 核心（~40 分钟）← 最重要的文件

```
5. agent.py         (265行)
   a) __init__() → 5 个组件怎么装配
   b) run()      → 策略注册表查表 STRATEGY_REGISTRY.get(strategy)
   c) _agent_loop() → O-O-D-A 四步:\        Decide: LLM.chat()
        Act:    tools.execute()
        Orient: orient_engine.orient()
        [结果注入 → 回到 Decide]
   d) _system_prompt() + _build_messages()
   问: messages 列表在 _agent_loop 中经历了什么变化？
   问: 新增一个策略需要改 agent.py 吗？(答: 不需要, 只需注册表加一行)
```

### 第 5 层：推理策略（~60 分钟）

```
6. strategies/base.py  (55行)  ← 先读, 理解接口契约
   看: BaseStrategy 基类, emit() 事件回调, _chat_json() JSON重试
   问: 新策略必须实现哪个方法？

7. react.py         (210行)  ← 最直观
   看: Thought(从content提取) → FC Action(可靠) → Observation → 循环
   看: emit() 调用 — Thought/Action/Observation 事件透传给 Web UI

8. reflexion.py     (185行)
   看: evaluate_result() → generate_reflection() → 教训跨任务累积

9. plan_execute.py  (162行)
   看: create_plan() → evaluate_step() → revise_plan() 失败重规划

10. tree_of_thought.py (262行)  ← 最复杂
    看: generate_candidates() → score_candidates(批量) → backtrack
```

### 辅助模块（最后扫）

```
10. orient.py       (170行)  orient() → {interpretation, association, implication}
11. logging_config.py (49行)  统一日志, stderr 输出, 环境变量控制
12. run.py          (144行)  interactive() + parse_args()
```

### 阅读地图

```
第1层  config.py → memory.py              地基
第2层  llm.py                              后端抽象
第3层  tools/ (7个子模块)                   工具系统 (最长)
第4层  agent.py                            核心循环 (最重要)
第5层  base.py → react.py → reflexion.py  策略 (读两个就够)
       → plan_execute.py → tree_of_thought.py
第6层  orient.py → logging_config.py → run.py   辅助
```

每层读完问自己的问题能回答出来，进下一层。

## 项目结构

```
nano_agent_plus/
├── run.py                           # CLI 入口
├── web/
│   ├── server.py                    # FastAPI 服务 (SSE 流式 + SQLite 会话持久化)
│   ├── static/index.html            # 聊天界面
│   ├── usage.json                   # 使用次数统计 (gitignored)
│   └── sessions.db                  # 会话历史持久化 (gitignored)
├── requirements.txt                 # 依赖
├── .env.example                     # 配置模板
├── .gitignore                       # 忽略 .env + 运行时文件
├── README.md
├── nano_agent/
│   ├── __init__.py                  # 包入口, 自动初始化 logging
│   ├── config.py                    # 环境变量配置
│   ├── logging_config.py            # 统一日志配置 (stderr, 可选文件日志)
│   ├── llm.py                       # 多后端 LLM (懒加载, 3次重试)
│   ├── tools/                       # 工具包 (模块化)
│   │   ├── __init__.py              #   ToolRegistry + 兼容委托
│   │   ├── sandbox.py               #   PathSandbox 路径沙箱
│   │   ├── file_ops.py              #   read, write, edit, glob, grep
│   │   ├── shell.py                 #   bash, calculate
│   │   ├── search.py                #   web_search, fetch_url, search_and_fetch
│   │   ├── weather.py               #   get_weather
│   │   └── stock.py                 #   stock_info, stock_history, stock_chart
│   ├── memory.py                    # 四层记忆门面 (Working+Persistent+Reflection+LongTerm)
│   ├── evaluation.py                # 基准测试 + 策略对比
│   ├── agent.py                     # Agent 核心 + 策略注册表路由 + 视觉路由预检 + threading.local
│   ├── orient.py                    # 显式 Orient 阶段 + 规则加载
│   ├── visual_router.py             # 三层视觉路由 (精确→关键词→模糊)
│   └── strategies/
│       ├── __init__.py              #   STRATEGY_REGISTRY 注册表
│       ├── base.py                  #   BaseStrategy 基类 + StrategyContext
│       ├── context.py               #   StrategyContext dataclass
│       ├── default.py               #   Default (流式→agent_loop / 画图检测)
│       ├── react.py                 #   ReAct (FC驱动, Thought显式可见, 事件透传)
│       ├── plan_execute.py          #   Plan-Execute (分解→执行→评估→重规划)
│       ├── reflexion.py             #   Reflexion (自评→反思→重试 + 简单任务跳过)
│       └── tree_of_thought.py       #   Tree-of-Thought (多候选→打分→回溯 + 简单任务跳过)
└── tests/
    ├── __init__.py
    ├── test_tools.py                # 工具单元测试
    ├── test_memory.py               # 记忆单元测试
    ├── test_agent.py                # Agent 循环测试
    ├── test_orient.py               # Orient 测试
    ├── test_strategies.py           # 策略测试
    ├── test_llm.py                  # LLM 测试 (JSON清理/重试/chat_json)
    ├── test_config.py               # Config 测试 (env/override/线程安全/单例)
    └── test_visual_router.py        # 视觉路由测试
```

## 核心代码量

| 模块 | 行数 | 职责 |
|------|:---:|------|
| `tools/` 包 | 1,500 | 21个工具, 分 10+ 个子模块 + Observation + 视觉路由 |
| `agent.py` | 520 | Agent 类 + O-O-D-A 循环 + 视觉路由预检 + threading.local 隔离 |
| `llm.py` | 449 | LLM 抽象 (Anthropic/OpenAI) + 智能重试 + Anthropic消息转换 |
| `visual_router.py` | 280 | 三层视觉路由 (精确匹配→关键词→模糊匹配) |
| `orient.py` | 170 | 显式 Orient: 解读→关联→规则→建议 |
| `memory.py` | 550 | 四层记忆门面 + CJK 二字滑窗 FTS5 检索 |
| `config.py` | 120 | 配置管理 (含策略参数 + Orient 阈值) |
| `logging_config.py` | 49 | 统一日志配置 |
| `strategies/base.py` | 248 | BaseStrategy 基类 (接口约束 + 事件回调 + JSON重试 + StrategyContext) |
| 5个策略文件 | 1,100 | Default + ReAct + Plan-Execute + Reflexion + ToT |
| **总计** | **~5,000** | 全部自己掌控 |

## 架构待提升

当前架构评分 **9/10**。以下按优先级列出待改进项：

### ✅ 已解决 (2026-06-30)

- ~~**P3: LLM 重试不区分错误类型**~~ → 已修复：只重试 429/5xx/timeout，400/401 直接 raise
- ~~**P3: Web 线程安全**~~ → 已修复：sessions dict 加锁，Agent 用 threading.local 隔离 per-request 状态
- ~~**P3: default 策略未注册**~~ → 已修复：DefaultStrategy 注册进 STRATEGY_REGISTRY
- ~~**P3: Orient JSON 解析未复用**~~ → 已修复：Orient 加 JSON 重试
- ~~**策略-引擎交互契约问题**~~ → 已修复：StrategyContext dataclass 统一注入
- ~~**视觉路由仅 Default 受益**~~ → 已修复：视觉路由下沉到 _agent_loop，所有策略受益
- ~~**Reflexion 无简单任务跳过**~~ → 已修复：简单任务检测 + 1 次调用完成
- ~~**FTS5 中文搜索无效**~~ → 已修复：CJK 二字滑窗分词

---

### 🔴 代码质量（已全部修复 ✅ 2026-06-30）

| # | 问题 | 位置 | 修复 |
|---|------|------|------|
| ~~1~~ | ~~**并行工具执行代码重复**~~ | ~~`agent.py:269` vs `base.py:146`~~ | ✅ 删除 `_execute_tools_parallel_inline`，Agent/DefaultStrategy 统一调用 `BaseStrategy.execute_tools_parallel` |
| ~~2~~ | ~~**访问私有属性**~~ | ~~`default.py:156`~~ | ✅ ToolRegistry 新增 `get_tool_names()` / `has_tool()` 公开 API |
| ~~3~~ | ~~**`messages[-1]` 无边界检查**~~ | ~~`base.py:207`~~ | ✅ 加 `isinstance(last, dict) and "content" in last` 防御检查 |

---

### 🟡 架构改进

| # | 建议 | 说明 |
|---|------|------|
| ~~4~~ | ~~**统一并行工具执行**~~ | ✅ **已解决**: 并行工具执行统一到 `BaseStrategy.execute_tools_parallel`，Agent 通过 `self._strategy_instance` 访问 |
| ~~5~~ | ~~**ToolRegistry 增加公开 API**~~ | ✅ **已解决**: 添加 `get_tool_names()` / `has_tool()`，消除私有属性访问 |
| ~~6~~ | ~~**Orient 做成可选中间件**~~ | ✅ **已解决**: `_agent_loop` 单工具路径改用 `enable_orient=True`，Orient 统一由 `execute_tool` 处理，不再硬编码 |
| 7 | ~~**策略间通信机制**~~ | ✅ **已解决 (2026-06-30)**: StrategyContext 新增 `pipeline_state` 共享字典，Meta 策略创建一次共享上下文，子策略通过 `pipeline_state` 读写中间结果 |
| 8 | **Agent 类拆分** | `agent.py` ~520 行承担 5 个职责：组件装配、任务编排、事件分发、核心循环、Prompt 构建。可拆出 `AgentLoop` 和 `PromptBuilder`，agent.py 只保留编排 |

---

### 🟢 功能增强

| # | 建议 | 说明 |
|---|------|------|
| 9 | **Human-in-the-Loop** | 在关键步骤（文件写入、网络请求、bash 执行）暂停等待用户确认，尤其在 `public_mode` 下很重要 |
| 10 | **Tool 热加载/热插拔** | 目前工具在 Agent 初始化时注册，运行中无法增减。做成动态注册可按场景按需加载工具 |
| 11 | **流式 tool_call 通知** | Default 策略流式阶段检测到 tool_call 时直接中断流，用户看不到 LLM 打算调什么工具。可在 SSE 事件中先发 `tool_calling` 事件 |
| 12 | **Memory 向量化检索** | 目前长时记忆用 FTS5 关键词匹配，对语义相近但用词不同的查询召回率低。可加轻量 embedding 检索（如 Ollama 本地 embedding 模型），与 FTS5 混合召回 |
| 13 | **请求级模型切换** | `llm.set_model()` 目前是实例级别（重新初始化 client），改成请求级别可避免并发问题 |
| 14 | **工具注册自动化** | 新增工具需手写 OpenAI function schema。可用装饰器或基类自动从类型注解 + docstring 生成 schema |

---

### 🔵 运维 / 工程化

| # | 建议 | 说明 |
|---|------|------|
| 15 | **加 `pyproject.toml`** | 目前只有 `requirements.txt`，没有标准化的项目元数据、依赖锁定、构建配置 |
| 16 | **加 CI Pipeline** | 现有 `tests/` 目录（143 tests）和 `scripts/evaluation.py`，但没有自动化 CI。加 GitHub Actions 跑测试 + 基准 |
| 17 | **Docker 化** | `DEPLOY.md` 已有部署文档，加 Dockerfile 让部署更标准化、可复现 |
| 18 | **请求 Tracing** | 加 trace_id 贯穿 Agent → LLM → Tools 全链路。目前 ReflexionTrace 只覆盖 reflexion 策略，其他策略无调用链追踪 |
| 19 | **日志结构化** | 目前日志为纯文本，改为结构化 JSON 日志便于采集和分析（保留 stderr 人类可读输出作为 fallback） |

## 更新日志

### 2026-06-30 (性能 & 架构优化)

- **#1 视觉路由下沉到 `agent_loop`**: `route_visual` 检查从 `DefaultStrategy.run()` 移到 `Agent._agent_loop()`，所有策略 (ReAct/Plan-Execute/Reflexion/ToT) 统一受益。命中时直接构造 tool_call (无需数据的图表类型) 或注入 hint 引导 LLM 选对工具。
- **#2 补 `llm.py` + `config.py` 单元测试**: 新增 `test_llm.py` (17 tests: JSON 清理 / tool_call 格式化 / 429/500/400 重试逻辑 / chat_json_with_retry) 和 `test_config.py` (8 tests: 默认值 / env override / with_overrides 不可变 / dotenv 线程安全 / 单例)。测试总数 118 → **143**。
- **#3 动态 tool schema 基础**: 视觉 hint 注入替代全量 schema，Phase 0 命中时省 ~1400 tokens schema 开销。`_agent_loop` 已支持 `exclude_tools` 参数，策略可按需裁剪。
- **#4 Per-request 状态隔离**: `Agent` 用 `threading.local` 存储 `on_event` / `model_override` / `visual_routed`，同一 Agent 实例的并发请求不再互相覆盖。
- **#5 Reflexion 简单任务跳过**: `_is_simple_task()` 检测纯推理/知识类任务 (<100 字 + 关键词)，跳过反思循环，1 次 LLM 调用完成 (原 4-7 次)。
- **#6 Memory FTS5 中文分词改进**: `LongTermMemory.search` 和 `search_lessons` 用 CJK 二字滑窗 (bigram) + 英文整词分词替代无效的 `query.split()`，中文搜索召回率大幅提升。
- **#7 策略间 Pipeline 通信**: `StrategyContext` 新增 `pipeline_state` 共享字典。Meta 策略创建一次共享上下文，所有子策略通过 `pipeline_state` 读写中间结果，避免重复探索。ToT 写入候选方案 → PlanExecute 读取并以此为基制定计划，改造前后对比：原来 ToT 探索的 3 个候选方案全部丢弃，PlanExecute 从零开始；现在 PlanExecute 直接复用最佳候选方案作为计划基础，跳过重复推理。改动 ~100 行，向后兼容。
- **#8 代码质量 & 架构改进 (3 合 1)**: (a) 统一并行工具执行 — 删除 `agent.py` 的 `_execute_tools_parallel_inline` 和 `default.py` 的薄包装，所有路径统一调用 `BaseStrategy.execute_tools_parallel`；(b) ToolRegistry 公开 API — 新增 `get_tool_names()` / `has_tool()`，消除 `_tools` 私有属性访问；(c) Orient 去硬编码 — `_agent_loop` 单工具路径改用 `enable_orient=True`，Orient 统一由 `execute_tool` 处理。净减 ~25 行。

### 2026-06-23 (上午重构)

- **TOOLS 声明式注册**：各工具子模块声明 `TOOLS` 类变量，`ToolRegistry.__init__` 自动遍历注册。加工具只改子模块不改 `__init__.py`。
- **BaseStrategy 统一 build_messages**：窗口记忆 + 长期记忆自动注入，所有策略一致。新增 `memory` 参数透传。
- **Agent.execute_tool 提取**：工具执行逻辑从 `_agent_loop` 抽成独立方法，可复用。
- **stock_indicators 独立文件**：从 `stock.py` 拆分。`_draw_shapes` 统一 draw/cat 图表。
- **Observation 独立**：`observation.py`，`ToolRegistry.execute` 统一返回 `Observation`。

### 2026-06-23 (午间)

- **搜索链优先级调整**：Bing（cn优先）→ DDG → Brave → SearXNG → Wikipedia。国内环境 Bing 最稳定，超时从 15s 降至 8s 实现快速降级。

### 2026-06-23 (凌晨续)

- **Decisive Cat 命名**：网页标题、FastAPI title、system prompt 统一改为 Decisive Cat（果断猫）。Agent 人设：简洁、果断、执行力强。
- **bash 白名单增强**：`bash` 加入白名单，支持 `bash -c "管道|脚本"` 复杂命令。`bash -c` 内层加 `_DANGEROUS_PATTERNS` 黑名单（`rm -rf /`/`mkfs`/`fork bomb`等），防止白名单被绕过。
- **venv 自动检测**：`Shell.__init__` 检测 `.venv/bin` 目录存在时自动注入 PATH 和 `VIRTUAL_ENV`，Agent 可直接使用项目虚拟环境中的 Python/工具。

### 2026-06-23 (凌晨打磨)

- **LLM 智能重试**：不再盲目重试所有异常。只重试可恢复错误（429 限速、5xx 服务端、网络超时），其余直接抛出。避免 API key 错误等致命问题被掩盖。
- **Default 策略进注册表**：`default` 不再硬编码 `_agent_loop`，和其他策略一样走 `STRATEGY_REGISTRY`。未知策略抛 `ValueError` 而非静默回退。
- **策略参数可配置**：`_strategy_defaults()` 从 Config 注入 `react_max_steps`、`reflexion_max_retries`、`tot_num_candidates`、`tot_score_threshold`，`.env` 可配。
- **Orient JSON 重试**：`_parse_orientation()` JSON 解析失败时重新让 LLM 生成，最多 3 次，而非直接回退到文本。
- **stock_info 分市场**：A 股走腾讯行情 API（国内可达，GBK 解码），美股/港股走 Yahoo Finance + 腾讯 fallback。无需 Key。
- **Web 线程安全**：`sessions` 全局 dict 加 `threading.Lock`，`agent_stream` 的 `item` 变量修复空指针风险。
- **history 持久化到 SQLite**：`db_save_message` / `db_load_history`，进程重启后从 DB 恢复会话历史。

### 2026-06-22 深夜续续续 (基准测试 + Observation + LongTermMemory)

- **Observation 结构化工具返回**：`shell.py` 新增 `Observation` dataclass（`tool_name`/`success`/`result`/`args`/`metadata`），`bash`/`calculate` 返回 `Observation`。Agent loop 用 `hasattr(success)` 兼容新旧格式，字符串操作委托到 `result`。
- **基准测试系统**：`evaluation.py` 提供 `TaskCase`/`TaskResult`/`EvalReport`/`Benchmark`，`run_eval.py` 支持策略对比、成功率/耗时/步数统计。
- **LongTermMemory**：SQLite FTS5 全文搜索引擎，`load_relevant(query)` 语义检索历史经验，跨会话精确召回。
- **Memory 四层门面**：Working + Persistent + Reflection + LongTerm。

### 2026-06-22 深夜续续 (架构文档建议 #3-#6)

- **Memory 三层门面**：`Memory` 从双层改为三层——Working（窗口）+ Persistent（任务历史）+ Reflection（反思教训）。新增 `save_reflection()` / `load_reflections()`，文件轮转逻辑抽取为 `_append_with_rotation()`。新增 `reflection_traces.md` 持久化反思轨迹。
- **Observation 层**：新建 `Observation` dataclass（`tools/shell.py`），结构化工具返回（含 `tool_name`/`success`/`args`/`metadata`）。`ToolRegistry.execute_observed()` 返回 `Observation`，`execute()` 保持返回 `str` 兼容旧代码。Observation 兼容字符串操作（`__str__`/`__contains__`/`__eq__`）。
- **Reflexion 轨迹持久化**：`ReflexionStrategy` 初始化时从 `reflection_traces.md` 加载历史教训，每次反思后持久化到文件。进程重启后教训不丢失，跨会话复用。
- **策略参数配置化**：`Config` 新增 6 个环境变量——`AGENT_REACT_MAX_STEPS`、`AGENT_REFLEXION_MAX_RETRIES`、`AGENT_TOT_CANDIDATES`、`AGENT_TOT_SCORE_THRESHOLD`、`AGENT_ORIENT_MIN_CHARS`、`AGENT_REFLECTION_FILE`。`Agent._strategy_defaults()` 从 Config 注入参数，策略 `__init__` 默认值改为从 config fallback。所有魔法数字可通过 `.env` 调整。

### 2026-06-22 深夜续 (架构审查 + 修复)

- **策略基类 `BaseStrategy`**：新建 `strategies/base.py`，定义统一接口契约（`run()` 抽象方法）、事件回调（`emit()`）、JSON 解析重试（`_chat_json()`）。4 个策略全部继承，`Agent._run_strategy` 透传 `_emit` 回调。
- **ReAct 事件透传**：ReAct 之前绕过 `agent_loop_fn` 自己调 LLM + tools，Web UI 看不到中间过程。现在加了 3 个 emit 点：Thought（text 事件）、Action（tool_call 事件）、Observation（tool_result 事件）。
- **max_iterations 警告**：`_agent_loop` 达到最大迭代次数时加 `logger.warning`，记录最后一次工具名，方便调试。
- **JSON 解析重试**：`BaseStrategy._chat_json()` 方法封装「调 LLM → 清理 → 解析 JSON → 失败重试」逻辑，默认重试 2 次。替换了 4 个策略中 8 处 `json.loads + except JSONDecodeError` 的静默降级模式。
- **持久记忆自动轮转**：`agent_memory.md` 超过 200 行时自动截断保留最近条目，避免无限增长（之前已膨胀到 1796 行）。`load_persistent()` 同步改为读最后 200 行。
- **system_prompt 循环前缓存**：`_agent_loop` 在进入循环前构建一次 `_system_prompt()`，不再每轮迭代重复读文件（10 轮迭代从 10 次文件读取降到 1 次）。

### 2026-06-22 深夜 (P1 架构重构)

- **拆分 `tools.py` (917行) → `tools/` 包 (7 个子模块)**：
  - `sandbox.py` (19行) — PathSandbox 路径沙箱
  - `file_ops.py` (91行) — read, write, edit, glob, grep
  - `shell.py` (106行) — bash, calculate
  - `search.py` (317行) — web_search, fetch_url, search_and_fetch + 五级搜索引擎
  - `weather.py` (81行) — get_weather
  - `stock.py` (212行) — stock_info, stock_history, stock_chart
  - `__init__.py` (174行) — ToolRegistry + `__getattr__` 兼容委托（旧测试零改动通过）
- **策略注册表 `STRATEGY_REGISTRY`**：替代 `agent.py` 的 4 个 `if/elif` 分支，新增策略只需在 `strategies/__init__.py` 注册表加一行
- **`requirements.txt` 补全**：新增 fastapi, uvicorn, akshare, yfinance, matplotlib
- **模型名迁移**：`deepseek-chat` → `deepseek-v4-flash`（前者 2026/7/24 弃用）
- **Web 会话 SQLite 持久化**：`web/sessions.db`，服务重启后自动恢复会话历史，`.gitignore` 加入 `sessions.db`
- **测试修复**：`MockLLM` 替代 `MagicMock`，修复 16 个策略测试 error，76 tests 全过

### 2026-06-22 晚

- **统一日志系统**：新增 `logging_config.py`，全项目 `print()` → `logging`（39 处）。日志输出到 stderr，不干扰 CLI stdout 和 Web SSE 流。支持 `AGENT_LOG_LEVEL` 和 `AGENT_LOG_FILE` 环境变量。
- **错误处理修复**：14 处静默 `except Exception: pass` → `logger.warning`/`logger.debug`。关键错误（Orient 失败、规则加载失败、持久记忆写入失败）不再被吞掉。
- **删除重复代码**：
  - 删除 `agent_noweb.py`（305 行）和 `generate_ppt.py`（425 行）——残留文件
  - 删除 `Agent._create_plan()` / `_handle_plan()`——与 `PlanExecuteStrategy` 重复
  - 删除 `Agent._load_rules()`——与 `Orient.load_rules()` 重复（统一到 Orient，有缓存）
  - 删除 `_agent_loop` 里的 `plan` 工具分支——不再递归调用
  - 删除 `tools.py` 的 `plan` 占位工具——返回 `__PLAN_TRIGGER__` 的旧设计残留
  - 删除 `plan_execute.py` 的 `_format_result()`——从未被调用
- **修复 Wikipedia 搜索 bug**：`results` 列表未初始化（之前被 `return []` 遮挡）
- **净减 783 行代码**（-921 / +138），76 个测试全部通过

### 2026-06-22

- **Web 界面**：FastAPI + SSE 流式，实时显示工具调用/结果/Orient。会话管理、访问码保护。
- **使用次数限制**：每人每天默认 20 次，`usage.json` 持久化，每天 0 点自动重置。
- **自动启动**：macOS launchd 托管，开机自启 + 崩溃自动重启。
- **web_search 三级降级**：DDG Lite POST → Bing（国际/国内）→ Wikipedia。反爬检测 + 中英文自动识别。
- **fetch_url 工具**：抓取任意 URL，去标签提取文本，搜索后可深入阅读网页内容。
- **Agent 事件回调**：`on_event` 支持流式，Web 层队列 + 后台线程推送。
- **get_weather 工具**：Open-Meteo 实时天气，免费无需 Key。
- **ReAct FC 驱动**：Native FC 调用工具，Thought 在 content 中显式可见。
- **Anthropic 消息格式转换**：`_convert_messages_for_anthropic()` 多后端无缝切换。
- **Orient 模块**：显式 O-O-D-A 循环，工具结果自动解读注入上下文。
- 测试覆盖：76 tests OK

