"""画图工具：generate_chart — 用 matplotlib 生成各类图表。

支持:
  - 折线图 (line)
  - 平滑曲线 (curve)
  - 柱状图 (bar)
  - 散点图 (scatter)
  - 饼图 (pie)
  - 直方图 (histogram)
  - 面积图 (area)

图片保存到 web/static/charts/ 目录，前端可直接访问。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.chart")


class Chart:
    def __init__(self, work_dir: str):
        self.work_dir = work_dir
        # 图片保存到 web/static/charts/ 以便前端直接访问
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
    ) -> str:
        """
        生成图表并保存为 PNG 图片。

        Args:
            chart_type: 图表类型 (line, bar, scatter, pie, histogram, area)
            title: 图表标题
            data: 数据，逗号分隔的数值 (如 "10,20,30,40")；多组用分号分隔 (如 "10,20;30,40")
            labels: 标签，逗号分隔 (如 "A,B,C,D")；多组用分号分隔
            x_label: X 轴标签
            y_label: Y 轴标签
            filename: 文件名 (可选，默认自动生成)
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        # 解析数据
        try:
            data_sets = self._parse_multi(data)
            label_sets = self._parse_multi(labels) if labels else []
        except Exception as e:
            return f"Error parsing data: {e}"

        if not data_sets or not data_sets[0]:
            return "Error: data is required (e.g. '10,20,30,40')"

        # 设置中文字体
        plt.rcParams["font.sans-serif"] = [
            "Arial Unicode MS", "PingFang SC", "Heiti SC",
            "Microsoft YaHei", "SimHei", "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        # 生成图表
        chart_type = chart_type.lower().strip()
        fig, ax = plt.subplots(figsize=(10, 6), facecolor="#1a1a2e")
        ax.set_facecolor("#1a1a2e")

        try:
            if chart_type == "line":
                self._draw_line(ax, data_sets, label_sets)
            elif chart_type == "curve":
                self._draw_curve(ax, data_sets, label_sets)
            elif chart_type == "bar":
                self._draw_bar(ax, data_sets, label_sets)
            elif chart_type == "scatter":
                self._draw_scatter(ax, data_sets, label_sets)
            elif chart_type == "pie":
                self._draw_pie(ax, data_sets, label_sets)
            elif chart_type == "histogram":
                self._draw_histogram(ax, data_sets, label_sets)
            elif chart_type == "area":
                self._draw_area(ax, data_sets, label_sets)
            else:
                return f"Error: Unknown chart type '{chart_type}'. Supported: line, curve, bar, scatter, pie, histogram, area"
        except Exception as e:
            plt.close(fig)
            return f"Error generating chart: {e}"

        # 样式
        if chart_type != "pie":
            ax.tick_params(colors="#ccc")
            ax.spines["bottom"].set_color("#444")
            ax.spines["left"].set_color("#444")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            if x_label:
                ax.set_xlabel(x_label, color="#ccc", fontsize=12)
            if y_label:
                ax.set_ylabel(y_label, color="#ccc", fontsize=12)
            ax.grid(True, alpha=0.2, color="#666")

        if title:
            fig.suptitle(title, color="#e0e0e0", fontsize=16, fontweight="bold")

        fig.tight_layout()

        # 清理旧文件（保留最近 50 个）
        self._cleanup(max_files=50)

        # 保存
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"chart_{ts}.png"
        if not filename.endswith(".png"):
            filename += ".png"
        filepath = self.charts_dir / filename

        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
        plt.close(fig)

        # 返回前端可访问的 URL
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

    def _draw_line(self, ax, data_sets, label_sets):
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            x = range(len(vals))
            ax.plot(x, vals, color=colors[i % len(colors)], marker="o",
                    linewidth=2, markersize=4, label=label)
        if any(i < len(label_sets) and label_sets[i] for i in range(len(data_sets))):
            ax.legend(facecolor="#222", edgecolor="#444", labelcolor="#ccc")

    def _draw_curve(self, ax, data_sets, label_sets):
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
            ax.legend(facecolor="#222", edgecolor="#444", labelcolor="#ccc")

    def _draw_bar(self, ax, data_sets, label_sets):
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
            ax.set_xticklabels(x_labels, color="#ccc")
        if n > 1:
            ax.legend(facecolor="#222", edgecolor="#444", labelcolor="#ccc")

    def _draw_scatter(self, ax, data_sets, label_sets):
        colors = ["#7c3aed", "#3b82f6", "#10b981"]
        # 需要 x,y 对：第一组=x，第二组=y
        if len(data_sets) < 2:
            vals = [float(x) for x in data_sets[0]]
            ax.scatter(range(len(vals)), vals, color=colors[0], s=60, alpha=0.8)
        else:
            xs = [float(x) for x in data_sets[0]]
            ys = [float(x) for x in data_sets[1]]
            ax.scatter(xs, ys, color=colors[0], s=60, alpha=0.8)

    def _draw_pie(self, ax, data_sets, label_sets):
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
            ax.legend(facecolor="#222", edgecolor="#444", labelcolor="#ccc")

    def _draw_area(self, ax, data_sets, label_sets):
        colors = ["#7c3aed", "#3b82f6", "#10b981"]
        for i, ds in enumerate(data_sets):
            vals = [float(x) for x in ds]
            label = label_sets[i][0] if i < len(label_sets) and label_sets[i] else None
            ax.fill_between(range(len(vals)), vals, color=colors[i % len(colors)],
                            alpha=0.4, label=label)
            ax.plot(range(len(vals)), vals, color=colors[i % len(colors)], linewidth=1.5)
        if any(i < len(label_sets) and label_sets[i] for i in range(len(data_sets))):
            ax.legend(facecolor="#222", edgecolor="#444", labelcolor="#ccc")

    # ── 清理 ──

    def _cleanup(self, max_files: int = 50):
        """保留最近 max_files 个图表文件。"""
        files = sorted(self.charts_dir.glob("*.png"), key=lambda f: f.stat().st_mtime)
        for f in files[:-max_files]:
            try:
                f.unlink()
            except OSError:
                pass
