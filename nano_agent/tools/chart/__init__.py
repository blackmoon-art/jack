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
        ("generate_chart", "Generate charts, coordinate graphs, geometric proof diagrams, and 3D plots. "
         "geometric proofs (勾股定理, Pythagoras, triangle squares) → chart_type='geometry'. "
         "data comparison → bar, trend → line, math → function. "
         "3D: wireframe=线框, surface=实体曲面(带光照+颜色映射). surface type supports formulas: data='sin(sqrt(X**2+Y**2))' "
         "and predefined shapes: sphere, torus, saddle, paraboloid, cone, cylinder, heart, mobius, ripple, hyperboloid, helix. "
         "waveform: signal/voltage wave (NOT 交互时序图 — use mermaid_chart sequenceDiagram for that). "
         "spectrum: FFT frequency spectrum (傅立叶变换/频谱分析).", "generate_chart",
         {"chart_type": {"type": "string", "description": (
             "Chart type (one of 20). Each with data format:\n"
             "line: y1,y2;y3,y4 labels='x;S1;S2' | "
             "curve: same as line, smoothed | "
             "bar: v1,v2,v3 labels='CatA,CatB' | "
             "scatter: x1,x2;y1,y2 labels='X;Y' | "
             "pie: v1,v2,v3 labels='A,B,C' | "
             "histogram: raw_values labels='bins' | "
             "area: same as line, filled | "
             "heatmap: r1c1,r1c2;r2c1,r2c2 labels='rA,rB;cX,cY' | "
             "radar: v1,v2,v3 labels='Axis1,Axis2,Axis3' | "
             "bubble: x;y;size labels='A,B,C' (3 series: x,y,bubble_radii) | "
             "function: formula labels='x1,x2' e.g. data='sin(x)',labels='-5,5' | "
             "regression: x1,x2;y1,y2 (paired rows) or x;y (2 series). Auto-fits line+R² | "
             "contour: formula labels='traj' e.g. data='X**2+Y**2',labels='0,5;1,3' | "
             "wireframe: shape|formula|edges labels='x1,x2;y1,y2' e.g. data='torus' | "
             "surface: shape|formula labels='x1,x2;y1,y2' e.g. data='heart' | "
             "waveform: analog='sine,2,5;square,6,3' (type,freq,amp) or digital='0,1,0;1,0,1'. labels='CH1;CH2' | "
             "spectrum: same format as waveform. labels='sample_rate;CH1' e.g. data='square,5,3',labels='1000' | "
             "geometry: proof_type labels='params' e.g. data='Pythagoras',labels='3,4' | "
             "draw: shape_defs labels='notes' e.g. data='circle(0,0,1)' | "
             "cat: style labels='pose' e.g. data='simple'"
         )},
          "data": {"type": "string", "description": (
              "Values: comma-sep within series, semicolon-sep between series. "
              "3D surface/wireframe: formula (X**2+Y**2) or shape name (sphere,torus,heart...). "
              "Waveform/spectrum: analog='sine,2,5;square,6,3' (type,freq_hz,amp) or digital='0,1,0;1,0,1'. "
              "Regression: 'x1,y1;x2,y2' (paired) or 'x1,x2;y1,y2' (2-series)."
          )},
          "title": {"type": "string", "description": "Chart title"},
          "labels": {"type": "string", "description": (
              "Semicolon-sep groups. For bar/pie: 'CatA,CatB'. Multi-series: 'x;Series1;Series2'. "
              "For 3D: 'x_min,x_max;y_min,y_max'. For spectrum: 'sample_rate'. "
              "For contour: trajectory 'x1,y1;x2,y2'."
          )},
          "x_label": {"type": "string", "description": "X-axis label"},
          "y_label": {"type": "string", "description": "Y-axis label"},
          "width": {"type": "integer", "description": "Image width (default: 512)"},
          "height": {"type": "integer", "description": "Image height (default: 384)"}},
         []),
    ]

    # chart_type → 绘制器映射
    _BASIC_TYPES = {"line", "curve", "bar", "scatter", "pie", "histogram", "area"}
    _ADVANCED_TYPES = {"heatmap", "radar", "bubble", "function", "regression",
                       "wireframe", "surface", "waveform", "spectrum", "contour"}
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

        no_data_types = {"draw", "cat", "wireframe", "surface", "geometry", "contour", "regression", "function"}
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

        try:
            fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=bg)
        finally:
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
            elif chart_type == "surface":
                AdvancedCharts.draw_surface(fig, raw_data, label_sets, is_dark, title)
            elif chart_type == "spectrum":
                AdvancedCharts.draw_spectrum(ax, data_sets, label_sets, is_dark=is_dark)
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
        if chart_type in ("wireframe", "surface"):
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
        """保留最近 max_files 个 PNG，1 小时内的不删。清理所有工具生成的图片。

        节流：两次清理间隔至少 60 秒，避免图表批量生成时重复扫描文件系统。
        """
        now = datetime.now().timestamp()
        if now - getattr(self, '_last_cleanup_ts', 0) < 60:
            return
        self._last_cleanup_ts = now

        all_pngs = []
        for pattern in ("chart_*.png", "mermaid_*.png", "circuit_*.png",
                        "ai_*.png", "plantuml_*.png"):
            all_pngs.extend(self.charts_dir.glob(pattern))
        files = sorted(all_pngs, key=lambda f: f.stat().st_mtime)
        now = datetime.now().timestamp()
        grace = 3600
        for f in files[:-max_files]:
            if now - f.stat().st_mtime > grace:
                try:
                    f.unlink()
                except OSError:
                    pass
