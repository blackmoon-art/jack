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

## 架构

```
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
    │ 多后端  │  │ 14个工具 │  │ 窗口+持久 │
    └─────────┘  └─────────┘  └───────────┘
```

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
| `stock_info` | 股票实时行情 (Yahoo Finance API) | 免费，无需 API Key |
| `stock_history` | 股票历史日K数据 (A股 akshare / 美股 yfinance) | 免费，无需 API Key |
| `stock_chart` | 生成股票走势图/K线图 PNG (matplotlib) | 同股票同周期自动缓存 |

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
│ 语义检索，自动 FTS tokenize       │
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
python -m unittest discover tests -v

# 输出：
# Ran 76 tests in 9.6s
# OK
```

测试分布：

| 文件 | 测试数 | 覆盖内容 |
|------|:---:|------|
| `tests/test_tools.py` | 26 | bash 安全/超时, 路径沙箱, 文件读写/编辑, glob, grep, calculate, web_search |
| `tests/test_memory.py` | 8 | 窗口记忆存取/淘汰, 持久记忆存取/截断, 摘要 |
| `tests/test_agent.py` | 8 | Agent 循环, 未知工具回退, 最大迭代, 记忆集成, 规则加载 |
| `tests/test_orient.py` | 8 | Orient 解读, 规则加载/缓存/匹配 |
| `tests/test_strategies.py` | 26 | Plan-Execute(6), ReAct(8), Reflexion(6), Tree-of-Thought(6) |
| `tests/test_evaluation.py` | 29 | TaskCase, TaskResult, EvalReport, Benchmark, 策略对比 |
| `tests/test_memory.py` | 14 | 窗口/持久/反思/LongTermMemory |

全部使用 Mock LLM，不依赖真实 API，可在 CI 运行。

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

### 决策 6：Memory 三层门面设计

```
窗口记忆: 保留最近N轮对话(FIFO淘汰)，进程内，会话结束即丢 → 当前上下文连贯
持久记忆: 追加写入 agent_memory.md，跨会话保留，自动轮转(上限200行) → 长期知识累积
反思记忆: 追加写入 reflection_traces.md，跨会话保留，自动轮转 → Reflexion 教训复用
```

### 一次请求的完整流转

```
Agent.run(task, strategy)
├─ Memory.get_window_messages()    ← 加载会话历史
├─ _system_prompt()                ← 规则 + 持久记忆 + 上次Orient结论 (循环前构建一次)
│
├─ 策略路由
│   └─ _agent_loop(messages)       ← 核心 O-O-D-A 循环
│       │
│       ┌────────────────────────────────────────┐
│       │  Decide: LLM.chat(messages, tools)     │
│       │    → tool_calls: [...]                 │
│       │                                        │
│       │  Act:    tools.execute(name, args)     │
│       │    → raw_result                        │
│       │                                        │
│       │  Orient: orient_engine.orient(result)  │
│       │    → 结构化解读注入 messages             │
│       │                                        │
│       │  Loop:  回到 Decide                    │
│       └────────────────────────────────────────┘
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
│   ├── agent.py                     # Agent 核心 + 策略注册表路由 + 参数注入
│   ├── orient.py                    # 显式 Orient 阶段 + 规则加载
│   └── strategies/
│       ├── __init__.py              #   STRATEGY_REGISTRY 注册表
│       ├── base.py                  #   BaseStrategy 基类 (接口约束 + 事件回调 + JSON重试)
│       ├── react.py                 #   ReAct (FC驱动, Thought显式可见, 事件透传, 参数可配)
│       ├── plan_execute.py          #   Plan-Execute (分解→执行→评估→重规划)
│       ├── reflexion.py             #   Reflexion (自评→反思→重试→教训持久化)
│       └── tree_of_thought.py       #   Tree-of-Thought (多候选→打分→回溯, 参数可配)
└── tests/
    ├── __init__.py
    ├── test_tools.py                # 工具单元测试 (26)
    ├── test_memory.py               # 记忆单元测试 (8)
    ├── test_agent.py                # Agent 循环测试 (8)
    ├── test_orient.py               # Orient 测试 (8)
    └── test_strategies.py           # 策略测试 (26)
```

## 核心代码量

| 模块 | 行数 | 职责 |
|------|:---:|------|
| `tools/` 包 | 1,050 | 14个工具, 分 7 个子模块 + Observation 结构化返回 |
| `agent.py` | 265 | Agent 类 + O-O-D-A 循环 + 策略注册表路由 + 策略参数注入 |
| `llm.py` | 233 | LLM 抽象 (Anthropic/OpenAI) + 重试 + Anthropic消息转换 |
| `orient.py` | 170 | 显式 Orient: 解读→关联→规则→建议 |
| `memory.py` | 120 | 三层记忆门面 (Working + Persistent + Reflection) |
| `config.py` | 90 | 配置管理 (含策略参数 + Orient 阈值) |
| `logging_config.py` | 49 | 统一日志配置 |
| `strategies/base.py` | 55 | BaseStrategy 基类 (接口约束 + 事件回调 + JSON重试) |
| 4个策略文件 | 820 | ReAct + Plan-Execute + Reflexion + ToT |
| **总计** | **~2,852** | 全部自己掌控 |

## 架构待提升

当前架构评分 **8.5/10**。以下按优先级列出待改进项：

### P3: Agent 类职责偏重

`agent.py` ~260 行承担 5 个职责：组件装配、任务编排、事件分发、核心循环、Prompt 构建。改 prompt 逻辑要动 agent.py，改循环逻辑也要动 agent.py。

**方案**：拆出 `AgentLoop`（O-O-D-A 循环）和 `PromptBuilder`（system_prompt + messages 构建），agent.py 只保留编排。

### P3: LLM 重试不区分错误类型

`LLM.chat()` 对所有异常都重试 3 次，包括 400（参数错误）和 401（key 错误）这类不可重试错误，纯浪费时间。

**方案**：只重试 429/500/502/503/timeout，其余直接 raise。

### P3: Orient JSON 解析未复用 _chat_json

`Orient._parse_orientation()` 自己做 JSON 清理 + 解析，没用 `BaseStrategy._chat_json()`，也没有重试。一次垃圾 JSON 就丢失 Orient 价值。

**方案**：Orient 复用 `_chat_json` 或类似逻辑。

### P3: Web 线程安全

`web/server.py` 的 `sessions` dict 在多请求并发时可能 race——两个请求同时创建同一 session_id。`agent_stream` 里的 `item` 变量在线程异常时可能未定义。

**方案**：`sessions` 加锁，或用 `threading.Lock`。

### P3: default 策略未注册

`default` 是唯一不在 `STRATEGY_REGISTRY` 里的策略，直接调 `_agent_loop`，不走 `BaseStrategy`。导致架构对称性缺失。

**方案**：写一个 `DefaultStrategy(BaseStrategy)` 包装 `_agent_loop`，注册进 registry。

### P4: 工具注册未自动化

新增工具需要手写 OpenAI function schema 并手动 `_register()`。14 个工具已经写好，重复成本已付。但后续如果加很多工具，这个模式会越来越烦。

**方案**：装饰器或基类自动从类型注解生成 schema。

## 更新日志

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

