# Changelog

## 2026-07-05

### Debug
- **基本模拟电路生成调试中** — 模板匹配、SPICE 生成、Ngspice 仿真链路排查

### Fix
- 评分公式修复：交叉/重叠指数衰减常数从 300/200 修正为 1.0/0.5，评分准确反映布局质量
- 反馈边检测+专用 bus 路由：Sugiyama DFS 自动检测 `DFF↔NOT` 反馈环，走线经底部专用通道，消除 gate/wire 重叠（4-bit counter overlap: 4→0）
- 计数器模板改为 ripple counter 结构（4 DFF + 4 NOT），`initial q=0` 确保仿真可计数
- 动态 N-bit 计数器生成：支持 "8-bit counter"、"8位计数器" 等描述，自动生成 ripple counter Verilog + testbench
- Layout gate 从硬检查（cross==0 && ov==0）改为评分阈值（≥7 clean / ≥5 acceptable / <5 reject）
- n_bits 上限 16 位，防止仿真周期指数爆炸
- `max(candidates)` 空列表崩溃修复，`layout_quality` 初始分从 10.0 改为 0.0
- `route_mid` forbidden-channel 检查修复：无效 `continue` 改为列表推导过滤
- 电路工具超时缩减：yosys 60s→20s, iverilog 30s→10s, vvp 30s→10s

### Perf
- 布局渲染预筛选：内部快速评分排序后只渲染 top 5 组合（原 8-18 次）
- FTS5 长期记忆查询加缓存，Reflexion 重试不再重复查库
- Seed 参数化布局重试，每轮不同初始行序 + barycenter 扰动

## 2026-07-04

### Feat
- 数字电路闭环流水线：NL→Verilog→iverilog 编译→vvp 仿真→yosys 综合→Sugiyama+Barycenter+Orthogonal 布线 SVG
- 模拟电路放大器指标：GBW、相位裕度、CMRR、压摆率
- 模板库扩展 + 仿真驱动评估

### Fix
- 模拟工具懒加载，数字运行时不输出 SPICE 模板日志
- URL-encode 文件名 Content-Disposition header

### Perf
- 路由缓存 + schema 裁剪 + 模板统一

## 2026-07-03

### Feat
- 模拟电路流水线重构 + 质量门控 + 测试
- 电路工具链全面重构 + 智谱 GLM 支持 + 用户反馈系统
- 电气验证门 + SPICE title line 修复
- `design_circuit` 端到端闭环模拟电路设计

### Fix
- 运放极性反转 + Ngspice 子电路回退
- `draw_analog_spice` 仿真门：LLM 修正重试自动验证
- SPICE 解析修复
- 模拟电路模板中文关键词匹配
- 电路绘制意图过滤 + 放大器参数计算 + 仿真反馈环
