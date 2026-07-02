"""模拟电路 SVG 渲染器 — SPICE netlist 输入。

SPICE 格式:
  R1 1 2 1k        ; 电阻: R<name> <n1> <n2> <value>
  C1 2 0 10n       ; 电容: C<name> <n1> <n2> <value>
  L1 1 2 1m        ; 电感
  D1 1 2            ; 二极管
  V1 1 0 AC 1      ; 电压源: V<name> <n1> <n2> AC <value>
  I1 1 2 1m        ; 电流源
  Q1 C B E npn     ; NPN 晶体管
  Q1 C B E pnp     ; PNP 晶体管
  X1 in+ in- out vcc 0 opamp  ; 运放 (5 节点)
  .end

节点 0 = GND。一行一个元件，忽略 .op/.end/.title 等控制行。
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


class AnalogSVG:
    TOOLS = [
        ("draw_analog_svg",
         "Draw analog circuit diagrams as SVG from SPICE netlist.\n"
         "\n"
         "**SPICE format, one component per line:**\n"
         "`R1 1 2 1k` — resistor between node 1 and 2\n"
         "`C1 2 0 10n` — capacitor, node 0 = GND\n"
         "`L1 1 2 1m` — inductor\n"
         "`D1 1 2` — diode\n"
         "`V1 1 0 AC 1` — AC voltage source\n"
         "`Q1 C B E npn` — NPN transistor\n"
         "`X1 in+ in- out vcc 0 opamp` — op-amp\n"
         "\n"
         "Ignore .op/.end lines. Node 0 is always ground.\n"
         "\n"
         "**RC low-pass:**\n"
         "`V1 1 0 AC 1\nR1 1 2 1k\nC1 2 0 10n`\n"
         "\n"
         "**Sallen-Key low-pass:**\n"
         "`V1 1 0 AC 1\nR1 1 2 10k\nR2 2 3 10k\n"
         "C1 2 4 10n\nC2 3 0 10n\n"
         "X1 3 4 4 5 0 opamp`",
         "draw_analog_svg",
         {"description": {"type": "string",
                          "description":
                          "SPICE netlist. One component per line. "
                          "R/C/L/D/V/I/Q for resistor/capacitor/inductor/diode/source/transistor. "
                          "X for opamp. Node 0 = GND. "
                          "Example: 'V1 1 0 AC 1\\nR1 1 2 1k\\nC1 2 0 10n' for RC low-pass"},
          "title": {"type": "string", "description": "Circuit title"}},
         ["description"]),
    ]

    # SVG 常量
    COL_GAP, ROW_GAP = 110, 70
    COLORS = {"bg": "#1a1a2e", "fg": "#e0e0e0", "stroke": "#7c3aed",
              "fill": "#2a2a4e", "text": "#e0e0e0", "gnd": "#3b82f6"}

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = Path(__file__).parent.parent.parent / "web" / "static" / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def draw_analog_svg(self, description: str, title: str = "") -> str:
        try:
            svg = self._render(description, title)
        except Exception as e:
            return f"Error drawing analog circuit: {e}"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"
        return f"![{title or 'Circuit'}]({url})\n{url}"

    # ═══════════ SPICE 解析 ═══════════

    @staticmethod
    def _parse_spice(desc: str) -> list:
        """解析 SPICE netlist → [{type, name, nodes: [n1,n2,...], value}]"""
        comps = []
        for line in desc.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith((".", "*", "#")):
                continue
            parts = line.split()
            if not parts:
                continue
            prefix = parts[0][0].upper()
            name = parts[0]
            rest = parts[1:]

            if prefix == "R":
                comps.append({"type": "resistor", "name": name,
                               "nodes": rest[:2], "value": rest[2] if len(rest) > 2 else ""})
            elif prefix == "C":
                comps.append({"type": "capacitor", "name": name,
                               "nodes": rest[:2], "value": rest[2] if len(rest) > 2 else ""})
            elif prefix == "L":
                comps.append({"type": "inductor", "name": name,
                               "nodes": rest[:2], "value": rest[2] if len(rest) > 2 else ""})
            elif prefix == "D":
                comps.append({"type": "diode", "name": name,
                               "nodes": rest[:2], "value": rest[2] if len(rest) > 2 else ""})
            elif prefix == "V":
                vtype = rest[2] if len(rest) > 2 else "DC"
                comps.append({"type": "vsource", "name": name,
                               "nodes": rest[:2], "value": rest[3] if len(rest) > 3 else rest[2] if vtype == "DC" else ""})
            elif prefix == "I":
                comps.append({"type": "isource", "name": name,
                               "nodes": rest[:2], "value": rest[2] if len(rest) > 2 else ""})
            elif prefix == "Q":
                model = rest[3] if len(rest) > 3 else "npn"
                comps.append({"type": model, "name": name,
                               "nodes": rest[:3], "value": ""})
            elif prefix == "X":
                comps.append({"type": "opamp", "name": name,
                               "nodes": rest[:5] if len(rest) >= 5 else rest, "value": ""})
        return comps

    # ═══════════ 渲染 ═══════════

    def _render(self, description: str, title: str = "") -> str:
        comps = self._parse_spice(description)
        if not comps:
            return "Error: no valid SPICE components found"

        # 构建连接图，分配节点位置
        nodes, layout, edges, svg_w, svg_h = self._layout(comps)

        svg = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {svg_w} {svg_h}",
            "width": str(svg_w), "height": str(svg_h),
        })
        ET.SubElement(svg, "rect", {
            "width": str(svg_w), "height": str(svg_h), "fill": self.COLORS["bg"],
        })
        if title:
            ET.SubElement(svg, "text", {
                "x": str(svg_w // 2), "y": "20", "text-anchor": "middle",
                "fill": self.COLORS["text"], "font-family": "monospace",
                "font-size": "13", "font-weight": "bold",
            }).text = title

        # 先画线，再画元件（元件覆盖线）
        for a, b in edges:
            if a in nodes and b in nodes:
                self._draw_wire(svg, nodes[a][0], nodes[a][1],
                                nodes[b][0], nodes[b][1])

        # 画元件
        for comp in comps:
            if comp["name"] in layout:
                x, y, orient = layout[comp["name"]]
                self._draw_component(svg, comp, x, y, orient)

        # 画地符号（节点 0）
        if 0 in nodes:
            gx, gy = nodes[0]
            self._draw_ground(svg, gx, gy)

        return ET.tostring(svg, encoding="unicode")

    # ═══════════ 布局 ═══════════

    def _layout(self, comps):
        """网格布局：每个元件独立位置，通过节点名连接。

        不需要拓扑分析——SPICE netlist 本身不包含布局信息。
        元件按顺序排在网格中，相同节点名的引脚用线连接。
        """
        COLS = 3  # 每行最多 3 个元件
        nodes = {}    # node_id → (x, y)
        layout = {}   # comp_name → (x, y, orient)
        edges = []    # [(node_a, node_b)]
        node_edges = set()

        # 逐元件分配位置
        for i, c in enumerate(comps):
            col = i % COLS
            row = i // COLS
            cx = 120 + col * self.COL_GAP
            cy = 80 + row * self.ROW_GAP
            layout[c["name"]] = (cx, cy, "h")

            # 记录该元件连接的节点
            nids = [self._node_id(n) for n in c["nodes"]]
            for nid in nids:
                if nid not in nodes:
                    # 新节点：放在元件附近
                    if nid == 0:
                        nodes[nid] = (cx, cy + 30)
                    else:
                        nodes[nid] = (cx, cy)
            for i2 in range(len(nids)):
                for j2 in range(i2 + 1, len(nids)):
                    edge = tuple(sorted([nids[i2], nids[j2]]))
                    node_edges.add(edge)

        # 节点 0 放底部
        if 0 in nodes:
            max_y = max(y for _, y, _ in layout.values()) if layout else 100
            nodes[0] = (nodes[0][0], max_y + 80)

        edges = list(node_edges)
        svg_w = (COLS + 0) * self.COL_GAP + 60
        max_r = (len(comps) - 1) // COLS if comps else 0
        svg_h = (max_r + 2) * self.ROW_GAP + 60
        return nodes, layout, edges, max(200, svg_w), max(100, svg_h)

    @staticmethod
    def _node_id(n):
        try:
            return int(n)
        except (ValueError, TypeError):
            return hash(str(n)) % 100000

    # ═══════════ 元件绘制 ═══════════

    def _draw_component(self, svg, comp, x, y, orient):
        t = comp["type"]
        v = comp.get("value", "")
        draw_fn = getattr(self, f"_draw_{t}", None)
        if draw_fn:
            draw_fn(svg, x, y, v, orient)

    def _draw_resistor(self, svg, x, y, v, orient):
        W, H = 60, 20
        n = 5
        seg_w = W / (n + 1)
        if orient == "v":
            pts = [(x, y - W // 2)]
            for i in range(1, n + 1):
                pts.append((x + (seg_w * 0.6 if i % 2 == 1 else -seg_w * 0.6),
                            y - W // 2 + i * seg_w))
            pts.append((x, y + W // 2))
        else:
            pts = [(x - W // 2, y)]
            for i in range(1, n + 1):
                pts.append((x - W // 2 + i * seg_w,
                            y + (seg_w * 0.6 if i % 2 == 1 else -seg_w * 0.6)))
            pts.append((x + W // 2, y))
        d = "M" + " L".join(f"{px},{py}" for px, py in pts)
        self._path(svg, d)
        self._label(svg, x, y + 22, v)

    def _draw_capacitor(self, svg, x, y, v, orient):
        L, G = 30, 8
        if orient == "v":
            self._line(svg, x, y - L, x, y - G)
            self._line(svg, x - 10, y - G, x + 10, y - G)
            self._line(svg, x - 10, y + G, x + 10, y + G)
            self._line(svg, x, y + G, x, y + L)
        else:
            self._line(svg, x - L, y, x - G, y)
            self._line(svg, x - G, y - 10, x - G, y + 10)
            self._line(svg, x + G, y - 10, x + G, y + 10)
            self._line(svg, x + G, y, x + L, y)
        self._label(svg, x, y + 22, v)

    def _draw_inductor(self, svg, x, y, v, orient):
        L, R, N = 40, 5, 4
        if orient == "v":
            d = f"M{x},{y - L // 2}"
            for i in range(N):
                sweep = 1 if i % 2 == 0 else 0
                d += f" A{R},{R} 0 0,{sweep} {x},{y - L // 2 + (i+1)*L//N}"
            self._path(svg, d)
        else:
            d = f"M{x - L // 2},{y}"
            for i in range(N):
                sweep = 1 if i % 2 == 0 else 0
                d += f" A{R},{R} 0 0,{sweep} {x - L // 2 + (i+1)*L//N},{y}"
            self._path(svg, d)
        self._label(svg, x, y + 22, v)

    def _draw_diode(self, svg, x, y, v, orient):
        S = 10
        if orient == "v":
            d = f"M{x - S},{y - S} L{x},{y} L{x + S},{y - S} Z"
        else:
            d = f"M{x - S},{y - S} L{x},{y} L{x - S},{y + S} Z"
        self._path(svg, d, fill=True)
        if orient == "v":
            self._line(svg, x - S, y + S, x + S, y + S)
        else:
            self._line(svg, x + S, y - S, x + S, y + S)
        self._label(svg, x, y + 22, v)

    def _draw_vsource(self, svg, x, y, v, orient):
        R = 14
        ET.SubElement(svg, "circle", {
            "cx": str(x), "cy": str(y), "r": str(R),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        ET.SubElement(svg, "text", {
            "x": str(x), "y": str(y + 4), "text-anchor": "middle",
            "fill": self.COLORS["text"], "font-family": "monospace", "font-size": "10",
        }).text = "+"
        self._label(svg, x, y + 28, v)

    _draw_isource = _draw_vsource

    def _draw_npn(self, svg, x, y, v, orient):
        R = 14
        ET.SubElement(svg, "circle", {
            "cx": str(x), "cy": str(y), "r": str(R),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        self._line(svg, x, y - R, x, y + R)
        self._line(svg, x + 6, y + R - 4, x, y + R)
        self._line(svg, x - 6, y + R - 4, x, y + R)
        self._label(svg, x, y + 28, v)

    def _draw_pnp(self, svg, x, y, v, orient):
        R = 14
        ET.SubElement(svg, "circle", {
            "cx": str(x), "cy": str(y), "r": str(R),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        self._line(svg, x, y - R, x, y + R)
        self._line(svg, x - 6, y - R + 4, x, y - R)
        self._line(svg, x + 6, y - R + 4, x, y - R)
        self._label(svg, x, y + 28, v)

    def _draw_opamp(self, svg, x, y, v, orient):
        W, H = 60, 50
        x0, y0 = x - W // 2, y - H // 2
        d = f"M{x0},{y0} L{x0},{y0 + H} L{x0 + W * 0.7},{y} Z"
        self._path(svg, d, fill=True)
        self._line(svg, x0, y0 + H * 0.3, x0 - 8, y0 + H * 0.3)
        self._line(svg, x0, y0 + H * 0.7, x0 - 8, y0 + H * 0.7)
        self._line(svg, x0 + W * 0.7, y, x0 + W * 0.7 + 8, y)
        ET.SubElement(svg, "text", {
            "x": str(x0 - 10), "y": str(y0 + H * 0.3 + 4),
            "text-anchor": "end", "fill": self.COLORS["text"],
            "font-family": "monospace", "font-size": "8",
        }).text = "-"
        ET.SubElement(svg, "text", {
            "x": str(x0 - 10), "y": str(y0 + H * 0.7 + 4),
            "text-anchor": "end", "fill": self.COLORS["text"],
            "font-family": "monospace", "font-size": "8",
        }).text = "+"

    def _draw_ground(self, svg, x, y):
        W = 20
        self._line(svg, x, y - 15, x, y)
        self._line(svg, x - W, y, x + W, y)
        self._line(svg, x - W * 0.6, y + 5, x + W * 0.6, y + 5)
        self._line(svg, x - W * 0.2, y + 10, x + W * 0.2, y + 10)

    # ═══════════ 连线 ═══════════

    def _draw_wire(self, svg, x1, y1, x2, y2):
        if abs(x1 - x2) < 5 or abs(y1 - y2) < 5:
            d = f"M{x1},{y1} L{x2},{y2}"
        else:
            mid = (x1 + x2) / 2
            d = f"M{x1},{y1} L{mid},{y1} L{mid},{y2} L{x2},{y2}"
        ET.SubElement(svg, "path", {
            "d": d, "fill": "none", "stroke": self.COLORS["stroke"],
            "stroke-width": "1.5", "stroke-linejoin": "round",
        })

    # ═══════════ 辅助 ═══════════

    def _path(self, svg, d, fill=False):
        ET.SubElement(svg, "path", {
            "d": d, "fill": self.COLORS["fill"] if fill else "none",
            "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
            "stroke-linejoin": "round", "stroke-linecap": "round",
        })

    def _line(self, svg, x1, y1, x2, y2):
        ET.SubElement(svg, "line", {
            "x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2),
            "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
            "stroke-linecap": "round",
        })

    def _label(self, svg, x, y, text):
        if text:
            ET.SubElement(svg, "text", {
                "x": str(x), "y": str(y), "text-anchor": "middle",
                "fill": self.COLORS["text"], "font-family": "monospace",
                "font-size": "9",
            }).text = text
