# 深度审查报告：可视化/画图相关代码

审查范围：visual_router.py, agent.py (visual route), chart/*, diagram.py, circuit.py, ai_image.py, default.py, test_visual_router.py

---

## 🔴 Bug（会导致崩溃或错误结果）

### B1. `draw_function` 颜色值缺少 `#` 前缀
- **文件**: `nano_agent/tools/chart/advanced.py` 第 112 行
- **问题**: `colors = ["#7c3aed", "#3b82f6", "#10b981", "f59e0b", "#ef4444"]` — `"f59e0b"` 缺少 `#` 前缀。matplotlib 会将其解析为颜色名称而非十六进制色值，导致 `ValueError: Invalid color`（第 4 条曲线时触发）。
- **修复**:
```python
# Line 112
colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444"]
```

### B2. `_safe_eval_2d` 隐式乘法正则破坏 `pi` 常量
- **文件**: `nano_agent/tools/chart/advanced.py` 第 601 行
- **问题**: 正则 `r"(?<![a-zA-Z])([a-zA-Z])([a-zA-Z])(?![a-zA-Z])"` 将两个连续字母视为隐式乘法（`XY` → `X*Y`），但 `pi` 正好是两个字母，会被错误拆分为 `p*i`，而 `p` 和 `i` 不在 eval 命名空间中，导致 `NameError`。任何包含 `pi` 的等高线表达式（如 `sin(pi*X) + Y**2`）都会失败。
- **修复**: 在正则中排除已知常量和函数名的首字母：
```python
# 排除已知 2 字母标识符（pi, e 为常量；in, ln 等不会出现）
# 更安全的做法：在隐式乘法前先保护已知名称
_PROTECTED_NAMES = re.compile(r'\b(pi|e|X|Y|x|y)\b')
# 或直接修改正则，用更严格的 lookbehind/lookahead 排除关键字
# 最简修复：在正则后添加修补步骤
expr = re.sub(r"(?<![a-zA-Z])([a-zA-Z])([a-zA-Z])(?![a-zA-Z])", r"\1*\2", expr)
# 保护 pi 不被拆分
expr = expr.replace("p*i", "pi")
```

### B3. `_safe_eval_2d` 缺少 `np.` 前缀剥离（与 `draw_function` 不一致）
- **文件**: `nano_agent/tools/chart/advanced.py` 第 579-608 行
- **问题**: `draw_function`（1D）会执行 `expr = expr.replace(f"np.{fn}", fn)` 来兼容 LLM 常见的 `np.sin(x)` 写法。但 `_safe_eval_2d`（2D 等高线）缺少此步骤。当用户写 `np.sin(X)+np.cos(Y)` 时：
  1. `np` 被隐式乘法正则拆成 `n*p`
  2. `n`, `p` 不在命名空间中 → `NameError`
- **修复**: 在 `_safe_eval_2d` 中添加与 `draw_function` 相同的 `np.` 剥离逻辑：
```python
# 在 expr.replace("^", "**") 之前添加：
for fn in ("sin", "cos", "tan", "exp", "log", "sqrt", "abs"):
    expr = expr.replace(f"np.{fn}", fn)
expr = expr.replace("np.pi", "pi").replace("np.e", "e")
```

### B4. Mermaid `_smart_mermaid_config` 双重 `flowchart` 声明
- **文件**: `nano_agent/tools/diagram.py` 第 193-201 行
- **问题**: 当用户的 mermaid 代码以 `flowchart` 或 `graph` 开头但未声明方向时（如 `flowchart\n  A --> B`），`has_direction` 为 False，代码会在前面追加 `flowchart TB\n`，产生双重声明：
  ```
  flowchart LR
  flowchart
    A --> B
  ```
  这是无效的 Mermaid 语法，mermaid.ink 会返回渲染错误。
- **修复**: 检测已有的 `flowchart`/`graph` 关键字，替换而非追加：
```python
if chart_type in ("flowchart", "graph") and not has_direction:
    # 移除已有的 flowchart/graph 行，替换为带方向的声明
    code_lines = code_stripped.split("\n")
    if code_lines[0].strip().lower().startswith(("flowchart", "graph")):
        code_lines[0] = f"flowchart {direction}"
        code_stripped = "\n".join(code_lines)
    else:
        code_stripped = f"flowchart {direction}\n" + code_stripped
```

### B5. `draw_pie` 标签与值数量不匹配时崩溃
- **文件**: `nano_agent/tools/chart/basic.py` 第 124-133 行
- **问题**: `vals = [float(x) for x in data_sets[0]]` 和 `labels = label_sets[0]`。如果 vals 有 5 个值但 labels 只有 3 个，`ax.pie(vals, labels=labels, ...)` 会抛出 `ValueError`。数据解析中没有长度校验。
- **修复**:
```python
vals = [float(x) for x in data_sets[0]]
labels = label_sets[0] if label_sets and label_sets[0] else None
if labels and len(labels) < len(vals):
    labels = labels + [None] * (len(vals) - len(labels))  # 补 None
elif labels and len(labels) > len(vals):
    labels = labels[:len(vals)]  # 截断
```

### B6. `draw_radar` 多系列长度不一致时崩溃
- **文件**: `nano_agent/tools/chart/advanced.py` 第 65-79 行
- **问题**: 第一组数据定义维度数 `n`，`angles` 基于 `n` 生成。如果后续系列 `ds` 的长度 ≠ `n`，则 `v = [float(x) for x in ds] + [float(ds[0])]` 与 `angles` 长度不匹配，`ax.fill()` / `ax.plot()` 会报错。
- **修复**:
```python
for i, ds in enumerate(data_sets):
    if len(ds) != n:
        logger.warning(f"Radar series {i} has {len(ds)} values, expected {n}, skipping")
        continue
    v = [float(x) for x in ds] + [float(ds[0])]
    # ... rest of plotting
```

### B7. `draw_bubble` 三组数据长度不匹配时崩溃
- **文件**: `nano_agent/tools/chart/advanced.py` 第 82-101 行
- **问题**: `xs`、`ys`、`sizes` 来自三组独立数据，如果长度不一致，`ax.scatter(xs, ys, s=sizes, ...)` 会因 numpy 广播失败而崩溃。
- **修复**:
```python
min_len = min(len(xs), len(ys), len(sizes))
xs, ys, sizes = xs[:min_len], ys[:min_len], sizes[:min_len]
if min_len == 0:
    ax.text(0.5, 0.5, "Need 3 data series of equal length: x;y;size",
            transform=ax.transAxes, ha="center", color=fg)
    return
```

### B8. `generate_chart` 中 `savefig` 异常导致 figure 泄漏
- **文件**: `nano_agent/tools/chart/__init__.py` 第 124-125 行
- **问题**: `fig.savefig(filepath, ...)` 在 `try/except` 块之外。如果 `savefig` 抛出异常（如磁盘满、权限错误），`plt.close(fig)` 不会执行，figure 对象泄漏。在大量请求下可能导致内存泄漏。
- **修复**:
```python
try:
    fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=bg)
finally:
    plt.close(fig)
```

---

## 🟡 设计缺陷（逻辑不完整、边界情况遗漏）

### D1. 波形图 `total_rows` 计算包含无效通道 → 空行
- **文件**: `nano_agent/tools/chart/advanced.py` 第 393 行
- **问题**: `total_rows = len(analog_channels) + len(digital_channels)` 计算所有通道，但模拟通道中 `len(ds) < 3` 的会被 `continue` 跳过（第 414 行），导致布局中出现空白行，视觉上多出空隙。
- **修复**: 只计算有效通道：
```python
valid_analog = [(i, ds, lbl) for i, ds, lbl in analog_channels if len(ds) >= 3]
total_rows = len(valid_analog) + len(digital_channels)
# 在循环中使用 valid_analog 代替 analog_channels
```

### D2. 波形图数字通道二值判断阈值错误
- **文件**: `nano_agent/tools/chart/advanced.py` 第 475 行
- **问题**: `if l_range <= 2:` 用于判断是否为二值信号。但 `l_range = max - min`，对于三值信号 `[0, 1, 2]`，`l_range = 2 ≤ 2`，被误判为二值信号。正确的阈值应该是 `l_range <= 1`（只有 0 和 1 时 range=1）。
- **修复**:
```python
if l_range <= 1:  # 真正的二值信号: max-min <= 1
```

### D3. 波形图低电平标注重复
- **文件**: `nano_agent/tools/chart/advanced.py` 第 486-490 行
- **问题**: 二值信号中，当 `y_norm < 0.3` 时在下方画一个标签，然后无论 `y_norm` 值如何，又在上方画一个标签（第 490 行的 `ax.text` 在 if 块外面）。低电平值会显示两个标注，造成视觉混乱。
- **修复**: 将上方标注移入 else 分支：
```python
if l_range <= 1:
    if y_norm < 0.3:
        ax.text(i + 0.5, y_val - 0.12, str(lv), ...)  # 下方
    else:
        ax.text(i + 0.5, y_val + 0.04, str(lv), ...)  # 上方
else:
    ax.text(i + 0.5, y_val + 0.04, str(lv), ...)  # 多值：上方
```

### D4. "timing diagram" 英文不参与时序消歧
- **文件**: `nano_agent/visual_router.py` 第 164-170 行
- **问题**: `时序图` 的硬件/软件消歧逻辑只在 CJK 关键词 `kw in ("时序图", "时序")` 时触发。英文请求 `"SPI timing diagram"` 虽然在 `_HW_TIMING_KW` 中有 `SPI`，但由于 `kw` 是 `"timing diagram"` 而非 `"时序图"`，消歧逻辑不会执行，会被错误路由到 mermaid `sequenceDiagram`。
- **修复**: 将 `"timing diagram"` 和 `"timing"` 也加入消歧条件：
```python
if kw in ("时序图", "时序", "timing diagram", "timing"):
    if _HW_TIMING_KW.search(task_lower):
        return "generate_chart", {"chart_type": "waveform"}
```

### D5. 热力图文本颜色逻辑在负值场景失效
- **文件**: `nano_agent/tools/chart/advanced.py` 第 55 行
- **问题**: `color="white" if abs(matrix[i,j]) > matrix.max()/2 else "black"` 以 `max()/2` 为阈值。当矩阵全是负值时（如 `[-10, -1]`），`max() = -1`，`max()/2 = -0.5`。`abs(-1) > -0.5` → True，`abs(-10) > -0.5` → True，所有文本都是白色，在深色背景上无对比度。
- **修复**: 使用极差作为阈值基准：
```python
threshold = (abs(matrix.max()) + abs(matrix.min())) / 2
color="white" if abs(matrix[i,j]) > threshold else "black"
```

### D6. `_cleanup` 只清理 `chart_*.png`，其他类型图片无限累积
- **文件**: `nano_agent/tools/chart/__init__.py` 第 146-153 行
- **问题**: `_cleanup` 用 `glob("chart_*.png")` 只清理图表文件。但 `mermaid_*.png`、`circuit_*.png`、`ai_*.png`、`plantuml_*.png` 永远不被清理，长时间运行会占满磁盘。
- **修复**: 在各工具类中添加类似的 cleanup 方法，或在共享的 charts_dir 层面实现统一清理：
```python
def _cleanup(self, max_files: int = 50):
    """清理所有工具生成的 PNG，保留最近 max_files 个。"""
    all_pngs = []
    for pattern in ("chart_*.png", "mermaid_*.png", "circuit_*.png", "ai_*.png", "plantuml_*.png"):
        all_pngs.extend(self.charts_dir.glob(pattern))
    all_pngs.sort(key=lambda f: f.stat().st_mtime)
    # ... 删除逻辑同现有
```

### D7. 热力图 colorbar 在 dark 主题下样式不一致
- **文件**: `nano_agent/tools/chart/advanced.py` 第 50 行
- **问题**: `plt.colorbar(im, ax=ax)` 创建的 colorbar 使用默认浅色样式。在 dark 主题下，colorbar 背景是白色、刻度文字是黑色，与整体深色风格不搭。
- **修复**:
```python
cbar = plt.colorbar(im, ax=ax)
cbar.ax.yaxis.set_tick_params(color="#ccc" if is_dark else "#333")
plt.setp(plt.getp(cbar.ax.axes, "yticklabels"), color="#ccc" if is_dark else "#333")
```

### D8. `_parse_trajectory` 对同时出现在 data_sets 和 label_sets 的轨迹重复计数
- **文件**: `nano_agent/tools/chart/advanced.py` 第 531-577 行
- **问题**: 方法分别遍历 `data_sets` 和 `label_sets` 解析轨迹点。在 `draw_contour` 的设计意图中（`data=表达式`, `labels=轨迹`），轨迹只在 `label_sets` 中，不会重复。但如果用户在 `data` 中同时提供了表达式和轨迹（如 `data='X**2+Y**2;0,5;1,3'`），且 `labels` 也提供了轨迹，就会重复计数。
- **修复**: 添加去重逻辑或只在一边解析：
```python
# 在 draw_contour 中，如果 is_expr=True 且 traj_data 有内容，
# 则不从 label_sets 解析轨迹
if is_expr and traj_data:
    traj_pts = AdvancedCharts._parse_trajectory(traj_data, [])
else:
    traj_pts = AdvancedCharts._parse_trajectory(traj_data, label_sets)
```

### D9. `draw_function` 中 `2x` 自动补全正则会破坏多字符函数名
- **文件**: `nano_agent/tools/chart/advanced.py` 第 134 行
- **问题**: `re.sub(r"(\d)([a-zA-Z])", r"\1*\2", expr)` 将数字后跟字母转为乘法。这在 `log10(x)` 等场景下虽然不破坏函数名（因为 `0` 后面是 `(`），但会错误修改如 `e2`（科学计数法变量）为 `e*2`。更关键的是，该正则在 `_safe_eval_2d` 中也会执行，对含系数的表达式（如 `2X**2 + 3Y**2`）可正常工作，但对 `1e5`（科学计数法）会错误拆分为 `1*e5`。
- **修复**: 排除科学计数法：
```python
# 不拆分 数字e/E 后接数字的情况（科学计数法）
expr = re.sub(r"(\d)([a-zA-Z])", r"\1*\2", expr)
# 修复被错误拆分的科学计数法
expr = re.sub(r"(\d)\*([eE])(\d)", r"\1\2\3", expr)
```

### D10. eval 安全沙箱未使用 AST 验证
- **文件**: `nano_agent/tools/chart/advanced.py` 第 22-36 行（`_FORBIDDEN`）, 第 149 行, 第 606 行
- **问题**: 当前安全策略基于字符串子串匹配（`_FORBIDDEN` 黑名单）。虽然对 LLM 生成的数学表达式基本够用，但存在绕过风险：
  - 黑名单无法覆盖所有危险属性（如未来 Python 版本新增的魔术方法）
  - 无长度限制（超长表达式可能导致 ReDoS 或内存爆炸）
  - `lambda` 关键字未被禁止（虽然难以利用，但理论上可构造嵌套 lambda）
- **修复建议**（非紧急）：使用 `ast` 模块做白名单验证：
```python
import ast
class MathExprValidator(ast.NodeVisitor):
    ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call,
                     ast.Name, ast.Constant, ast.Load, ast.Add, ast.Sub,
                     ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd)
    ALLOWED_NAMES = {"sin", "cos", "tan", "exp", "log", "sqrt", "abs", "pi", "e",
                      "X", "Y", "x", "y"}
    def visit_Name(self, node): assert node.id in self.ALLOWED_NAMES
    def visit_Call(self, node):
        assert isinstance(node.func, ast.Name)
        assert node.func.id in {"sin","cos","tan","exp","log","sqrt","abs"}
        self.generic_visit(node)
```

### D11. `elm.Element()` 作为电路图回退元件可能不存在
- **文件**: `nano_agent/tools/circuit.py` 第 131 行, 第 160 行
- **问题**: 代码使用 `elm.Element()` 作为未知元件的回退。但 `schemdraw.elements.Element` 是抽象基类，直接实例化可能失败或产生空白图形。
- **修复**: 使用 `elm.Line()` 或 `elm.Resistor().label(name)` 作为更安全的回退：
```python
# 替换 elm.Element() → elm.Line().label(f"${label_text}$")
fallback = elm.Line().right().at(cur.end if cur else (0, 0)).label(f"${pt}$")
```

---

## 🟢 优化建议（代码质量、用户体验）

### O1. `draw_function` 中重复设置 xlabel/ylabel
- **文件**: `nano_agent/tools/chart/advanced.py` 第 162 行和第 203-204 行
- **问题**: `draw_function` 内部两次设置 `ax.set_xlabel("x")` 和 `ax.set_ylabel("y")`，第二次覆盖第一次且参数简化（无 fontsize）。
- **修复**: 删除第 203-204 行的重复设置。

### O2. `draw_function` 中三角函数 π 刻度总是覆盖用户自定义 x_label
- **文件**: `nano_agent/tools/chart/advanced.py` 第 175-196 行
- **问题**: 当检测到三角函数时，代码用 `ax.set_xticks` / `ax.set_xticklabels` 设置 π 刻度。但如果用户通过 `generate_chart` 传入了自定义的 `x_label`，`_apply_style` 会覆盖 `draw_function` 设的 xlabel，但 xticks 仍保留 π 格式——即 xlabel 显示 "Time" 但刻度是 π/2、π 等，造成混淆。
- **修复**: 仅当用户未提供 x_label 时显示 π 刻度，或在函数检测到三角函数时忽略用户的 x_label。

### O3. `_FORBIDDEN` 中 `'code'` 过于宽泛
- **文件**: `nano_agent/tools/chart/advanced.py` 第 26 行
- **问题**: `'code'` 作为禁止子串会误拦截包含 `decode`、`encode`、`barcode` 等子串的表达式。虽然这些在数学表达式中极少出现，但属于误报风险。
- **修复**: 移除 `'code'` 或改为 `r'\bcode\b'`（但需改为正则匹配模式）。

### O4. 测试缺少边界场景覆盖
- **文件**: `tests/test_visual_router.py`
- **问题**: 现有测试只覆盖正常路由命中，缺少：
  - 空数据 / 错误类型数据的图表生成
  - eval 注入尝试（安全测试）
  - matplotlib figure 关闭验证
  - 大量数据的性能测试
  - 英文 "timing diagram" 的硬件消歧
  - radar/bubble/pie 的长度不匹配场景
- **修复**: 添加上述边界场景的测试用例。

### O5. `diagram.py` 中 `_render_plantuml` 的正则匹配脆弱
- **文件**: `nano_agent/tools/diagram.py` 第 61-105 行
- **问题**: `_render_plantuml` 用多个正则将 Mermaid stateDiagram 语法逐行转换为 PlantUML 语法。这种逐行正则替换方式对格式变化很敏感（如多余空格、注释、嵌套状态），容易产生无效 PlantUML。
- **修复**: 考虑直接接受 PlantUML 语法输入，或用更结构化的解析器。

### O6. `_smart_mermaid_config` 中 node_count 统计不准确
- **文件**: `nano_agent/tools/diagram.py` 第 197 行
- **问题**: `node_count = len(re.findall(r'[A-Za-z_][A-Za-z0-9_]*\s*[\[\(\{/\\]', code_stripped))` 用于估算节点数。但这个正则会匹配非节点的标识符（如 CSS 类名、样式定义等），导致 node_count 偏高，方向被错误设为 TB。
- **修复**: 仅在代码主体（非 style 段）中统计，或使用更精确的节点模式。

---

## 总结

| 严重程度 | 数量 | 关键项 |
|---------|------|-------|
| 🔴 Bug | 8 | 颜色缺#、pi 被拆分、np.未剥离、双重 flowchart、pie/radar/bubble 长度不匹配崩溃、fig 泄漏 |
| 🟡 设计缺陷 | 11 | 波形空行/二值阈值、timing diagram 消歧、热力图颜色、文件清理缺失、eval 无 AST |
| 🟢 优化 | 6 | 重复代码、测试覆盖、正则脆弱 |

**最高优先级修复**（影响最大、修复最简单）：
1. **B1** — `"f59e0b"` → `"#f59e0b"`（1 字符修改）
2. **B2** — `pi` 被拆分（1 行修补）
3. **B4** — 双重 flowchart 声明（~5 行修改）
4. **B8** — fig 泄漏（添加 try/finally）
5. **B5/B6/B7** — 添加长度校验（各 ~3 行）
