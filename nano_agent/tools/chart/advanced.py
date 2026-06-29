"""高级图表: heatmap, radar, bubble, function, regression, wireframe, waveform。"""

import logging

import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger("nano_agent.tools.chart.advanced")


class AdvancedCharts:
    """高级图表绘制 — 热力图、雷达、气泡、函数、回归、3D、波形。"""

    @staticmethod
    def _extract_xy(data_sets, label_sets):
        if label_sets and label_sets[0] and label_sets[0][0].strip().lower() == "x":
            x_vals = [float(v) for v in data_sets[0]]
            y_sets = data_sets[1:]
            y_labels = label_sets[1:]
            return x_vals, y_sets, y_labels
        return None, data_sets, label_sets

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
                        color="white" if abs(matrix[i,j]) > matrix.max()/2 else "black",
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
        """数学函数绘图 — data[0][0] 是 Python 表达式，例: x**2, sin(x)。"""
        expr = data_sets[0][0] if data_sets and data_sets[0] else "x"

        _FORBIDDEN = ('__', 'import ', 'exec', 'eval', 'compile', 'open(',
                      'getattr', 'setattr', 'globals', 'locals', 'vars',
                      'dir(', 'type(', 'breakpoint', 'input(', 'class ',
                      'subclass', 'mro', 'builtin', 'system', 'popen',
                      'os.', 'sys.', 'subprocess', '__import__')
        expr_lower = expr.lower()
        for kw in _FORBIDDEN:
            if kw in expr_lower:
                raise ValueError(f"Forbidden pattern '{kw}' in expression")

        x_range = (-5, 5)
        if len(data_sets) > 1 and data_sets[1]:
            x_range = (float(data_sets[1][0]) if data_sets[1] else -5,
                       float(data_sets[1][1]) if len(data_sets[1]) > 1 else 5)
        x = np.linspace(x_range[0], x_range[1], 500)
        ns = {"x": x, "np": np, "sin": np.sin, "cos": np.cos, "tan": np.tan,
              "exp": np.exp, "log": np.log, "sqrt": np.sqrt, "abs": np.abs,
              "pi": np.pi, "e": np.e}

        fg = "#e0e0e0" if is_dark else "#333"

        try:
            y = eval(expr, {"__builtins__": {}}, ns)
        except Exception as e:
            logger.warning(f"Function eval failed for '{expr}': {e}")
            ax.text(0.5, 0.5, f"Error: cannot evaluate '{expr}'\n{e}",
                    transform=ax.transAxes, ha="center", color=fg, fontsize=10)
            return

        ax.plot(x, y, color="#7c3aed", linewidth=2)
        ax.axhline(y=0, color=fg, linewidth=0.5, alpha=0.5)
        ax.axvline(x=0, color=fg, linewidth=0.5, alpha=0.5)

        is_trig = any(fn in expr_lower for fn in ('sin', 'cos', 'tan'))
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

        ax.set_title(f"y = {expr}", color=fg, fontsize=14, fontweight="bold")
        ax.set_xlabel("x", color=fg)
        ax.set_ylabel("y", color=fg)

    @staticmethod
    def draw_regression(ax, data_sets, label_sets, is_dark=True):
        """最小二乘回归 — 散点 + 拟合直线 + 方程 + R²。"""
        fg = "#e0e0e0" if is_dark else "#333"
        groups = [ds for ds in data_sets if ds]
        if not groups:
            ax.text(0.5, 0.5, "Need ≥2 data points for regression", transform=ax.transAxes,
                    ha="center", color=fg)
            return

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

    @staticmethod
    def draw_wireframe(fig, data, label_sets, is_dark, title):
        """3D 线框模型 — 立方体、锥体、球体网格等。"""
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

        fg = "#e0e0e0" if is_dark else "#333"
        bg = "#1a1a2e" if is_dark else "#fafafa"

        ax = fig.add_subplot(111, projection='3d')
        ax.set_facecolor(bg)
        fig.patch.set_facecolor(bg)

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
                x1, y1, z1 = float(parts[0]), float(parts[1]), float(parts[2])
                x2, y2, z2 = float(parts[3]), float(parts[4]), float(parts[5])
                ax.plot([x1, x2], [y1, y2], [z1, z2],
                        color="#7c3aed", linewidth=2, alpha=0.9)

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

    @staticmethod
    def draw_waveform(ax, data_sets, label_sets, is_dark=True):
        """波形图 — 数字时序波形 & 模拟波形。"""
        fg = "#e0e0e0" if is_dark else "#333"
        bg = "#1a1a2e" if is_dark else "#ffffff"
        grid_c = "#333333" if is_dark else "#dddddd"
        colors = ["#7c3aed", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#ec4899"]

        wave_types = {"sine", "square", "triangle", "sawtooth", "sin", "cos"}
        is_analog = False
        if data_sets and data_sets[0]:
            first_val = str(data_sets[0][0]).strip().lower()
            if first_val in wave_types:
                is_analog = True

        if is_analog:
            t_max = 0
            for ds in data_sets:
                if len(ds) < 3:
                    continue
                freq = float(ds[1]) if len(ds) > 1 else 1.0
                period = 1.0 / freq if freq > 0 else 1.0
                t_max = max(t_max, period * 3)

            t = np.linspace(0, t_max, 1000)
            for i, ds in enumerate(data_sets):
                if len(ds) < 3:
                    continue
                wtype = str(ds[0]).strip().lower()
                freq = float(ds[1]) if len(ds) > 1 else 1.0
                amp = float(ds[2]) if len(ds) > 2 else 1.0
                phase_deg = float(ds[3]) if len(ds) > 3 else 0.0
                phase_rad = np.radians(phase_deg)
                omega = 2 * np.pi * freq

                color = colors[i % len(colors)]

                if i < len(label_sets) and label_sets[i]:
                    label = label_sets[i][0]
                elif wtype in ("sine", "sin"):
                    label = f"{freq}Hz sine, A={amp}"
                elif wtype == "cos":
                    label = f"{freq}Hz cos, A={amp}"
                else:
                    label = f"{freq}Hz {wtype}, A={amp}"

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

                ax.plot(t, y, color=color, linewidth=2, label=label)

            ax.set_xlim(0, t_max)
            ax.set_ylim(-1.5, 1.5)
            ax.axhline(y=0, color=fg, linewidth=0.5, alpha=0.3)
            ax.set_xlabel("Time (s)", color=fg)
            ax.set_ylabel("Amplitude", color=fg)
        else:
            n_channels = len(data_sets)
            if n_channels == 0:
                ax.text(0.5, 0.5, "No waveform data", transform=ax.transAxes,
                        ha="center", color=fg)
                return

            channel_height = 1.0
            channel_gap = 0.4
            total_height = n_channels * (channel_height + channel_gap)

            for ch_idx, ds in enumerate(data_sets):
                levels = []
                for v in ds:
                    try:
                        levels.append(int(float(v)))
                    except ValueError:
                        levels.append(0)

                if not levels:
                    continue

                base_y = total_height - ch_idx * (channel_height + channel_gap)
                ch_name = f"CH{ch_idx+1}"
                if ch_idx < len(label_sets) and label_sets[ch_idx]:
                    ch_name = label_sets[ch_idx][0]

                color = colors[ch_idx % len(colors)]

                n = len(levels)
                x_step, y_step = [], []
                for i, lv in enumerate(levels):
                    y_val = base_y + (channel_height * 0.6 if lv else 0)
                    if i == 0:
                        x_step.append(0)
                        y_step.append(y_val)
                    else:
                        x_step.append(i)
                        prev_lv = levels[i-1]
                        prev_y = base_y + (channel_height * 0.6 if prev_lv else 0)
                        y_step.append(prev_y)
                        x_step.append(i)
                        y_step.append(y_val)
                    if i == n - 1:
                        x_step.append(i + 1)
                        y_step.append(y_val)

                ax.plot(x_step, y_step, color=color, linewidth=2, solid_joinstyle="miter")

                for i, lv in enumerate(levels):
                    if lv:
                        ax.fill_between([i, i+1], base_y,
                                        base_y + channel_height * 0.6,
                                        color=color, alpha=0.15)

                ax.text(-0.5, base_y + channel_height * 0.3, ch_name,
                        color=color, fontsize=11, fontweight="bold",
                        ha="right", va="center")

                for i, lv in enumerate(levels):
                    y_val = base_y + (channel_height * 0.6 if lv else 0)
                    ax.text(i + 0.5, y_val + 0.05, str(lv),
                            color=color, fontsize=8, ha="center", alpha=0.7)

            ax.set_xlim(-1, max(len(ds) for ds in data_sets) + 1)
            ax.set_ylim(-0.3, total_height + 0.5)
            ax.set_xlabel("Clock cycle", color=fg)
            ax.set_yticks([])

        ax.tick_params(colors=fg)
        for spine in ["bottom", "left"]:
            ax.spines[spine].set_color(grid_c)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, axis="x", alpha=0.15, color=grid_c)
