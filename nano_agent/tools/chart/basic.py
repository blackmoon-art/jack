"""基础图表: line, curve, bar, scatter, pie, histogram, area。"""

import matplotlib.pyplot as plt
import numpy as np


class BasicCharts:
    """基础图表绘制 — 无状态，所有方法接收 ax 和数据参数。"""

    @staticmethod
    def _extract_xy(data_sets, label_sets):
        """如果 labels[0] 是 'x'，则 data_sets[0] 为 X 坐标，其余为 Y 系列。"""
        if label_sets and label_sets[0] and label_sets[0][0].strip().lower() == "x":
            x_vals = [float(v) for v in data_sets[0]]
            y_sets = data_sets[1:]
            y_labels = label_sets[1:]
            return x_vals, y_sets, y_labels
        return None, data_sets, label_sets

    @staticmethod
    def _legend(ax, label_sets, data_sets, is_dark):
        if any(i < len(label_sets) and label_sets[i] for i in range(len(data_sets))):
            lc = "#222" if is_dark else "#f0f0f0"
            ax.legend(facecolor=lc, edgecolor="#444" if is_dark else "#ccc",
                      labelcolor="#ccc" if is_dark else "#333")

    @staticmethod
    def draw_line(ax, data_sets, label_sets, is_dark=True):
        x_vals, data_sets, label_sets = BasicCharts._extract_xy(data_sets, label_sets)
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            x = x_vals if x_vals and len(x_vals) == len(vals) else range(len(vals))
            ax.plot(x, vals, color=colors[i % len(colors)], marker="o",
                    linewidth=2, markersize=4, label=label)
        BasicCharts._legend(ax, label_sets, data_sets, is_dark)

    @staticmethod
    def draw_curve(ax, data_sets, label_sets, is_dark=True):
        """平滑曲线 — scipy spline 优先，回退到 numpy polyfit。"""
        x_vals, data_sets, label_sets = BasicCharts._extract_xy(data_sets, label_sets)
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]
        for i, ds in enumerate(data_sets):
            vals = np.array([float(x) for x in ds])
            n = len(vals)
            if n < 3:
                x = x_vals if x_vals and len(x_vals) == n else range(n)
                ax.plot(x, vals, color=colors[i % len(colors)], marker="o",
                        linewidth=2, markersize=4)
                continue
            x_smooth = np.linspace(x_vals[0] if x_vals else 0, x_vals[-1] if x_vals else n - 1, 200)
            x_raw = x_vals if x_vals and len(x_vals) == n else np.arange(n)
            try:
                from scipy.interpolate import make_interp_spline
                spl = make_interp_spline(x_raw, vals, k=min(3, n - 1))
                y = spl(x_smooth)
            except ImportError:
                z = np.polyfit(x_raw, vals, min(4, n - 1))
                y = np.polyval(z, x_smooth)
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            ax.plot(x_smooth, y, color=colors[i % len(colors)], linewidth=2, label=label)
            ax.scatter(x_raw, vals, color=colors[i % len(colors)], s=20, zorder=5)
        BasicCharts._legend(ax, label_sets, data_sets, is_dark)

    @staticmethod
    def draw_bar(ax, data_sets, label_sets, is_dark=True):
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444"]
        x_labels = label_sets[0] if label_sets else None
        n = len(data_sets)
        width = 0.8 / max(n, 1)
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            offset = (i - (n - 1) / 2) * width
            x = [j + offset for j in range(len(vals))]
            label = label_sets[i+1][0] if i+1 < len(label_sets) and label_sets[i+1] else None
            ax.bar(x, vals, width=width, color=colors[i % len(colors)], label=label, alpha=0.85)
        if x_labels:
            ax.set_xticks(range(len(x_labels)))
            ax.set_xticklabels(x_labels, color="#ccc" if is_dark else "#333")
        if n > 1:
            ax.legend(facecolor="#222" if is_dark else "#f0f0f0", edgecolor="#444" if is_dark else "#ccc", labelcolor="#ccc" if is_dark else "#333")

    @staticmethod
    def draw_scatter(ax, data_sets, label_sets, is_dark=True):
        x_vals, data_sets, label_sets = BasicCharts._extract_xy(data_sets, label_sets)
        colors = ["#7c3aed", "#3b82f6", "#10b981"]
        if len(data_sets) < 2:
            vals = [float(x) for x in data_sets[0]]
            x = x_vals if x_vals and len(x_vals) == len(vals) else range(len(vals))
            ax.scatter(x, vals, color=colors[0], s=60, alpha=0.8)
        else:
            xs = x_vals if x_vals else [float(x) for x in data_sets[0]]
            ys = [float(x) for x in data_sets[1]]
            ax.scatter(xs, ys, color=colors[0], s=60, alpha=0.8)

    @staticmethod
    def draw_pie(ax, data_sets, label_sets, is_dark=True):
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899", "#14b8a6"]
        vals = [float(x) for x in data_sets[0]]
        labels = label_sets[0] if label_sets else None
        text_color = "#e0e0e0" if is_dark else "#333"
        bg = "#1a1a2e" if is_dark else "#ffffff"
        ax.pie(vals, labels=labels, colors=colors[:len(vals)],
               autopct="%1.1f%%", textprops={"color": text_color, "fontsize": 11},
               startangle=90)
        ax.set_facecolor(bg)

    @staticmethod
    def draw_histogram(ax, data_sets, label_sets, is_dark=True):
        colors = ["#7c3aed", "#3b82f6"]
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            ax.hist(vals, bins=15, color=colors[i % len(colors)], alpha=0.7, label=label, edgecolor="#333")
        if len(data_sets) > 1:
            BasicCharts._legend(ax, label_sets, data_sets, is_dark)

    @staticmethod
    def draw_area(ax, data_sets, label_sets, is_dark=True):
        colors = ["#7c3aed", "#3b82f6", "#10b981"]
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            ax.fill_between(range(len(vals)), vals, color=colors[i % len(colors)],
                            alpha=0.4, label=label)
            ax.plot(range(len(vals)), vals, color=colors[i % len(colors)], linewidth=1.5)
        BasicCharts._legend(ax, label_sets, data_sets, is_dark)
