"""画图工具包 — generate_chart 入口 + 调度。

chart/ 包结构:
  __init__.py   — Chart 类：注册声明、参数解析、样式、调度
  basic.py      — 基础图表: line/curve/bar/scatter/pie/histogram/area
  advanced.py   — 高级图表: heatmap/radar/bubble/function/regression/wireframe/waveform
  special.py    — 特殊图表: geometry/draw/cat
"""

import logging
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .basic import BasicCharts
from .advanced import AdvancedCharts
from .special import SpecialCharts

logger = logging.getLogger("nano_agent.tools.chart")


class Chart:
    """图表工具入口 — 解析参数、创建画布、分发到具体绘制器、保存图片。"""

    TOOLS = [
        ("generate_chart", "Generate charts, coordinate graphs, geometric proof diagrams, and 3D wireframes. "
         "geometric proofs (勾股定理, Pythagoras, triangle squares) → chart_type='geometry'. "
         "data comparison → bar, trend → line, math → function, 3D → wireframe. "
         "waveform: signal/voltage waveform (信号波形, NOT 交互时序图 — use mermaid_chart sequenceDiagram for that).", "generate_chart",
         {"chart_type": {"type": "string", "description": "geometry: geometric proof. wireframe: 3D. line: trend. bar: comparison. curve: smoothed. scatter: x-y. pie: proportions. function: math formula. regression: fitting. histogram: distribution. area/heatmap/radar/bubble/contour: specialized. contour: 等高线图/梯度下降可视化, data=目标函数表达式(如 X**2+Y**2), labels=迭代轨迹(如 0,5;1,3;2,1). waveform: signal/voltage wave (NOT sequence diagram). Digital data='0,1,0,1;1,0,0,1' labels='CLK;DATA'. Analog data='sine,2,5;square,1,3' labels='CH1;CH2' (type,freq_hz,amplitude)."},
          "data": {"type": "string", "description": "Data: comma-sep values. Multi-series: semicolon-sep. Waveform digital: '0,1,0,1;1,0,0,1' (levels per channel). Waveform analog: 'sine,2,5;square,1,3' (type,freq,amp)."},
          "title": {"type": "string", "description": "Chart title"},
          "labels": {"type": "string", "description": "X-axis labels or series names (semicolon-separated). For bar: 'A,B;Series1;Series2'. For line/scatter with X coords: 'x;Series1'. Shape defs for draw/geometry."},
          "x_label": {"type": "string", "description": "X-axis label"},
          "y_label": {"type": "string", "description": "Y-axis label"},
          "width": {"type": "integer", "description": "Image width (default: 512)"},
          "height": {"type": "integer", "description": "Image height (default: 384)"}},
         []),
    ]

    # chart_type → 绘制器映射
    _BASIC_TYPES = {"line", "curve", "bar", "scatter", "pie", "histogram", "area"}
    _ADVANCED_TYPES = {"heatmap", "radar", "bubble", "function", "regression", "wireframe", "waveform", "contour"}
    _SPECIAL_TYPES = {"geometry", "draw", "cat"}

    def __init__(self, work_dir: str, charts_dir: str = ""):
        self.work_dir = work_dir
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            web_static = Path(__file__).parent.parent.parent.parent / "web" / "static"
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
        **kwargs,
    ) -> str:
        # 解析数据
        try:
            data_sets = self._parse_multi(data) if data else [[]]
            label_sets = self._parse_multi(labels) if labels else []
        except Exception as e:
            return f"Error parsing data: {e}"

        no_data_types = {"draw", "cat", "wireframe", "geometry"}
        if (not data_sets or not data_sets[0]) and chart_type not in no_data_types:
            return "Error: data is required (e.g. '10,20,30,40')"

        # 中文字体
        plt.rcParams["font.sans-serif"] = [
            "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Arial Unicode MS", "PingFang SC", "Heiti SC",
            "Microsoft YaHei", "SimHei", "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        chart_type = chart_type.lower().strip()
        style = style.lower().strip()
        is_dark = style != "light"
        bg = "#1a1a2e" if is_dark else "#ffffff"
        fg = "#e0e0e0" if is_dark else "#333333"
        grid_c = "#333" if is_dark else "#ddd"
        fig, ax = plt.subplots(figsize=(10, 6), facecolor=bg)
        ax.set_facecolor(bg)
        if chart_type == "radar":
            fig.delaxes(ax)
            ax = fig.add_subplot(111, polar=True)
            ax.set_facecolor(bg)

        try:
            self._dispatch(chart_type, fig, ax, data_sets, label_sets,
                           data, labels, is_dark, title, fg, grid_c)
        except Exception as e:
            plt.close(fig)
            return f"Error generating chart: {e}"

        # 通用 2D 样式
        self._apply_style(ax, chart_type, is_dark, fg, grid_c, x_label, y_label, title)

        fig.tight_layout()
        self._cleanup(max_files=50)

        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"chart_{ts}.png"
        filename = Path(filename).name
        if not filename.endswith(".png"):
            filename += ".png"
        filepath = (self.charts_dir / filename).resolve()
        if not str(filepath).startswith(str(self.charts_dir.resolve())):
            return f"Error: path traversal blocked — '{filename}'"

        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=bg)
        plt.close(fig)
        url = f"/charts/{filename}"
        return f"Chart generated: {url}\n![{title}]({url})"

    def _dispatch(self, chart_type, fig, ax, data_sets, label_sets,
                  raw_data, raw_labels, is_dark, title, fg, grid_c):
        """分发到具体的绘制器。"""
        if chart_type in self._BASIC_TYPES:
            method = getattr(BasicCharts, f"draw_{chart_type}")
            method(ax, data_sets, label_sets, is_dark=is_dark)

        elif chart_type in self._ADVANCED_TYPES:
            if chart_type == "wireframe":
                AdvancedCharts.draw_wireframe(fig, raw_data, label_sets, is_dark, title)
            else:
                method = getattr(AdvancedCharts, f"draw_{chart_type}")
                method(ax, data_sets, label_sets, is_dark=is_dark)

        elif chart_type in self._SPECIAL_TYPES:
            if chart_type == "geometry":
                SpecialCharts.draw_geometry(ax, raw_data, label_sets, is_dark)
            elif chart_type in ("draw", "cat"):
                SpecialCharts.draw_shapes(ax, raw_labels, is_dark, is_cat=(chart_type == "cat"))

        else:
            raise ValueError(
                f"Unknown chart type '{chart_type}'. Supported: "
                f"{', '.join(sorted(self._BASIC_TYPES | self._ADVANCED_TYPES | self._SPECIAL_TYPES))}"
            )

    @staticmethod
    def _apply_style(ax, chart_type, is_dark, fg, grid_c, x_label, y_label, title):
        """应用通用坐标轴样式。"""
        if chart_type == "wireframe":
            pass  # 3D 轴样式在各绘制器内设置
        elif chart_type not in ("pie", "radar"):
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
            fig = ax.get_figure()
            fig.suptitle(title, color=fg, fontsize=16, fontweight="bold")

    @staticmethod
    def _parse_multi(s: str) -> list[list]:
        """解析多组数据/标签，分号分隔组，逗号分隔项。"""
        groups = []
        for part in s.split(";"):
            items = [x.strip() for x in part.split(",") if x.strip()]
            groups.append(items)
        return groups

    def _cleanup(self, max_files: int = 50):
        """保留最近 max_files 个 PNG，1 小时内的文件不删。"""
        files = sorted(self.charts_dir.glob("*.png"), key=lambda f: f.stat().st_mtime)
        now = datetime.now().timestamp()
        grace = 3600
        for f in files[:-max_files]:
            if now - f.stat().st_mtime > grace:
                try:
                    f.unlink()
                except OSError:
                    pass
