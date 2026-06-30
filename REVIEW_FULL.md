# 全架构 + 代码质量审查报告

**日期**: 2026-06-30  
**代码量**: ~9,453 行 Python（核心） + ~4,161 行（web/models） + ~569 行（web/server）  
**测试**: 1,649 行 / 143 tests，覆盖率 ~17.4%

---

## 总评分表

| 维度 | 评分 | 说明 |
|------|------|------|
| **架构设计** | **9.0/10** | 分层清晰，策略+工具注册表设计优秀，扩展成本低 |
| **代码质量** | **8.0/10** | 命名规范好，类型标注覆盖率高；超长函数多，部分重复 |
| **线程安全** | **8.5/10** | threading.local 隔离完整，双检查锁到位；锁顺序有风险 |
| **安全性** | **8.0/10** | eval 黑名单+沙箱，路径遍历防护，SSRF 防护；eval 仍非 AST 白名单 |
| **性能** | **8.5/10** | 视觉路由省 LLM 调用，prompt 缓存，快速路径优化好 |
| **测试质量** | **6.5/10** | 覆盖率 17.4%，核心路径有测试，但边界/集成测试缺失 |
| **可维护性** | **8.0/10** | README 详尽，docstring 覆盖好；部分魔法数字仍硬编码 |
| **综合** | **8.1/10** | — |

---

## 🔴 Bug / 问题（4）

### R1. `_agent_loop` 126 行 — 超长方法
- **文件**: `agent.py:288-413`
- **问题**: 单方法承担视觉路由+LLM调用+工具执行+并行分发+回调+迭代控制，认知负载极高
- **修复**: 拆分为 `_check_visual_route()`, `_process_llm_response()`, `_execute_tool_calls()` 三个方法
- **工作量**: M

### R2. `draw_circuit` 228 行 — God Function
- **文件**: `tools/circuit.py:38-265`
- **问题**: 解析+布局+绘制+标注全在一个函数，难维护难测试。无并联/分支电路支持
- **修复**: 拆分为 `_parse_circuit_spec()`, `_layout_components()`, `_draw_series()` 
- **工作量**: L

### R3. `draw_waveform` 175 行 + `draw_contour` 101 行 — 超长
- **文件**: `tools/chart/advanced.py:352-526`, `643-743`
- **问题**: 类似上述，单函数承担过多职责
- **修复**: 拆分子方法
- **工作量**: M

### R4. `_cleanup` 的 glob 扩展可能误删用户手动上传的图片
- **文件**: `tools/chart/__init__.py:188-200`
- **问题**: 上轮修复 D6 扩展了清理范围到 `mermaid_*.png` 等，但如果用户上传了 `ai_photo.png`，不会被误删（前缀是 `ai_*` 只删 `ai_image` 生成的）。但 `circuit_*.png` 可能和用户的文件重名
- **修复**: 给所有工具生成的文件加统一前缀 `gen_`（如 `gen_chart_xxx.png`），区分用户上传文件
- **工作量**: S

---

## 🟡 设计改进（8）

### Y1. eval 安全：黑名单 → AST 白名单
- **文件**: `tools/chart/advanced.py:16-36`
- **当前**: `_FORBIDDEN` 黑名单拦截危险关键词
- **改进**: 用 `ast.NodeVisitor` 白名单验证数学表达式，更安全
- **工作量**: M

### Y2. `_strategy_instance` 仍是共享实例属性
- **文件**: `agent.py:_run_strategy()`
- **问题**: 并发请求共用同一个 `_strategy_instance`，虽然策略运行时状态都在 context 里，但如果策略自身有实例状态（如 ReAct 的 `thought_trail`），会有竞态
- **修复**: 移入 `_local.strategy_instance` 或每次创建新实例
- **工作量**: S

### Y3. 跨文件重复：`__init__` 模式
- **文件**: `circuit.py:30`, `diagram.py:57`
- **问题**: `def __init__(self, work_dir, charts_dir="")` 重复
- **修复**: 提取 `BaseTool` 基类
- **工作量**: S

### Y4. `plan_execute.py run()` 143 行 — 拆分
- **问题**: 规划+执行+评估+重规划全在一个方法
- **修复**: 拆分 `_execute_plan()`, `_evaluate_step()`, `_replan()`
- **工作量**: M

### Y5. `reflexion.py run()` 121 行 + `tree_of_thought.py run()` 122 行
- **问题**: 策略 run() 方法普遍过长
- **修复**: 提取私有方法
- **工作量**: M（每个）

### Y6. 测试缺少集成测试
- **问题**: 无端到端测试（Agent → LLM mock → 工具执行 → 结果验证）
- **修复**: 添加 `tests/test_integration.py`，mock LLM 返回，验证完整 Agent.run() 流程
- **工作量**: M

### Y7. `web/models/MiniCPM-V` 占 ~4100 行但不在测试范围
- **问题**: 第三方模型代码占了近 1/3 总行数，拉低覆盖率
- **修复**: 从测试覆盖率统计中排除 `web/models/`，或标记为 vendored
- **工作量**: S

### Y8. `hash()` 用作缓存 key 不稳定
- **文件**: `agent.py:_system_prompt()`
- **问题**: Python `hash()` 在不同进程间不稳定（PYTHONHASHSEED），但在单进程内一致，所以实际影响很小
- **修复**: 改为 `hashlib.md5(rules.encode()).hexdigest()[:8]` 或直接用字符串比较
- **工作量**: S

---

## 🟢 优化建议（5）

### O1. 工具描述加 USE FOR / NOT FOR
- 当前工具描述偏短，LLM 可能误选工具
- 加 `"USE FOR: ..." / "NOT FOR: ..."` 前缀提高选择准确率

### O2. 策略层加 metrics 收集
- 当前策略执行无统一指标（LLM 调用次数、耗时、工具调用次数）
- 在 BaseStrategy 加 `_metrics` 字典，run() 结束时上报

### O3. Config 的 `rules_dir` 默认值 `.agent/rules` 与实际 `rules/` 不一致
- 文档/代码命名不统一

### O4. shell.py `bash()` 107 行 — 拆分命令构建+执行+结果处理
- 当前方法同时处理白名单检查+别名+执行+超时+输出截断

### O5. 加 `pyproject.toml` 或 `setup.cfg` 标准化项目配置
- 当前无标准 Python 打包配置

---

## ✅ 架构亮点

1. **策略注册表 + StrategyContext** — 零猴子补丁，显式契约，扩展只需加一个类
2. **视觉路由三层匹配** — 关键词→意图→LLM，命中率 ~85%，0 LLM 调用
3. **四层记忆架构** — Working/Persistent/Reflection/LongTerm，FTS5 + CJK bigram
4. **threading.local 全面隔离** — on_event/model_override/visual_routed/prompt_cache
5. **Config dataclass + with_overrides** — 不可变配置，per-session 隔离
6. **LLM 统一接口** — Anthropic/OpenAI 双实现，流式+非流式，reasoning_content 兼容
7. **Orient 观察系统** — O-O-D-A 中间层，结构化解读工具结果
8. **Meta 策略 pipeline_state** — 跨策略中间产物共享，避免重复探索
9. **并行工具执行** — ThreadPoolExecutor + 批量 Orient，1次 LLM vs N次
10. **prompt 缓存** — per-request 缓存 system prompt，避免每轮重复构建

---

## 改进优先级排序

| 优先级 | 项目 | 工作量 | 影响 |
|--------|------|--------|------|
| **P1** | R1: 拆分 `_agent_loop` | M | 可维护性↑↑ |
| **P1** | Y2: `_strategy_instance` 移入 `_local` | S | 线程安全↑ |
| **P2** | R2+R3: 拆分超长画图函数 | L | 可维护性↑ |
| **P2** | Y1: eval AST 白名单 | M | 安全性↑ |
| **P2** | Y6: 集成测试 | M | 质量↑↑ |
| **P3** | Y4+Y5: 策略 run() 拆分 | M×3 | 可维护性↑ |
| **P3** | O1: 工具描述优化 | S | LLM 选对率↑ |
| **P3** | O2: 策略 metrics | S | 可观测性↑ |
| **P4** | Y3: BaseTool 基类 | S | 代码重复↓ |
| **P4** | R4: 文件前缀统一 | S | 安全↑ |
| **P4** | Y7: 排除 vendored 代码 | S | 覆盖率统计准确 |
| **P4** | Y8: hash() → 稳定 key | S | 理论正确性 |
