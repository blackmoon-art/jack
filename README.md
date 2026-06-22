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
                    │        Agent             │  策略路由
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
    │ 多后端  │  │ 9个工具 │  │ 窗口+持久 │
    └─────────┘  └─────────┘  └───────────┘
```

## 快速开始

```bash
cd nano_agent_plus
pip install openai python-dotenv

# 配置（Ollama 本地模型示例）
cat > .env << 'EOF'
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

## 推理策略

| 策略 | 命令 | 流程 | 适用场景 |
|------|------|------|---------|
| **Default** | `--strategy default` | LLM ⇄ Tools 循环直到完成 | 日常任务 |
| **Plan-Execute** | `--strategy plan` | 分解→逐步执行→评估→必要时重规划 | 多步骤复杂任务 |
| **Reflexion** | `--strategy reflexion` | 执行→自评→反思→重试→教训累积 | 需要质量保证的试错型任务 |
| **Tree-of-Thought** | `--strategy tot` | 生成N候选→打分→执行最优→失败回溯 | 有多种解法的不确定任务 |

### 策略细节

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
| `web_search` | DuckDuckGo 网页搜索 | SSL 验证保留 |
| `calculate` | 数学表达式求值 | `ast` 安全解析，无 `eval` |
| `plan` | 触发计划分解 | 纯 LLM 调用，无副作用 |

## 记忆系统

```
┌─ 窗口记忆 (会话级) ──────────────┐
│ 保留最近 N 轮对话                │
│ 自动淘汰旧消息                   │
│ 用于：上下文连贯性               │
└──────────────────────────────────┘
                +
┌─ 持久记忆 (文件级) ──────────────┐
│ 追加写入 agent_memory.md         │
│ 跨会话保留                       │
│ 加载最近 50 行                   │
│ 用于：长期知识累积               │
└──────────────────────────────────┘
```

## 配置项

```bash
# ── LLM 后端 ──
AGENT_PROVIDER=anthropic          # anthropic | openai | deepseek | openrouter
ANTHROPIC_API_KEY=sk-ant-xxx
OPENAI_API_KEY=sk-xxx             # 也用于 DeepSeek/OpenRouter/Ollama
OPENAI_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-chat

# ── Agent 行为 ──
AGENT_MAX_ITERATIONS=10           # 最大工具调用轮数
AGENT_MAX_TOKENS=8000             # LLM 输出 token 上限
AGENT_BASH_TIMEOUT=120            # bash 命令超时 (秒)
AGENT_WORK_DIR=/your/project      # 工作目录（文件操作的根）
AGENT_MEMORY_WINDOW=10            # 会话窗口保留轮数
AGENT_MEMORY_FILE=agent_memory.md # 持久记忆文件路径
AGENT_RULES_DIR=.agent/rules      # 自定义规则 .md 目录
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
# Ran 60 tests in 5.4s
# OK
```

测试分布：

| 文件 | 测试数 | 覆盖内容 |
|------|:---:|------|
| `tests/test_tools.py` | 27 | bash 安全/超时, 路径沙箱, 文件读写/编辑, glob, grep, calculate, web_search |
| `tests/test_memory.py` | 8 | 窗口记忆存取/淘汰, 持久记忆存取/截断 |
| `tests/test_agent.py` | 8 | Agent 循环, 未知工具回退, 最大迭代, 记忆集成, 规则加载 |
| `tests/test_strategies.py` | 17 | Plan-Execute(6), Reflexion(5), Tree-of-Thought(6) |

全部使用 Mock LLM，不依赖真实 API，可在 CI 运行。

## 项目结构

```
nano_agent_plus/
├── run.py                           # CLI 入口
├── requirements.txt                 # 依赖 (openai, python-dotenv)
├── .env.example                     # 配置模板
├── .gitignore                       # 忽略 .env + 运行时文件
├── README.md
├── nano_agent/
│   ├── __init__.py
│   ├── config.py                    # 环境变量配置
│   ├── llm.py                       # 多后端 LLM (懒加载, 3次重试)
│   ├── tools.py                     # 9个工具 (安全加固)
│   ├── memory.py                    # 双层记忆 (窗口+文件)
│   ├── agent.py                     # Agent 核心 + 策略路由
│   └── strategies/
│       ├── __init__.py
│       ├── plan_execute.py          # Plan-Execute (分解→执行→评估→重规划)
│       ├── reflexion.py             # Reflexion (自评→反思→重试→教训)
│       └── tree_of_thought.py       # Tree-of-Thought (多候选→打分→回溯)
└── tests/
    ├── __init__.py
    ├── test_tools.py                # 工具单元测试
    ├── test_memory.py               # 记忆单元测试
    ├── test_agent.py                # Agent 循环测试
    └── test_strategies.py           # 策略测试
```

## 核心代码量

| 模块 | 行数 | 职责 |
|------|:---:|------|
| `tools.py` | 250 | 9个工具 + PathSandbox + ToolRegistry |
| `agent.py` | 230 | Agent 类 + 核心循环 + 策略路由 |
| `llm.py` | 130 | LLM 抽象 (Anthropic/OpenAI) + 重试 |
| `memory.py` | 100 | 窗口 + 持久记忆 |
| `config.py` | 70 | 配置管理 |
| 3个策略文件 | 430 | Plan-Execute + Reflexion + ToT |
| **总计** | **~1200** | 全部自己掌控 |
