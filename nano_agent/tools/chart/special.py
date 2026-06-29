"""特殊图表: geometry, draw, cat (简笔画/几何证明)。"""

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon


class SpecialCharts:
    """特殊图表 — 几何证明图、形状绘制、简笔猫。"""

    @staticmethod
    def draw_geometry(ax, data, label_sets, is_dark):
        """2D 几何证明图 — 勾股定理、三角形、多边形等。"""
        fg = "#e0e0e0" if is_dark else "#333"
        bg = "#1a1a2e" if is_dark else "#fafafa"
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]

        ax.set_facecolor(bg)
        ax.set_aspect('equal')
        ax.grid(True, alpha=0.15, color=fg)
        ax.tick_params(colors=fg, labelsize=9)

        shapes_data = data if data else "0,0;3,0;0,4"
        shapes = [s.strip() for s in shapes_data.split(";") if s.strip()]

        if len(shapes) <= 3:
            # 从 labels 推断几何类型，支持勾股定理、相似三角形、全等三角形等
            label_text = label_sets[0][0] if label_sets and label_sets[0] else ""
            label_lower = label_text.lower()

            if any(kw in label_lower for kw in ("pythagoras", "勾股", "pythagorean")):
                # 勾股定理：3-4-5 直角三角形 + 三个正方形
                tri = [(0, 0), (3, 0), (0, 4)]
                tri_arr = np.array(tri)
                tri_patch = Polygon(tri_arr, closed=True, facecolor="none",
                                   edgecolor=colors[0], linewidth=2.5, alpha=0.9)
                ax.add_patch(tri_patch)
                lbl = ["A", "B", "C"]
                for i, (x, y) in enumerate(tri):
                    ax.text(x - 0.1, y - 0.3, lbl[i], color=fg, fontsize=12, fontweight="bold")
                ax.text(1.5, -0.3, "a = 3", color=colors[1], fontsize=11, ha="center")
                ax.text(-0.6, 2.0, "b = 4", color=colors[2], fontsize=11, ha="center")
                ax.text(1.8, 2.2, "c = 5", color="#f59e0b", fontsize=11, ha="center")
                for sq_pts, sq_color, sq_label, sq_pos in [
                    ([(0,0),(3,0),(3,-3),(0,-3)], colors[1], "a² = 9", (1.5,-1.5)),
                    ([(0,0),(-4,0),(-4,4),(0,4)], colors[2], "b² = 16", (-2,2)),
                    ([(0,4),(3,0),(8,3),(5,7)], "#f59e0b", "c² = 25", (4,3.7)),
                ]:
                    ax.add_patch(Polygon(sq_pts, closed=True, facecolor=sq_color, alpha=0.2,
                                        edgecolor=sq_color, linewidth=2))
                    ax.text(sq_pos[0], sq_pos[1], sq_label, color=sq_color, fontsize=12, ha="center", fontweight="bold")
                ax.text(1.5, 5.5, "a² + b² = c²", color=fg, fontsize=15, ha="center", fontweight="bold")
                ax.text(1.5, 4.8, "9 + 16 = 25 ✓", color="#10b981", fontsize=13, ha="center", fontweight="bold")
                margin = 6
                ax.set_xlim(-margin, margin + 3)
                ax.set_ylim(-margin, margin + 3)
            elif any(kw in label_lower for kw in ("similar", "相似", "triangle", "三角")):
                # 相似/全等三角形：从 data 解析顶点
                tri1 = [(0, 0), (3, 0), (1.5, 3)]
                tri2 = [(x + 5, y) for x, y in tri1]
                for tri_pts, tri_color in [(tri1, colors[0]), (tri2, colors[1])]:
                    ax.add_patch(Polygon(np.array(tri_pts), closed=True, facecolor="none",
                                        edgecolor=tri_color, linewidth=2.5, alpha=0.9))
                scale = 0.6 if "similar" in label_lower else 1.0
                mid1 = (sum(p[0] for p in tri1)/3, sum(p[1] for p in tri1)/3)
                mid2 = (sum(p[0] for p in tri2)/3, sum(p[1] for p in tri2)/3)
                ax.text(mid1[0], mid1[1], "△ABC", color=fg, fontsize=12, ha="center", fontweight="bold")
                ax.text(mid2[0], mid2[1], "△DEF", color=fg, fontsize=12, ha="center", fontweight="bold")
                relation = "相似" if "similar" in label_lower else "全等"
                ax.text(3.5, -1, f"△ABC ~ △DEF ({relation})", color=fg, fontsize=14, ha="center", fontweight="bold")
                ax.set_xlim(-2, 9)
                ax.set_ylim(-2, 5)
            else:
                # 通用几何: 从 labels 读取顶点坐标描点
                ax.text(0, 0, label_text or "几何图形 (在labels中添加描述)",
                       color=fg, fontsize=14, ha="center")
                ax.set_xlim(-5, 5)
                ax.set_ylim(-5, 5)
        else:
            for i, shape in enumerate(shapes):
                pts_str = [p.strip() for p in shape.split(",")]
                if len(pts_str) < 2:
                    continue
                pts = [(float(pts_str[j]), float(pts_str[j+1]))
                       for j in range(0, len(pts_str) - 1, 2)]
                if len(pts) < 2:
                    continue
                color = colors[i % len(colors)]
                pts_arr = np.array(pts)
                ax.add_patch(Polygon(pts_arr, closed=False, facecolor="none",
                                     edgecolor=color, linewidth=2, alpha=0.8))
                for j, (x, y) in enumerate(pts):
                    ax.plot(x, y, 'o', color=color, markersize=4)
                    ax.text(x + 0.1, y + 0.1, f"{j+1}", color=fg, fontsize=8)

            ax.autoscale_view()

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color(fg + "44")
        ax.spines["left"].set_color(fg + "44")

    @staticmethod
    def draw_shapes(ax, labels, is_dark=True, is_cat=False):
        """绘制形状。labels 格式: 'circle:0,0,3,red;rect:1,1,2,1,blue'。

        如果 labels 为空且非 cat 模式，raise ValueError（调用方应处理）。"""
        fg = "#e0e0e0" if is_dark else "#222"

        if not labels:
            if is_cat:
                SpecialCharts._draw_cat(ax, is_dark)
                return
            raise ValueError("labels required for draw/geometry. Format: 'circle:x,y,r,color;rect:x,y,w,h,color'")

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

    @staticmethod
    def _draw_cat(ax, is_dark=True):
        """画一只简笔猫。"""
        fg = "#e0e0e0" if is_dark else "#222"
        accent = "#7c3aed"

        head = plt.Circle((0, 0), 2, fill=False, edgecolor=fg, linewidth=2.5)
        ax.add_patch(head)

        ax.plot([-1.6, -1.0, -1.8], [1.4, 2.5, 2.5], color=fg, linewidth=2)
        ax.fill([-1.6, -1.0, -1.8], [1.4, 2.5, 2.5], color=accent, alpha=0.3)
        ax.plot([1.6, 1.0, 1.8], [1.4, 2.5, 2.5], color=fg, linewidth=2)
        ax.fill([1.6, 1.0, 1.8], [1.4, 2.5, 2.5], color=accent, alpha=0.3)

        left_eye = plt.Circle((-0.7, 0.5), 0.3, fill=True, facecolor=accent, edgecolor=fg, linewidth=1.5)
        right_eye = plt.Circle((0.7, 0.5), 0.3, fill=True, facecolor=accent, edgecolor=fg, linewidth=1.5)
        ax.add_patch(left_eye)
        ax.add_patch(right_eye)
        ax.plot(-0.7, 0.5, "o", color=fg, markersize=4)
        ax.plot(0.7, 0.5, "o", color=fg, markersize=4)

        ax.plot([0, -0.15, 0.15, 0], [0.0, -0.2, -0.2, 0.0], color="#f59e0b", linewidth=2)

        theta = np.linspace(0, np.pi, 30)
        ax.plot(0.3 * np.cos(theta) - 0.3, -0.3 * np.sin(theta) - 0.3, color=fg, linewidth=1.5)
        ax.plot(0.3 * np.cos(theta) + 0.3, -0.3 * np.sin(theta) - 0.3, color=fg, linewidth=1.5)

        for dy in [-0.15, -0.35, -0.55]:
            ax.plot([-0.5, -2.0], [dy, dy - 0.1], color=fg, linewidth=1, alpha=0.7)
            ax.plot([0.5, 2.0], [dy, dy - 0.1], color=fg, linewidth=1, alpha=0.7)

        ax.set_xlim(-3.5, 3.5)
        ax.set_ylim(-3.5, 3.5)
        ax.set_aspect("equal")
        ax.axis("off")
