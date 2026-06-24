"""画图工具：generate_chart — matplotlib 生成 14 种图表。

line | curve | bar | scatter | pie | histogram | area
heatmap | radar | bubble | function

图片保存到 web/static/charts/ 目录，前端可访问。
"""

import logging
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger("nano_agent.tools.chart")


class Chart:
    # 工具注册声明
    TOOLS = [
        ("generate_chart", "Generate charts, math plots, and regression analysis. Data: chart_type=line/bar (data='10,20'). Least squares: chart_type=regression, data='x1,y1;x2,y2' or 'y1,y2,...'. Math: chart_type=function (data='sin(x);-3;3'). Drawings: chart_type=draw/cat.", "generate_chart",
         {"chart_type": {"type": "string", "description": "Chart type: line, curve, bar, scatter, pie, histogram, area, heatmap, radar, bubble, function, regression, draw, cat (default: line)"},
          "data": {"type": "string", "description": "Data: comma-sep values. Regression: 'x,y;x,y...' or 'y1,y2...'. Multi-series: semicolon-sep."},
          "title": {"type": "string", "description": "Chart title"},
          "labels": {"type": "string", "description": "Series labels or shape definitions (semicolon-separated)"},
          "x_label": {"type": "string", "description": "X-axis label"},
          "y_label": {"type": "string", "description": "Y-axis label"},
          "width": {"type": "integer", "description": "Image width (default: 512)"},
          "height": {"type": "integer", "description": "Image height (default: 384)"}},
         []),
    ]

    def __init__(self, work_dir: str, charts_dir: str = ""):
        self.work_dir = work_dir
        # 图片保存到 charts_dir，默认回退到旧路径
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            web_static = Path(__file__).parent.parent.parent / "web" / "static"
            self.charts_dir = web_static / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def generate_chart(
        self,
        chart_type: str = "line",
        title: str = "",
        data: str = "",
        labels: str = "",
        x_label: str = "",
        y_label: str = "",
        filename: str = "",
        style: str = "dark",
    ) -> str:
        # 解析数据 (raw types 不过度拆分逗号)
        raw_types = set()
        if chart_type in raw_types:
            data_sets = [[data.strip()]] if data.strip() else [[]]
            label_sets = [[labels.strip()]] if labels.strip() else [[]]
        else:
            try:
                data_sets = self._parse_multi(data)
                label_sets = self._parse_multi(labels) if labels else []
            except Exception as e:
                return f"Error parsing data: {e}"

        no_data_types = {"draw", "cat"}
        if (not data_sets or not data_sets[0]) and chart_type not in no_data_types:
            return "Error: data is required (e.g. '10,20,30,40')"

        # 设置中文字体
        plt.rcParams["font.sans-serif"] = [
            "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Arial Unicode MS", "PingFang SC", "Heiti SC",
            "Microsoft YaHei", "SimHei", "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        # 生成图表
        chart_type = chart_type.lower().strip()
        style = style.lower().strip()
        is_dark = style != "light"
        bg = "#1a1a2e" if is_dark else "#ffffff"
        fg = "#e0e0e0" if is_dark else "#333333"
        grid_c = "#333" if is_dark else "#ddd"
        fig, ax = plt.subplots(figsize=(10, 6), facecolor=bg)
        ax.set_facecolor(bg)
        # 雷达图需要极坐标
        if chart_type == "radar":
            fig.delaxes(ax)
            ax = fig.add_subplot(111, polar=True)
            ax.set_facecolor(bg)

        try:
            if chart_type == "line":
                self._draw_line(ax, data_sets, label_sets, is_dark)
            elif chart_type == "curve":
                self._draw_curve(ax, data_sets, label_sets, is_dark)
            elif chart_type == "bar":
                self._draw_bar(ax, data_sets, label_sets, is_dark)
            elif chart_type == "scatter":
                self._draw_scatter(ax, data_sets, label_sets, is_dark)
            elif chart_type == "pie":
                self._draw_pie(ax, data_sets, label_sets, is_dark)
            elif chart_type == "histogram":
                self._draw_histogram(ax, data_sets, label_sets, is_dark)
            elif chart_type == "area":
                self._draw_area(ax, data_sets, label_sets, is_dark)
            elif chart_type == "heatmap":
                self._draw_heatmap(ax, data_sets, label_sets, is_dark)
            elif chart_type == "radar":
                self._draw_radar(ax, data_sets, label_sets, is_dark)
            elif chart_type == "bubble":
                self._draw_bubble(ax, data_sets, label_sets, is_dark)
            elif chart_type == "function":
                self._draw_function(ax, data_sets, label_sets, is_dark)
            elif chart_type == "regression":
                self._draw_regression(ax, data_sets, label_sets, is_dark)
            elif chart_type in ("draw", "cat"):
                self._draw_shapes(ax, labels, is_dark, is_cat=(chart_type == "cat"))
            else:
                return f"Error: Unknown chart type '{chart_type}'. Supported: line, curve, bar, scatter, pie, histogram, area, heatmap, radar, bubble, function, regression, draw, cat"
        except Exception as e:
            plt.close(fig)
            return f"Error generating chart: {e}"

        # 样式
        if chart_type not in ("pie", "radar"):
            ax.tick_params(colors=fg)
            for spine in ["bottom", "left"]:
                ax.spines[spine].set_color(grid_c)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            if x_label:
                ax.set_xlabel(x_label, color=fg, fontsize=12)
            if y_label:
                ax.set_ylabel(y_label, color=fg, fontsize=12)
            ax.grid(True, alpha=0.2, color=grid_c)
        elif chart_type == "radar":
            ax.tick_params(colors=fg, labelcolor=fg)
            ax.grid(True, alpha=0.3, color=grid_c)

        if title:
            fig.suptitle(title, color=fg, fontsize=16, fontweight="bold")

        fig.tight_layout()
        self._cleanup(max_files=50)

        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"chart_{ts}.png"
        if not filename.endswith(".png"):
            filename += ".png"
        filepath = self.charts_dir / filename

        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=bg)
        plt.close(fig)
        url = f"/charts/{filename}"
        return f"Chart generated: {url}\n![{title}]({url})"

    # ── 解析 ──

    def _parse_multi(self, s: str) -> list[list]:
        """解析多组数据/标签，分号分隔组，逗号分隔项。"""
        groups = []
        for part in s.split(";"):
            items = [x.strip() for x in part.split(",") if x.strip()]
            groups.append(items)
        return groups

    # ── 绘图 ──

    def _draw_line(self, ax, data_sets, label_sets, is_dark=True):
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            x = range(len(vals))
            ax.plot(x, vals, color=colors[i % len(colors)], marker="o",
                    linewidth=2, markersize=4, label=label)
        if any(i < len(label_sets) and label_sets[i] for i in range(len(data_sets))):
            lc = "#222" if is_dark else "#f0f0f0"
            ax.legend(facecolor=lc, edgecolor="#444" if is_dark else "#ccc",
                      labelcolor="#ccc" if is_dark else "#333")

    def _draw_curve(self, ax, data_sets, label_sets, is_dark=True):
        """平滑曲线 — scipy spline 优先，回退到 numpy polyfit。"""
        import numpy as np
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]
        for i, ds in enumerate(data_sets):
            vals = np.array([float(x) for x in ds])
            n = len(vals)
            if n < 3:
                ax.plot(range(n), vals, color=colors[i % len(colors)], marker="o",
                        linewidth=2, markersize=4)
                continue
            x = np.linspace(0, n - 1, 200)
            try:
                from scipy.interpolate import make_interp_spline
                spl = make_interp_spline(range(n), vals, k=min(3, n - 1))
                y = spl(x)
            except ImportError:
                z = np.polyfit(range(n), vals, min(4, n - 1))
                y = np.polyval(z, x)
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            ax.plot(x, y, color=colors[i % len(colors)], linewidth=2, label=label)
            ax.scatter(range(n), vals, color=colors[i % len(colors)], s=20, zorder=5)
        if any(i < len(label_sets) and label_sets[i] for i in range(len(data_sets))):
            lc = "#222" if is_dark else "#f0f0f0"
            ax.legend(facecolor=lc, edgecolor="#444" if is_dark else "#ccc",
                      labelcolor="#ccc" if is_dark else "#333")

    def _draw_bar(self, ax, data_sets, label_sets, is_dark=True):
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444"]
        x_labels = label_sets[0] if label_sets else None
        n = len(data_sets)
        width = 0.8 / max(n, 1)
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            offset = (i - (n - 1) / 2) * width
            x = [j + offset for j in range(len(vals))]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            ax.bar(x, vals, width=width, color=colors[i % len(colors)], label=label, alpha=0.85)
        if x_labels:
            ax.set_xticks(range(len(x_labels)))
            ax.set_xticklabels(x_labels, color="#ccc" if is_dark else "#333")
        if n > 1:
            ax.legend(facecolor="#222" if is_dark else "#f0f0f0", edgecolor="#444" if is_dark else "#ccc", labelcolor="#ccc" if is_dark else "#333")

    def _draw_scatter(self, ax, data_sets, label_sets, is_dark=True):
        colors = ["#7c3aed", "#3b82f6", "#10b981"]
        # 需要 x,y 对：第一组=x，第二组=y
        if len(data_sets) < 2:
            vals = [float(x) for x in data_sets[0]]
            ax.scatter(range(len(vals)), vals, color=colors[0], s=60, alpha=0.8)
        else:
            xs = [float(x) for x in data_sets[0]]
            ys = [float(x) for x in data_sets[1]]
            ax.scatter(xs, ys, color=colors[0], s=60, alpha=0.8)

    def _draw_pie(self, ax, data_sets, label_sets, is_dark=True):
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899", "#14b8a6"]
        vals = [float(x) for x in data_sets[0]]
        labels = label_sets[0] if label_sets else None
        ax.pie(vals, labels=labels, colors=colors[:len(vals)],
               autopct="%1.1f%%", textprops={"color": "#e0e0e0", "fontsize": 11},
               startangle=90)
        ax.set_facecolor("#1a1a2e")

    def _draw_histogram(self, ax, data_sets, label_sets):
        colors = ["#7c3aed", "#3b82f6"]
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            ax.hist(vals, bins=15, color=colors[i % len(colors)], alpha=0.7, label=label, edgecolor="#333")
        if len(data_sets) > 1:
            lc = "#222" if is_dark else "#f0f0f0"
            ax.legend(facecolor=lc, edgecolor="#444" if is_dark else "#ccc",
                      labelcolor="#ccc" if is_dark else "#333")

    def _draw_area(self, ax, data_sets, label_sets):
        colors = ["#7c3aed", "#3b82f6", "#10b981"]
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            ax.fill_between(range(len(vals)), vals, color=colors[i % len(colors)],
                            alpha=0.4, label=label)
            ax.plot(range(len(vals)), vals, color=colors[i % len(colors)], linewidth=1.5)
        if any(i < len(label_sets) and label_sets[i] for i in range(len(data_sets))):
            lc = "#222" if is_dark else "#f0f0f0"
            ax.legend(facecolor=lc, edgecolor="#444" if is_dark else "#ccc",
                      labelcolor="#ccc" if is_dark else "#333")

    # ── 新图表类型 ──

    def _draw_heatmap(self, ax, data_sets, label_sets, is_dark=True):
        """热力图 — 矩阵数据，行用分号，列用逗号。"""
        import numpy as np
        matrix = np.array([[float(x) for x in ds] for ds in data_sets])
        im = ax.imshow(matrix, cmap="coolwarm", aspect="auto")
        cbar = plt.colorbar(im, ax=ax)
        cbar.ax.yaxis.set_tick_params(color="#ccc" if is_dark else "#333")
        for i in range(len(data_sets)):
            for j in range(len(data_sets[0])):
                ax.text(j, i, f"{matrix[i,j]:.1f}", ha="center", va="center",
                        color="white" if abs(matrix[i,j]) > matrix.max()/2 else "black",
                        fontsize=10)
        if label_sets and label_sets[0]:
            ax.set_xticks(range(len(label_sets[0])))
            ax.set_xticklabels(label_sets[0], color="#ccc" if is_dark else "#333")
        if label_sets and len(label_sets) > 1:
            ax.set_yticks(range(len(label_sets[1])))
            ax.set_yticklabels(label_sets[1], color="#ccc" if is_dark else "#333")

    def _draw_radar(self, ax, data_sets, label_sets, is_dark=True):
        """雷达图 — 第一组为数值，label_sets[0] 为维度名。"""
        import numpy as np
        vals = [float(x) for x in data_sets[0]]
        n = len(vals)
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        angles += angles[:1]
        vals += vals[:1]
        colors = ["#7c3aed", "#3b82f6", "#10b981"]
        for i, ds in enumerate(data_sets):
            v = [float(x) for x in ds] + [float(ds[0])]
            ax.fill(angles, v, alpha=0.25, color=colors[i % len(colors)])
            ax.plot(angles, v, color=colors[i % len(colors)], linewidth=2)
        if label_sets and label_sets[0]:
            cats = label_sets[0] + [label_sets[0][0]]
            ax.set_xticks(angles)
            ax.set_xticklabels(cats, color="#ccc" if is_dark else "#333", fontsize=10)

    def _draw_bubble(self, ax, data_sets, label_sets, is_dark=True):
        """气泡图 — 三组数据: x;y;size。label_sets[0] 为各点标签。"""
        import numpy as np
        if len(data_sets) < 3:
            ax.scatter([float(x) for x in data_sets[0]], [float(x) for x in data_sets[1]] if len(data_sets) > 1 else [0],
                       s=60, alpha=0.8, color="#7c3aed")
            return
        xs = np.array([float(x) for x in data_sets[0]])
        ys = np.array([float(x) for x in data_sets[1]])
        sizes = np.array([float(x) for x in data_sets[2]]) * 50
        sc = ax.scatter(xs, ys, s=sizes, alpha=0.6, c=sizes, cmap="coolwarm", edgecolors="#fff", linewidth=0.5)
        if label_sets and label_sets[0]:
            for i, lbl in enumerate(label_sets[0]):
                if i < len(xs):
                    ax.annotate(lbl, (xs[i], ys[i]), textcoords="offset points", xytext=(0, 8),
                                ha="center", fontsize=9, color="#ccc" if is_dark else "#333")
        plt.colorbar(sc, ax=ax)

    def _draw_function(self, ax, data_sets, label_sets, is_dark=True):
        """数学函数绘图 — data[0][0] 是 Python 表达式，例: x**2, sin(x), log(x)。"""
        import numpy as np
        expr = data_sets[0][0] if data_sets and data_sets[0] else "x"
        x_range = (-5, 5)
        if len(data_sets) > 1 and data_sets[1]:
            x_range = (float(data_sets[1][0]) if data_sets[1] else -5,
                       float(data_sets[1][1]) if len(data_sets[1]) > 1 else 5)
        x = np.linspace(x_range[0], x_range[1], 500)
        ns = {"x": x, "np": np, "sin": np.sin, "cos": np.cos, "tan": np.tan,
              "exp": np.exp, "log": np.log, "sqrt": np.sqrt, "abs": np.abs,
              "pi": np.pi, "e": np.e}
        try:
            y = eval(expr, {"__builtins__": {}}, ns)
        except Exception:
            ns["x"] = np.linspace(x_range[0], x_range[1], 500)
            y = eval(expr, {"__builtins__": {}}, ns)
        fg = "#e0e0e0" if is_dark else "#333"
        ax.plot(x, y, color="#7c3aed", linewidth=2)
        ax.axhline(y=0, color=fg, linewidth=0.5, alpha=0.5)
        ax.axvline(x=0, color=fg, linewidth=0.5, alpha=0.5)
        ax.set_title(f"y = {expr}", color=fg, fontsize=14, fontweight="bold")
        ax.set_xlabel("x", color=fg)
        ax.set_ylabel("y", color=fg)

    def _draw_shapes(self, ax, labels, is_dark=True, is_cat=False):
        """绘制形状。labels 格式: 'circle:0,0,3,red;rect:1,1,2,1,blue'。"""
        import numpy as np
        fg = "#e0e0e0" if is_dark else "#222"

        if not labels:
            if is_cat:
                self._draw_cat(ax, is_dark)
                return
            return "Error: labels required for draw. Format: 'circle:0,0,3,red;rect:1,1,2,1,blue'"

        shapes = labels.split(";")
        for shape_str in shapes:
            parts = shape_str.strip().split(":")
            if len(parts) < 2:
                continue
            kind = parts[0].strip().lower()
            params = [p.strip() for p in parts[1].split(",")]
            try:
                if kind == "circle" and len(params) >= 4:
                    cx, cy, r = float(params[0]), float(params[1]), float(params[2])
                    color = params[3]
                    circle = plt.Circle((cx, cy), r, fill=False, edgecolor=color, linewidth=2)
                    ax.add_patch(circle)
                elif kind == "rect" and len(params) >= 5:
                    x, y, w, h = float(params[0]), float(params[1]), float(params[2]), float(params[3])
                    color = params[4]
                    rect = plt.Rectangle((x, y), w, h, fill=False, edgecolor=color, linewidth=2)
                    ax.add_patch(rect)
                elif kind == "line" and len(params) >= 5:
                    x1, y1, x2, y2 = float(params[0]), float(params[1]), float(params[2]), float(params[3])
                    color = params[4]
                    ax.plot([x1, x2], [y1, y2], color=color, linewidth=2)
                elif kind == "text" and len(params) >= 3:
                    tx, ty = float(params[0]), float(params[1])
                    txt = ",".join(params[2:])
                    ax.text(tx, ty, txt, color=fg, fontsize=12, ha="center")
            except (ValueError, IndexError):
                continue

        ax.set_xlim(-5, 5)
        ax.set_ylim(-5, 5)
        ax.set_aspect("equal")

    def _draw_cat(self, ax, is_dark=True):
        """画一只简笔猫。"""
        import numpy as np
        fg = "#e0e0e0" if is_dark else "#222"
        accent = "#7c3aed"

        # 头 (圆)
        head = plt.Circle((0, 0), 2, fill=False, edgecolor=fg, linewidth=2.5)
        ax.add_patch(head)

        # 左耳 (三角形)
        ax.plot([-1.6, -1.0, -1.8], [1.4, 2.5, 2.5], color=fg, linewidth=2)
        ax.fill([-1.6, -1.0, -1.8], [1.4, 2.5, 2.5], color=accent, alpha=0.3)
        # 右耳
        ax.plot([1.6, 1.0, 1.8], [1.4, 2.5, 2.5], color=fg, linewidth=2)
        ax.fill([1.6, 1.0, 1.8], [1.4, 2.5, 2.5], color=accent, alpha=0.3)

        # 眼睛
        left_eye = plt.Circle((-0.7, 0.5), 0.3, fill=True, facecolor=accent, edgecolor=fg, linewidth=1.5)
        right_eye = plt.Circle((0.7, 0.5), 0.3, fill=True, facecolor=accent, edgecolor=fg, linewidth=1.5)
        ax.add_patch(left_eye)
        ax.add_patch(right_eye)
        # 瞳孔
        ax.plot(-0.7, 0.5, "o", color=fg, markersize=4)
        ax.plot(0.7, 0.5, "o", color=fg, markersize=4)

        # 鼻子 (小三角)
        ax.plot([0, -0.15, 0.15, 0], [0.0, -0.2, -0.2, 0.0], color="#f59e0b", linewidth=2)

        # 嘴
        theta = np.linspace(0, np.pi, 30)
        ax.plot(0.3 * np.cos(theta) - 0.3, -0.3 * np.sin(theta) - 0.3, color=fg, linewidth=1.5)
        ax.plot(0.3 * np.cos(theta) + 0.3, -0.3 * np.sin(theta) - 0.3, color=fg, linewidth=1.5)

        # 胡须
        for dy in [-0.15, -0.35, -0.55]:
            ax.plot([-0.5, -2.0], [dy, dy - 0.1], color=fg, linewidth=1, alpha=0.7)
            ax.plot([0.5, 2.0], [dy, dy - 0.1], color=fg, linewidth=1, alpha=0.7)

        ax.set_xlim(-3.5, 3.5)
        ax.set_ylim(-3.5, 3.5)
        ax.set_aspect("equal")
        ax.axis("off")

    def _draw_regression(self, ax, data_sets, label_sets, is_dark=True):
        """最小二乘回归 — 散点 + 拟合直线 + 方程 + R²。
        data 格式: "x1,y1;x2,y2;x3,y3" 或 "y1,y2,y3"（后者自动生成 x=0,1,2...）
        """
        import numpy as np
        fg = "#e0e0e0" if is_dark else "#333"

        # 解析数据点
        pts = []
        for ds in data_sets:
            for item in ds:
                parts = item.split(",")
                if len(parts) >= 2:
                    pts.append((float(parts[0]), float(parts[1])))

        # 如果只有一组逗号数值，当作 y 值，自动生成 x=0,1,2...
        if not pts and data_sets and data_sets[0]:
            ys = [float(x) for x in data_sets[0]]
            pts = [(i, y) for i, y in enumerate(ys)]

        if len(pts) < 2:
            ax.text(0.5, 0.5, "Need ≥2 data points for regression", transform=ax.transAxes,
                    ha="center", color=fg)
            return

        xs = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])

        # 最小二乘: y = a + bx
        n = len(xs)
        b = (n * (xs * ys).sum() - xs.sum() * ys.sum()) / (n * (xs * xs).sum() - xs.sum() ** 2)
        a = (ys.sum() - b * xs.sum()) / n

        # R²
        y_pred = a + b * xs
        ss_res = ((ys - y_pred) ** 2).sum()
        ss_tot = ((ys - ys.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        # 画散点
        ax.scatter(xs, ys, color="#7c3aed", s=60, zorder=5, alpha=0.8, edgecolors="white", linewidth=0.5)

        # 拟合直线
        x_line = np.linspace(xs.min(), xs.max(), 200)
        ax.plot(x_line, a + b * x_line, color="#f59e0b", linewidth=2, zorder=4)

        # 方程 + R²
        sign = "+" if b >= 0 else "-"
        eq = f"y = {a:.2f} {sign} {abs(b):.2f}x"
        ax.text(0.05, 0.95, f"{eq}\nR² = {r2:.4f}", transform=ax.transAxes,
                fontsize=12, color=fg, verticalalignment="top",
                bbox=dict(boxstyle="round", facecolor="#1a1a2e" if is_dark else "#f5f5f5",
                          edgecolor=fg, alpha=0.8))
        ax.set_xlabel("x", color=fg)
        ax.set_ylabel("y", color=fg)

    # ── 清理 ──

    def _cleanup(self, max_files: int = 50):
        """保留最近 max_files 个图表文件。"""
        files = sorted(self.charts_dir.glob("*.png"), key=lambda f: f.stat().st_mtime)
        for f in files[:-max_files]:
            try:
                f.unlink()
            except OSError:
                pass
