"""高级图表: heatmap, radar, bubble, function, regression, wireframe, waveform, contour。"""

import ast
import logging
import operator as _op
import re

import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger("nano_agent.tools.chart.advanced")


# ── 安全数学表达式求值器 (AST-based, 替代 eval) ──────────

def _safe_eval_math(expr_str: str, namespace: dict):
    """用 ast.parse 安全求值数学表达式。只允许已知的运算符、函数和变量。

    支持的语法:
      - 字面量: 数字 (int/float)
      - 变量:   Name 节点（必须在 namespace 中已存在）
      - 运算符: +, -, *, /, **, 一元 +/-
      - 函数调用: 只允许 namespace 中的可调用对象
      - 不允许: 属性访问, 下标, 比较, 布尔, 推导式等
    """

    _BINOPS = {
        ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
        ast.Div: _op.truediv, ast.Pow: _op.pow,
    }
    _UNOPS = {ast.USub: _op.neg, ast.UAdd: _op.pos}

    def _eval(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in namespace:
                return namespace[node.id]
            raise NameError(f"name '{node.id}' is not defined")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op_cls = type(node.op)
            if op_cls not in _BINOPS:
                raise ValueError(f"Unsupported operator: {op_cls.__name__}")
            return _BINOPS[op_cls](left, right)
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            op_cls = type(node.op)
            if op_cls not in _UNOPS:
                raise ValueError(f"Unsupported unary: {op_cls.__name__}")
            return _UNOPS[op_cls](operand)
        if isinstance(node, ast.Call):
            func = _eval(node.func)
            if not callable(func):
                raise ValueError(f"'{type(func).__name__}' is not callable")
            args = [_eval(a) for a in node.args]
            if node.keywords:
                raise ValueError("Keyword arguments not allowed")
            return func(*args)
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    try:
        tree = ast.parse(expr_str.strip(), mode="eval")
        return _eval(tree)
    except Exception:
        raise  # re-raise for caller to handle


class AdvancedCharts:
    """高级图表绘制 — 热力图、雷达、气泡、函数、回归、3D、波形。"""

    @staticmethod
    def draw_heatmap(ax, data_sets, label_sets, is_dark=True):
        """热力图 — 矩阵数据，行用分号，列用逗号。"""
        matrix = np.array([[float(x) for x in ds] for ds in data_sets])
        im = ax.imshow(matrix, cmap="coolwarm", aspect="auto")
        cbar = plt.colorbar(im, ax=ax)
        cbar.ax.yaxis.set_tick_params(color="#ccc" if is_dark else "#333")
        for i in range(len(data_sets)):
            for j in range(len(data_sets[0])):
                ax.text(j, i, f"{matrix[i,j]:.1f}", ha="center", va="center",
                        color="white" if abs(matrix[i,j]) > (abs(matrix.max()) + abs(matrix.min())) / 2 else "black",
                        fontsize=10)
        if label_sets and label_sets[0]:
            ax.set_xticks(range(len(label_sets[0])))
            ax.set_xticklabels(label_sets[0], color="#ccc" if is_dark else "#333")
        if label_sets and len(label_sets) > 1:
            ax.set_yticks(range(len(label_sets[1])))
            ax.set_yticklabels(label_sets[1], color="#ccc" if is_dark else "#333")

    @staticmethod
    def draw_radar(ax, data_sets, label_sets, is_dark=True):
        """雷达图 — 第一组为数值，label_sets[0] 为维度名。"""
        vals = [float(x) for x in data_sets[0]]
        n = len(vals)
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        angles += angles[:1]
        colors = ["#7c3aed", "#3b82f6", "#10b981"]
        for i, ds in enumerate(data_sets):
            if len(ds) != n:
                logger.warning(f"Radar series {i} has {len(ds)} values, expected {n}, skipping")
                continue
            v = [float(x) for x in ds] + [float(ds[0])]
            ax.fill(angles, v, alpha=0.25, color=colors[i % len(colors)])
            ax.plot(angles, v, color=colors[i % len(colors)], linewidth=2)
        if label_sets and label_sets[0]:
            cats = label_sets[0] + [label_sets[0][0]]
            ax.set_xticks(angles)
            ax.set_xticklabels(cats, color="#ccc" if is_dark else "#333", fontsize=10)

    @staticmethod
    def draw_bubble(ax, data_sets, label_sets, is_dark=True):
        """气泡图 — 三组数据: x;y;size。"""
        if len(data_sets) < 3:
            ax.scatter([float(x) for x in data_sets[0]],
                       [float(x) for x in data_sets[1]] if len(data_sets) > 1 else [0],
                       s=60, alpha=0.8, color="#7c3aed")
            return
        xs = np.array([float(x) for x in data_sets[0]])
        ys = np.array([float(x) for x in data_sets[1]])
        sizes = np.array([float(x) for x in data_sets[2]]) * 50
        min_len = min(len(xs), len(ys), len(sizes))
        xs, ys, sizes = xs[:min_len], ys[:min_len], sizes[:min_len]
        sc = ax.scatter(xs, ys, s=sizes, alpha=0.6, c=sizes, cmap="coolwarm",
                        edgecolors="#fff", linewidth=0.5)
        if label_sets and label_sets[0]:
            for i, lbl in enumerate(label_sets[0]):
                if i < len(xs):
                    ax.annotate(lbl, (xs[i], ys[i]), textcoords="offset points",
                                xytext=(0, 8), ha="center", fontsize=9,
                                color="#ccc" if is_dark else "#333")
        plt.colorbar(sc, ax=ax)

    @staticmethod
    def draw_function(ax, data_sets, label_sets, is_dark=True):
        """数学函数绘图 — 支持多函数叠加。

        data 格式 (经 _parse_multi 拆分后):
          [['x**2']]               → 单函数
          [['sin(x)'],['cos(x)']]  → 双函数叠加
          [['sin(x)'],['-3','3']]  → 函数 + 范围
        """
        fg = "#e0e0e0" if is_dark else "#333"
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444"]

        if not data_sets or not data_sets[0]:
            # Demo 模式
            data_sets = [["sin(x)"], ["x**2 / 10"]]
            ax.text(0.3, 0.95, "Demo: y = sin(x), y = x²/10", transform=ax.transAxes,
                    color="#10b981", fontsize=10, alpha=0.8)
            return

        # 收集所有表达式（非数值的 data_set 视为表达式）
        exprs = []
        x_range = (-5, 5)
        for ds in data_sets:
            if not ds:
                continue
            item = ds[0]
            # 尝试判断是表达式还是数值范围
            try:
                float(item)
                # 纯数字 = 范围参数，不是表达式
                if len(ds) >= 2:
                    try:
                        x_range = (float(ds[0]), float(ds[1]))
                    except ValueError:
                        pass
                continue
            except ValueError:
                pass
            # 去掉可能的前缀
            expr = item.strip().strip("`'\"")
            # LLM 写法兼容: x^2 → x**2, 2x → 2*x
            expr = expr.replace("^", "**")
            expr = re.sub(r"(\d)([a-zA-Z])", r"\1*\2", expr)
            expr = re.sub(r"(\d)\*([eE])(\d)", r"\1\2\3", expr)  # 修复科学计数法
            # 自动补全 np. 前缀 → 裸函数名
            for fn in ("sin", "cos", "tan", "exp", "log", "sqrt", "abs"):
                expr = expr.replace(f"np.{fn}", fn)
            exprs.append(expr)
            # 如果后面跟着数值，那是范围
            if len(ds) >= 3:
                try:
                    x_range = (float(ds[1]), float(ds[2]))
                except ValueError:
                    pass

        if not exprs:
            exprs = ["x"]

        x = np.linspace(x_range[0], x_range[1], 500)

        # 安全沙箱：只暴露纯数值函数和变量
        ns = {"x": x, "sin": np.sin, "cos": np.cos, "tan": np.tan,
              "exp": np.exp, "log": np.log, "sqrt": np.sqrt, "abs": np.abs,
              "pi": np.pi, "e": np.e}

        has_error = False
        for i, expr in enumerate(exprs):
            try:
                y = _safe_eval_math(expr, ns)
            except Exception as e:
                logger.warning(f"Function eval failed for '{expr}': {e}")
                if not has_error:
                    ax.text(0.5, 0.5, f"Error: cannot evaluate '{expr}'\n{e}",
                            transform=ax.transAxes, ha="center", color=fg, fontsize=10)
                    has_error = True
                continue

            color = colors[i % len(colors)]
            label = f"y = {expr}" if len(exprs) > 1 else None
            ax.plot(x, y, color=color, linewidth=2, label=label)

        ax.axhline(y=0, color=fg, linewidth=0.5, alpha=0.5)
        ax.axvline(x=0, color=fg, linewidth=0.5, alpha=0.5)

        # 多函数时显示图例
        if len(exprs) > 1:
            ax.legend(facecolor="#222" if is_dark else "#f0f0f0",
                      edgecolor="#444" if is_dark else "#ccc", labelcolor=fg)

        # 默认坐标轴标签（_apply_style 中的用户 x_label/y_label 会覆盖）
        ax.set_xlabel("x", color=fg, fontsize=12)
        ax.set_ylabel("y", color=fg, fontsize=12)

        # 三角函数检测：用所有表达式的合并文本判断
        all_expr = " ".join(exprs).lower()
        is_trig = any(fn in all_expr for fn in ('sin', 'cos', 'tan'))
        if is_trig:
            pi = np.pi
            x_min, x_max = x_range
            ticks, labels = [], []
            n_start = int(np.floor(x_min / (pi / 2)))
            n_end = int(np.ceil(x_max / (pi / 2)))
            for n in range(n_start, n_end + 1):
                val = n * pi / 2
                if x_min <= val <= x_max:
                    ticks.append(val)
                    if n == 0:
                        labels.append("0")
                    elif n == 1:
                        labels.append("π/2")
                    elif n == -1:
                        labels.append("-π/2")
                    elif n % 2 == 0:
                        half = n // 2
                        labels.append("π" if half == 1 else ("-π" if half == -1 else f"{half}π"))
                    else:
                        labels.append(f"{n}π/2")
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels, color=fg, fontsize=9)

        title_expr = exprs[0] if len(exprs) == 1 else f"{len(exprs)} functions"
        ax.set_title(f"y = {title_expr}", color=fg, fontsize=14, fontweight="bold")
        ax.set_xlabel("x", color=fg)
        ax.set_ylabel("y", color=fg)

    @staticmethod
    def draw_regression(ax, data_sets, label_sets, is_dark=True):
        """最小二乘回归 — 散点 + 拟合直线 + 方程 + R²。"""
        fg = "#e0e0e0" if is_dark else "#333"
        groups = [ds for ds in data_sets if ds]
        if not groups:
            # Demo 模式：生成带噪声的线性数据
            import numpy as np
            np.random.seed(42)
            x_demo = np.linspace(0, 10, 20)
            y_demo = 2.5 * x_demo + 1.0 + np.random.normal(0, 2, 20)
            data_sets = [[float(x), float(y)] for x, y in zip(x_demo, y_demo)]
            groups = data_sets
            ax.text(0.3, 0.95, "Demo: y = 2.5x + 1 + noise", transform=ax.transAxes,
                    color="#10b981", fontsize=10, alpha=0.8)

        pts = []
        all_pairs = all(len(ds) == 2 for ds in groups)
        two_series = len(groups) == 2 and not all_pairs

        if all_pairs:
            for ds in groups:
                try:
                    pts.append((float(ds[0]), float(ds[1])))
                except ValueError:
                    continue
        elif two_series and len(groups[0]) == len(groups[1]):
            for x_val, y_val in zip(groups[0], groups[1]):
                try:
                    pts.append((float(x_val), float(y_val)))
                except ValueError:
                    continue
        else:
            all_vals = []
            for ds in groups:
                for item in ds:
                    try:
                        all_vals.append(float(item))
                    except ValueError:
                        continue
            if all_vals:
                pts = [(i, y) for i, y in enumerate(all_vals)]

        if len(pts) < 2:
            ax.text(0.5, 0.5, "Need ≥2 data points for regression", transform=ax.transAxes,
                    ha="center", color=fg)
            return

        xs = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])
        n = len(xs)
        denom = n * (xs * xs).sum() - xs.sum() ** 2
        if denom == 0:
            ax.text(0.5, 0.5, "Cannot fit regression: all x values identical",
                    transform=ax.transAxes, ha="center", color=fg)
            return
        b = (n * (xs * ys).sum() - xs.sum() * ys.sum()) / denom
        a = (ys.sum() - b * xs.sum()) / n

        y_pred = a + b * xs
        ss_res = ((ys - y_pred) ** 2).sum()
        ss_tot = ((ys - ys.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        ax.scatter(xs, ys, color="#7c3aed", s=60, zorder=5, alpha=0.8,
                   edgecolors="white", linewidth=0.5)
        x_line = np.linspace(xs.min(), xs.max(), 200)
        ax.plot(x_line, a + b * x_line, color="#f59e0b", linewidth=2, zorder=4)

        sign = "+" if b >= 0 else "-"
        eq = f"y = {a:.2f} {sign} {abs(b):.2f}x"
        ax.text(0.05, 0.95, f"{eq}\nR² = {r2:.4f}", transform=ax.transAxes,
                fontsize=12, color=fg, verticalalignment="top",
                bbox=dict(boxstyle="round", facecolor="#1a1a2e" if is_dark else "#f5f5f5",
                          edgecolor=fg, alpha=0.8))
        ax.set_xlabel("x", color=fg)
        ax.set_ylabel("y", color=fg)

    # ── 预定义 3D 形状 ────────────────────────────────────

    _SHAPE_NAMES: set[str] = {
        "cube", "sphere", "torus", "cone", "cylinder",
        "paraboloid", "saddle", "ripple", "helix", "mobius",
        "heart", "hyperboloid",
    }

    @classmethod
    def _generate_shape_mesh(cls, shape: str, resolution: int = 80):
        """生成预定义 3D 形状的网格数据 (X, Y, Z)。"""
        res = resolution
        if shape == "sphere":
            theta = np.linspace(0, np.pi, res)
            phi = np.linspace(0, 2 * np.pi, res)
            T, P = np.meshgrid(theta, phi)
            r = 1
            X = r * np.sin(T) * np.cos(P)
            Y = r * np.sin(T) * np.sin(P)
            Z = r * np.cos(T)
        elif shape == "torus":
            theta = np.linspace(0, 2 * np.pi, res)
            phi = np.linspace(0, 2 * np.pi, res)
            T, P = np.meshgrid(theta, phi)
            R, r = 2.0, 0.6
            X = (R + r * np.cos(P)) * np.cos(T)
            Y = (R + r * np.cos(P)) * np.sin(T)
            Z = r * np.sin(P)
        elif shape == "cone":
            h = np.linspace(0, 2, res)
            theta = np.linspace(0, 2 * np.pi, res)
            H, T = np.meshgrid(h, theta)
            r = 1 - H / 2
            X = r * np.cos(T)
            Y = r * np.sin(T)
            Z = H
        elif shape == "cylinder":
            h = np.linspace(0, 2, res)
            theta = np.linspace(0, 2 * np.pi, res)
            H, T = np.meshgrid(h, theta)
            X = np.cos(T)
            Y = np.sin(T)
            Z = H
        elif shape == "helix":
            t = np.linspace(0, 4 * np.pi, res * 2)
            r = np.linspace(0.2, 0.2, res)
            T, R = np.meshgrid(t, r)
            X = R * np.cos(T)
            Y = R * np.sin(T)
            Z = T / (2 * np.pi)
        elif shape == "hyperboloid":
            u = np.linspace(-1.5, 1.5, res)
            v = np.linspace(0, 2 * np.pi, res)
            U, V = np.meshgrid(u, v)
            X = np.sqrt(1 + U**2) * np.cos(V)
            Y = np.sqrt(1 + U**2) * np.sin(V)
            Z = U
        elif shape == "heart":
            u = np.linspace(0, 2 * np.pi, res)
            v = np.linspace(-1, 1, res)
            U, V = np.meshgrid(u, v)
            X = 16 * np.sin(U)**3
            Y = 13 * np.cos(U) - 5 * np.cos(2*U) - 2 * np.cos(3*U) - np.cos(4*U)
            Z = V * 5
            X, Y, Z = X / 10, Y / 10, Z / 5
        else:
            # cube / paraboloid / saddle / ripple / mobius
            x = np.linspace(-2, 2, res)
            y = np.linspace(-2, 2, res)
            X, Y = np.meshgrid(x, y)
            if shape == "paraboloid":
                Z = X**2 + Y**2
            elif shape == "saddle":
                Z = X**2 - Y**2
            elif shape == "ripple":
                R = np.sqrt(X**2 + Y**2)
                Z = np.sin(R * 3) / (R + 0.5)
            elif shape == "mobius":
                u = np.linspace(0, 2 * np.pi, res)
                v = np.linspace(-0.5, 0.5, res)
                U, V = np.meshgrid(u, v)
                X = (1 + V * np.cos(U/2)) * np.cos(U)
                Y = (1 + V * np.cos(U/2)) * np.sin(U)
                Z = V * np.sin(U/2)
            elif shape == "cube":
                # cube as surface formula: (x^10 + y^10 + z^10)^(1/10) = 1
                # approximate with smooth cube
                X, Y = np.meshgrid(x, y)
                Z = np.ones_like(X)  # top face
                X, Y, Z = X, Y, Z  # placeholder — cube is special
                # Actually, cube is better as wireframe; for surface, use rounded cube
                p = 10
                Z = (1 - np.abs(X)**p - np.abs(Y)**p) ** (1/p)
                Z = np.nan_to_num(Z, nan=0)
                X, Y = X, Y
                # top half only
                mask = np.abs(X) <= 1
                mask &= np.abs(Y) <= 1
                Z[~mask] = np.nan
        return X, Y, Z

    @classmethod
    def _parse_3d_ranges(cls, label_sets, default=(-5.0, 5.0)):
        """从 label_sets 解析 X/Y 范围。labels='x_min,x_max;y_min,y_max'"""
        x_range, y_range = list(default), list(default)
        if label_sets:
            for i, group in enumerate(label_sets):
                nums = []
                for item in group:
                    try:
                        nums.append(float(item))
                    except (ValueError, TypeError):
                        pass
                if len(nums) >= 2 and i == 0:
                    x_range = [nums[0], nums[1]]
                if len(nums) >= 2 and i == 1:
                    y_range = [nums[0], nums[1]]
        return x_range, y_range

    @staticmethod
    def draw_wireframe(fig, data, label_sets, is_dark, title):
        """3D 线框模型 — 预定义形状 / 数学公式 / 边列表。

        data 格式:
          - 形状名: "sphere", "torus", "cone", "saddle", "paraboloid" 等
          - 公式:   "sin(sqrt(X**2+Y**2))" → 用 plot_wireframe 渲染
          - 边列表: "x1,y1,z1;x2,y2,z2;..." (向后兼容)
        """
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

        fg = "#e0e0e0" if is_dark else "#333"
        bg = "#1a1a2e" if is_dark else "#fafafa"

        ax = fig.add_subplot(111, projection='3d')
        ax.set_facecolor(bg)
        fig.patch.set_facecolor(bg)

        raw = (data or "").strip().lower()

        # ── 模式 1: 预定义形状 ──
        if raw in AdvancedCharts._SHAPE_NAMES:
            X, Y, Z = AdvancedCharts._generate_shape_mesh(raw)
            try:
                ax.plot_wireframe(X, Y, Z, color="#7c3aed", linewidth=0.5, alpha=0.8)
            except Exception:
                # fallback: draw as scatter wire
                for i in range(0, X.shape[0], 4):
                    ax.plot(X[i, :], Y[i, :], Z[i, :], color="#7c3aed",
                            linewidth=0.5, alpha=0.7)
                for j in range(0, X.shape[1], 4):
                    ax.plot(X[:, j], Y[:, j], Z[:, j], color="#7c3aed",
                            linewidth=0.5, alpha=0.7)
            _title = f"{raw.title()} Wireframe"
            ax.set_title(_title, color=fg, fontsize=13, fontweight="bold")

        # ── 模式 2: 数学公式 → plot_wireframe ──
        elif raw and any(op in raw for op in ("**", "*", "+", "-", "/", "sin", "cos", "exp", "sqrt", "X", "Y")):
            x_range, y_range = AdvancedCharts._parse_3d_ranges(label_sets)
            X, Y = np.meshgrid(
                np.linspace(x_range[0], x_range[1], 80),
                np.linspace(y_range[0], y_range[1], 80),
            )
            Z = AdvancedCharts._safe_eval_2d(raw, X, Y)
            if Z is not None:
                ax.plot_wireframe(X, Y, Z, color="#7c3aed", linewidth=0.5, alpha=0.8)
                clean = raw.replace("**", "^").replace("*", "")
                ax.set_title(f"Z = {clean}", color=fg, fontsize=13, fontweight="bold")
            else:
                ax.text2D(0.5, 0.5, f"Error evaluating:\n{raw}", transform=ax.transAxes,
                          ha="center", color=fg, fontsize=11)

        # ── 模式 3: 边列表 (向后兼容) ──
        else:
            edges_data = data if data else ""
            if not edges_data.strip():
                edges_data = (
                    "0,0,0;1,0,0; 1,0,0;1,1,0; 1,1,0;0,1,0; 0,1,0;0,0,0;"
                    "0,0,1;1,0,1; 1,0,1;1,1,1; 1,1,1;0,1,1; 0,1,1;0,0,1;"
                    "0,0,0;0,0,1; 1,0,0;1,0,1; 1,1,0;1,1,1; 0,1,0;0,1,1"
                )
            edges = [e.strip() for e in edges_data.replace("\n", ";").split(";") if e.strip()]
            for edge in edges:
                parts = edge.split(",")
                if len(parts) >= 6:
                    try:
                        x1, y1, z1 = float(parts[0]), float(parts[1]), float(parts[2])
                        x2, y2, z2 = float(parts[3]), float(parts[4]), float(parts[5])
                        ax.plot([x1, x2], [y1, y2], [z1, z2],
                                color="#7c3aed", linewidth=2, alpha=0.9)
                    except ValueError:
                        pass

        try:
            ax.set_box_aspect([1, 1, 1])
        except Exception:
            pass

        ax.tick_params(colors=fg, labelsize=9)
        ax.set_xlabel("X" if not label_sets else label_sets[0][0] if label_sets[0] else "X",
                      color=fg, fontsize=10)
        ax.set_ylabel("Y", color=fg, fontsize=10)
        ax.set_zlabel("Z", color=fg, fontsize=10)

        try:
            ax.set_proj_type('ortho')
        except Exception:
            pass

    # ── 3D 曲面渲染 ─────────────────────────────────────

    @classmethod
    def draw_surface(cls, fig, data, label_sets, is_dark, title):
        """3D 曲面渲染 — plot_surface，带光照、颜色映射、colorbar。

        data 格式:
          - 形状名: "sphere", "torus", "saddle", "heart", "mobius" 等
          - 数学公式: "sin(sqrt(X**2+Y**2))" → meshgrid + plot_surface

        labels 可指定范围: "x_min,x_max;y_min,y_max" (默认 -5,5)
        """
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

        fg = "#e0e0e0" if is_dark else "#333"
        bg = "#1a1a2e" if is_dark else "#fafafa"

        ax = fig.add_subplot(111, projection='3d')
        ax.set_facecolor(bg)
        fig.patch.set_facecolor(bg)

        raw = (data or "").strip()

        # ── 模式 1: 预定义形状 ──
        if raw.lower() in cls._SHAPE_NAMES:
            X, Y, Z = cls._generate_shape_mesh(raw.lower())
            surf_title = f"{raw.title()} Surface"
        # ── 模式 2: 数学公式 ──
        elif raw:
            x_range, y_range = cls._parse_3d_ranges(label_sets)
            X, Y = np.meshgrid(
                np.linspace(x_range[0], x_range[1], 100),
                np.linspace(y_range[0], y_range[1], 100),
            )
            Z = cls._safe_eval_2d(raw, X, Y)
            if Z is None:
                ax.text2D(0.5, 0.5, f"Error evaluating:\n{raw}", transform=ax.transAxes,
                          ha="center", color=fg, fontsize=11)
                ax.tick_params(colors=fg, labelsize=9)
                return
            surf_title = raw.replace("**", "^").replace("*", "")
        else:
            # 默认: 涟漪
            x = np.linspace(-5, 5, 100)
            y = np.linspace(-5, 5, 100)
            X, Y = np.meshgrid(x, y)
            R = np.sqrt(X**2 + Y**2)
            Z = np.sin(R) / (R + 0.3)
            surf_title = "sin(r) / (r + 0.3)"

        # 渲染曲面
        cmap_name = "viridis" if is_dark else "plasma"
        try:
            surf = ax.plot_surface(X, Y, Z, cmap=cmap_name, alpha=0.92,
                                   linewidth=0, antialiased=True,
                                   rstride=1, cstride=1)
            fig.colorbar(surf, ax=ax, shrink=0.5, aspect=12, pad=0.08,
                         label="Z" if not title else "")
        except Exception as e:
            logger.warning(f"plot_surface failed: {e}, falling back to wireframe")
            ax.plot_wireframe(X, Y, Z, color="#7c3aed", linewidth=0.3, alpha=0.7)

        ax.set_title(title or surf_title, color=fg, fontsize=13, fontweight="bold")

        try:
            ax.set_box_aspect([1, 1, 1])
        except Exception:
            pass

        ax.tick_params(colors=fg, labelsize=9)
        ax.set_xlabel("X", color=fg, fontsize=10)
        ax.set_ylabel("Y", color=fg, fontsize=10)
        ax.set_zlabel("Z", color=fg, fontsize=10)

        try:
            ax.set_proj_type('ortho')
        except Exception:
            pass

    @staticmethod
    def draw_waveform(ax, data_sets, label_sets, is_dark=True):
        """波形图 — 数字时序波形 & 模拟波形，支持混合通道。

        模拟通道: data='sine,2,5;square,1,3' (type,freq_hz,amp[,phase_deg])
        数字通道: data='0,1,0,1;1,0,0,1' (每通道逗号分隔的电平值，支持多值)
        混合:    data='sine,2,5;0,1,0,0;1,0,1,1' (模拟+数字自动识别)
        """
        fg = "#e0e0e0" if is_dark else "#333"
        bg = "#1a1a2e" if is_dark else "#ffffff"
        grid_c = "#333333" if is_dark else "#dddddd"
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]

        wave_types = {"sine", "square", "triangle", "sawtooth", "sin", "cos"}

        # ── 逐通道分类：模拟 vs 数字 ──
        analog_channels = []   # (idx, ds, label)
        digital_channels = []  # (idx, ds, label)
        for i, ds in enumerate(data_sets):
            if not ds:
                continue
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else f"CH{i+1}"
            first_val = str(ds[0]).strip().lower()
            if first_val in wave_types:
                if len(ds) >= 3:  # 需要至少 type,freq,amp
                    analog_channels.append((i, ds, label))
                # 参数不足的模拟通道直接丢弃，不留空行
            else:
                digital_channels.append((i, ds, label))

        has_analog = len(analog_channels) > 0
        has_digital = len(digital_channels) > 0
        total_rows = len(analog_channels) + len(digital_channels)
        if total_rows == 0:
            ax.text(0.5, 0.5, "No waveform data", transform=ax.transAxes,
                    ha="center", color=fg)
            return

        # ── 布局参数 ──
        row_height = 1.0
        row_gap = 0.4 if has_digital else 0.15
        time_span = None  # 由数字通道数据长度统一

        # ── 模拟通道：统一时间轴 ──
        if has_analog:
            t_max = 0.0
            for _, ds, _ in analog_channels:
                if len(ds) < 3:
                    continue
                freq = float(ds[1]) if len(ds) > 1 else 1.0
                period = 1.0 / freq if freq > 0 else 1.0
                t_max = max(t_max, period * 3)
            # 数字通道影响时间跨度
            if has_digital:
                max_digital_len = max(len(ds) for _, ds, _ in digital_channels)
                t_max = max(t_max, float(max_digital_len))
            t = np.linspace(0, t_max, max(1000, int(t_max * 200)))

            for row_idx, (orig_idx, ds, label) in enumerate(analog_channels):
                if len(ds) < 3:
                    continue
                wtype = str(ds[0]).strip().lower()
                freq = float(ds[1]) if len(ds) > 1 else 1.0
                amp = float(ds[2]) if len(ds) > 2 else 1.0
                phase_deg = float(ds[3]) if len(ds) > 3 else 0.0
                phase_rad = np.radians(phase_deg)
                omega = 2 * np.pi * freq
                color = colors[row_idx % len(colors)]

                if wtype in ("sine", "sin"):
                    y = amp * np.sin(omega * t + phase_rad)
                elif wtype == "cos":
                    y = amp * np.cos(omega * t + phase_rad)
                elif wtype == "square":
                    y = amp * np.sign(np.sin(omega * t + phase_rad))
                elif wtype == "triangle":
                    raw = np.arcsin(np.sin(omega * t + phase_rad))
                    y = amp * (2 / np.pi) * raw
                elif wtype == "sawtooth":
                    phase_t = (omega * t + phase_rad) / (2 * np.pi)
                    y = amp * 2 * (phase_t - np.floor(phase_t + 0.5))
                else:
                    continue

                # 垂直偏移：模拟通道放在顶部
                base_y = (total_rows - 1 - row_idx) * (row_height + row_gap) + row_height / 2
                ax.plot(t, base_y + y, color=color, linewidth=2, label=label)
                ax.axhline(y=base_y, color=fg, linewidth=0.5, alpha=0.2)
                ax.text(-0.5, base_y + y.max() + 0.2, label,
                        color=color, fontsize=10, fontweight="bold",
                        ha="right", va="bottom")

            time_span = t_max

        # ── 数字通道：多值电平支持 ──
        if has_digital:
            # 无模拟通道时，时间轴来自数据长度
            if time_span is None:
                time_span = float(max(len(ds) for _, ds, _ in digital_channels))

            digital_offset = len(analog_channels)  # 数字通道从模拟下方开始
            for row_idx, (orig_idx, ds, label) in enumerate(digital_channels):
                levels = []
                for v in ds:
                    try:
                        levels.append(float(v))
                    except (ValueError, TypeError):
                        levels.append(0.0)

                if not levels:
                    continue

                n = len(levels)
                # 多值信号：归一化到 [0, 1] 映射到通道高度
                l_min, l_max = min(levels), max(levels)
                l_range = l_max - l_min if l_max != l_min else 1.0

                base_y = (total_rows - 1 - (digital_offset + row_idx)) * (row_height + row_gap)
                color = colors[(len(analog_channels) + row_idx) % len(colors)]

                # 绘制阶梯波形
                x_step, y_step = [], []
                for i, lv in enumerate(levels):
                    y_norm = (lv - l_min) / l_range
                    y_val = base_y + y_norm * row_height
                    if i == 0:
                        x_step.append(0)
                        y_step.append(y_val)
                    else:
                        x_step.append(i)
                        prev_norm = (levels[i-1] - l_min) / l_range
                        prev_y = base_y + prev_norm * row_height
                        y_step.append(prev_y)
                        x_step.append(i)
                        y_step.append(y_val)
                    if i == n - 1:
                        x_step.append(i + 1)
                        y_step.append(y_val)

                ax.plot(x_step, y_step, color=color, linewidth=2, solid_joinstyle="miter")

                # 高电平填充
                for i, lv in enumerate(levels):
                    y_norm = (lv - l_min) / l_range
                    if y_norm > 0.01:
                        ax.fill_between([i, i+1], base_y, base_y + y_norm * row_height,
                                        color=color, alpha=0.12)

                # 通道标签 + 电平标注
                ax.text(-0.5, base_y + row_height * 0.5, label,
                        color=color, fontsize=10, fontweight="bold",
                        ha="right", va="center")
                # 只在二进制时标注 0/1，多值时标注实际值
                for i, lv in enumerate(levels):
                    y_norm = (lv - l_min) / l_range
                    y_val = base_y + y_norm * row_height
                    # 二值信号标注 0/1，多值信号标注实际值
                    is_binary = len(set(levels)) <= 2
                    if is_binary and y_norm < 0.5:
                        # 低电平标注在下方
                        ax.text(i + 0.5, y_val - 0.12, str(lv),
                                color=color, fontsize=7, ha="center", alpha=0.6)
                    else:
                        # 高电平/多值标注在上方
                        ax.text(i + 0.5, y_val + 0.04, str(lv),
                                color=color, fontsize=7, ha="center", alpha=0.6)

            ax.set_xlabel("Clock cycle / Time", color=fg)

        # ── 通用轴设置 ──
        ax.set_xlim(-0.8, time_span + 0.8)
        y_bottom = -0.3
        y_top = total_rows * (row_height + row_gap)
        ax.set_ylim(y_bottom, y_top)
        ax.set_yticks([])
        ax.tick_params(colors=fg)
        for spine in ["bottom", "left"]:
            ax.spines[spine].set_color(grid_c)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, axis="x", alpha=0.15, color=grid_c)

    # ── 频谱 / FFT ─────────────────────────────────────────

    @staticmethod
    def draw_spectrum(ax, data_sets, label_sets, is_dark=True):
        """频谱图 — 对时域信号做 FFT 显示频域幅度谱。

        输入格式 (同 waveform):
          模拟: data='sine,2,5;square,6,3' (type,freq_hz,amp)
          数字: data='1,0,1,0,1,0;0,1,0,1' (采样值)
          混合: data='sine,2,5;1,0,0,1' (模拟+数字自动识别)

        labels[0] 可指定采样率: 'sample_rate' 或数字 (默认 1000 Hz)
        """
        fg = "#e0e0e0" if is_dark else "#333"
        grid_c = "#333333" if is_dark else "#dddddd"
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]
        wave_types = {"sine", "square", "triangle", "sawtooth", "sin", "cos"}

        # 采样率：从 labels[0] 提取数字
        sample_rate = 1000.0
        if label_sets and label_sets[0]:
            try:
                sample_rate = float(label_sets[0][0])
            except (ValueError, TypeError, IndexError):
                pass

        # ── 逐通道生成时域信号 → FFT ──
        max_freq = 0.0
        spectrum_lines = []  # (freqs, magnitude, label, color)

        for i, ds in enumerate(data_sets):
            if not ds:
                continue
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else f"CH{i+1}"
            first_val = str(ds[0]).strip().lower()
            n_samples = max(1024, int(sample_rate * 0.5))

            if first_val in wave_types and len(ds) >= 3:
                # 模拟信号 → 生成时域 → FFT
                wtype = first_val
                freq = float(ds[1])
                amp = float(ds[2])
                phase_deg = float(ds[3]) if len(ds) > 3 else 0.0
                phase_rad = np.radians(phase_deg)
                t = np.linspace(0, n_samples / sample_rate, n_samples, endpoint=False)
                omega = 2 * np.pi * freq

                if wtype in ("sine", "sin"):
                    y = amp * np.sin(omega * t + phase_rad)
                elif wtype == "cos":
                    y = amp * np.cos(omega * t + phase_rad)
                elif wtype == "square":
                    y = amp * np.sign(np.sin(omega * t + phase_rad))
                elif wtype == "triangle":
                    y = amp * (2 / np.pi) * np.arcsin(np.sin(omega * t + phase_rad))
                elif wtype == "sawtooth":
                    phase_t = (omega * t + phase_rad) / (2 * np.pi)
                    y = amp * 2 * (phase_t - np.floor(phase_t + 0.5))
                else:
                    continue
                max_freq = max(max_freq, freq)
            else:
                # 数字信号 → 采样值
                samples = []
                for v in ds:
                    try:
                        samples.append(float(v))
                    except (ValueError, TypeError):
                        samples.append(0.0)
                if not samples:
                    continue
                n_samples = max(1024, len(samples) * 8)
                # 上采样（零阶保持）
                t = np.linspace(0, n_samples / sample_rate, n_samples, endpoint=False)
                y = np.zeros(n_samples)
                ratio = n_samples // len(samples)
                for j, v in enumerate(samples):
                    start = j * ratio
                    end = start + ratio
                    if end > n_samples:
                        end = n_samples
                    y[start:end] = v
                max_freq = max(max_freq, sample_rate / (2 * len(samples)))

            # FFT
            fft = np.fft.rfft(y)
            freqs = np.fft.rfftfreq(n_samples, 1.0 / sample_rate)
            magnitude = np.abs(fft) / n_samples * 2  # 归一化（单边谱）
            magnitude[0] /= 2  # DC 分量不乘 2
            spectrum_lines.append((freqs, magnitude, label, colors[i % len(colors)]))

        # ── 绘图 ──
        for freqs, mag, label, color in spectrum_lines:
            ax.plot(freqs, mag, color=color, linewidth=1.8, label=label, alpha=0.9)

        # 标注峰值
        for freqs, mag, label, color in spectrum_lines:
            if len(freqs) < 3:
                continue
            # 找前 5 个峰值
            peak_indices = []
            for j in range(1, len(mag) - 1):
                if mag[j] > mag[j-1] and mag[j] > mag[j+1]:
                    peak_indices.append((j, mag[j]))
            peak_indices.sort(key=lambda x: -x[1])
            for j, m in peak_indices[:5]:
                if m > mag.max() * 0.15:  # 只标显著峰值
                    ax.annotate(f"{freqs[j]:.1f}Hz", (freqs[j], m),
                                textcoords="offset points", xytext=(0, 8),
                                fontsize=7, color=color, ha="center", alpha=0.8)

        x_max = min(max_freq * 8, sample_rate / 2)
        ax.set_xlim(0, max(x_max, 10))
        ax.set_ylim(bottom=0)
        ax.set_xlabel("Frequency (Hz)", color=fg, fontsize=11)
        ax.set_ylabel("Magnitude", color=fg, fontsize=11)
        ax.set_title("Frequency Spectrum (FFT)", color=fg, fontsize=13, fontweight="bold")
        ax.tick_params(colors=fg)
        ax.grid(True, alpha=0.15, color=grid_c)
        if len(spectrum_lines) > 1:
            ax.legend(facecolor="#1a1a2e" if is_dark else "#f5f5f5",
                      edgecolor="#444" if is_dark else "#ccc", labelcolor=fg)

    # ── 等高线 / 梯度下降 ──────────────────────────────

    _SAFE_FUNCTIONS = {
        "sin": np.sin, "cos": np.cos, "tan": np.tan,
        "exp": np.exp, "log": np.log, "sqrt": np.sqrt,
        "abs": np.abs, "pi": np.pi, "e": np.e,
    }

    @staticmethod
    def _parse_trajectory(data_sets, label_sets) -> list:
        """解析梯度下降轨迹点序列。

        输入格式（_parse_multi 拆分后）：
          [['0','5'], ['1','3'], ...]   — 每组一对坐标
          [['0,5;1,3;2,1']]             — 整体字符串含 ; (原始格式)
          [[0, 5, 1, 3, ...]]           — 数值列表
        """
        pts = []

        def _try_pair(a, b):
            try:
                return (float(a), float(b))
            except (ValueError, TypeError):
                return None

        for ds in (data_sets or []):
            for item in ds:
                if isinstance(item, str) and ";" in item:
                    # 整体字符串格式: "x,y;x,y;..."
                    for pair in item.split(";"):
                        parts = pair.strip().split(",")
                        if len(parts) >= 2:
                            p = _try_pair(parts[0], parts[1])
                            if p:
                                pts.append(p)
            # 每组一对坐标: ['0','5'] 或 ['0', '5', 'extra']
            if len(ds) >= 2:
                p = _try_pair(ds[0], ds[1])
                if p:
                    pts.append(p)
        # labels 中的轨迹点（同格式）
        for group in (label_sets or []):
            for item in group:
                if isinstance(item, str) and ";" in item:
                    for pair in item.split(";"):
                        parts = pair.strip().split(",")
                        if len(parts) >= 2:
                            p = _try_pair(parts[0], parts[1])
                            if p:
                                pts.append(p)
            if len(group) >= 2:
                p = _try_pair(group[0], group[1])
                if p:
                    pts.append(p)
        return pts

    @classmethod
    def _safe_eval_2d(cls, expr: str, X: np.ndarray, Y: np.ndarray) -> np.ndarray | None:
        """安全求值 2D 数学表达式 Z = f(X, Y)。失败返回 None。"""
        # LLM 写法兼容:
        #   x²+y² → x**2+y**2     (Unicode 上标 → **n)
        #   10⁻²  → 10**(-2)       (负上标指数)
        #   X^2 → X**2
        #   2X → 2*X
        #   XY → X*Y              (隐式乘法)
        _SUPER_DIGITS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                         "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9"}
        # ⁻+数字 → **(-n)   (e.g. x⁻² → x**(-2))
        for u, d in _SUPER_DIGITS.items():
            expr = expr.replace("⁻" + u, f"**(-{d})")
        # 纯上标数字 → **n   (e.g. x² → x**2)
        for u, d in _SUPER_DIGITS.items():
            expr = expr.replace(u, f"**{d}")
        expr = expr.replace("⁻", "**-")  # e⁻ˣ → e**-x
        expr = expr.replace("⁺", "**+")
        # 剥离 np. 前缀（与 draw_function 一致）
        for _fn in ("sin", "cos", "tan", "exp", "log", "sqrt", "abs"):
            expr = expr.replace(f"np.{_fn}", _fn)
        expr = expr.replace("np.pi", "pi").replace("np.e", "e")
        expr = expr.replace("^", "**")
        expr = re.sub(r"(\d)([a-zA-Z])", r"\1*\2", expr)
        expr = re.sub(r"(\d)\*([eE])(\d)", r"\1\2\3", expr)  # 修复科学计数法
        # 隐式乘法 XY→X*Y，但不拆分已知函数名 (sin/cos/exp/log/abs/sqrt/tan/pi)
        expr = re.sub(r"(?<![a-zA-Z])([a-zA-Z])([a-zA-Z])(?![a-zA-Z])", r"\1*\2", expr)
        expr = expr.replace("p*i", "pi")  # 保护 pi 常量
        ns = {"X": X, "Y": Y, "x": X, "y": Y, **cls._SAFE_FUNCTIONS}
        try:
            Z = _safe_eval_math(expr, ns)
            return np.asarray(Z, dtype=float)
        except Exception as e:
            logger.warning(f"Contour expression eval failed for '{expr}': {e}")
            return None

    @staticmethod
    def _parse_points(data_sets: list[list[str]]) -> np.ndarray:
        """从 data_sets 解析 (x, y) 坐标点序列。"""
        pts = []
        for ds in data_sets:
            for item in ds:
                try:
                    val = float(item)
                    pts.append(val)
                except ValueError:
                    continue
        if len(pts) < 2:
            return np.array([])
        if len(pts) % 2 != 0:
            pts = pts[:-1]
        return np.array(pts).reshape(-1, 2)

    @staticmethod
    def draw_contour(ax, data_sets, label_sets, is_dark=True, **kwargs):
        """等高线图 + 梯度下降轨迹。

        data = "X**2+Y**2"                       → 纯等高线
        data = "X**2+Y**2", labels = "0,0;1,2;0.5,3"  → 等高线 + 轨迹
        data = "0,5;1,3;2,1.5;2.5,0.5;2.5,0"   → 纯轨迹
        """
        fg = "#e0e0e0" if is_dark else "#333"
        bg_c = "#1a1a2e" if is_dark else "#f0f0f0"

        first_item = data_sets[0][0] if data_sets and data_sets[0] else ""

        # 表达式检测：包含函数名/数学运算符，且不含 ';'（轨迹分隔符）
        _FN_NAMES = ("sin", "cos", "tan", "exp", "log", "abs", "sqrt")
        has_fn = any(fn in first_item.lower() for fn in _FN_NAMES)
        has_op = any(op in first_item for op in ("**", "*", "+", "-", "/", "^"))
        has_semicolon = ";" in first_item
        is_expr = (has_fn or has_op) and not has_semicolon

        expr = None
        Z = X_grid = Y_grid = None

        if is_expr:
            expr = first_item.strip().strip("`'\"")
            # 范围
            x_min, x_max = -5.0, 5.0
            y_min, y_max = -5.0, 5.0
            for ds in data_sets[1:]:
                nums = []
                for x in ds:
                    try:
                        nums.append(float(x))
                    except (ValueError, TypeError):
                        pass
                if len(nums) >= 4:
                    x_min, x_max, y_min, y_max = nums[:4]
                elif len(nums) >= 2:
                    x_min, x_max = nums[0], nums[1]
                break
            X_grid, Y_grid = np.meshgrid(
                np.linspace(x_min, x_max, 100),
                np.linspace(y_min, y_max, 100),
            )
            Z = AdvancedCharts._safe_eval_2d(expr, X_grid, Y_grid)

        # 画填充等高线
        if Z is not None:
            try:
                levels = 15 if Z.min() != Z.max() else [Z.min(), Z.min() + 1]
                cmap = "coolwarm" if is_dark else "RdYlBu_r"
                ax.contourf(X_grid, Y_grid, Z, levels=levels, cmap=cmap, alpha=0.6)
                cs = ax.contour(X_grid, Y_grid, Z, levels=levels,
                                colors="#555" if is_dark else "#999", linewidths=0.5, alpha=0.4)
                ax.clabel(cs, inline=True, fontsize=7, fmt="%.0f")
            except Exception as e:
                logger.warning(f"Contour failed: {e}")
        ax.set_facecolor(bg_c)

        # 解析并画轨迹
        traj_data = data_sets[1:] if is_expr else data_sets
        traj_pts = AdvancedCharts._parse_trajectory(traj_data, label_sets)

        if traj_pts:
            c_gold = "#fbbf24" if is_dark else "#d97706"
            tx = [p[0] for p in traj_pts]
            ty = [p[1] for p in traj_pts]

            # 连线 + 点
            ax.plot(tx, ty, "o-", color=c_gold, linewidth=2, markersize=7,
                    markerfacecolor="white", markeredgecolor=c_gold,
                    markeredgewidth=2, zorder=5, label="Descent")

            # 箭头
            for i in range(len(traj_pts) - 1):
                ax.annotate("", xy=traj_pts[i + 1], xytext=traj_pts[i],
                            arrowprops=dict(arrowstyle="->", color=c_gold, lw=1.5), zorder=6)

            # 起点 / 终点
            if traj_pts:
                ax.plot(*traj_pts[0], "o", color="#10b981", markersize=10, zorder=7, label="Start")
                if len(traj_pts) > 1:
                    ax.plot(*traj_pts[-1], "*", color="#ef4444", markersize=14, zorder=7, label="Min")

            # 步数标签
            for i, (px, py) in enumerate(traj_pts):
                ax.annotate(str(i + 1), (px, py), textcoords="offset points",
                            xytext=(8, 8), fontsize=8, color=fg)

            lc = "#222" if is_dark else "#f0f0f0"
            ax.legend(facecolor=lc, edgecolor="#444" if is_dark else "#ccc", labelcolor=fg)

        # 标题
        if expr:
            ax.set_title(f"Z = {expr.replace('**', '^').replace('*', '·')}", color=fg, fontsize=13, fontweight="bold")
        elif traj_pts:
            ax.set_title("Gradient Descent Path", color=fg, fontsize=13)

        ax.set_xlabel("θ₀ / x", color=fg, fontsize=11)
        ax.set_ylabel("θ₁ / y", color=fg, fontsize=11)
        ax.set_aspect("equal", adjustable="box")
        ax.tick_params(colors=fg)
