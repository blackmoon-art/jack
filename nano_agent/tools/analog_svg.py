"""模拟电路 SVG 渲染器 — 纯 Python，零依赖。

DSL: 和现有 schemdraw DSL 完全兼容
  ac(Vin) -> resistor(1k) -> capacitor(10n) -> ground

支持元件:
  无源: resistor, capacitor, inductor, diode, led, ground
  有源: opamp, npn, pnp
  电源: ac, v, battery, isource
  连接: line, wire, open, dot, port
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


class AnalogSVG:
    TOOLS = [
        ("draw_analog_svg",
         "Draw analog circuit diagrams as SVG. Same DSL as draw_analog.\n"
         "For: filters, amplifiers, transistor circuits, power supplies.\n"
         "\n"
         "**Syntax:** Series `A->B->C`, Multi-chain `;`, "
         "Directions `up/down/left/right`, Named nodes `as N1`, "
         "Connect `connect(N1, N2)`.\n"
         "\n"
         "**RC low-pass:**\n"
         "`ac(Vin)->resistor(1k) as n1->line->open(Vout);n1->down->capacitor(10n)->ground`\n"
         "\n"
         "**Sallen-Key:**\n"
         "`ac(Vin)->resistor(R1) as n1->resistor(R2) as n2;n2->opamp@in1;"
         "opamp@out as op_out;connect(op_out,opamp@in2);"
         "n1->down->capacitor(C1) as c1_end;connect(op_out,c1_end);"
         "n2->down->capacitor(C2)->ground`",
         "draw_analog_svg",
         {"description": {"type": "string",
                          "description":
                          "Analog circuit. Same DSL as draw_analog. "
                          "Components: resistor,capacitor,inductor,diode,led,opamp,npn,pnp,"
                          "ground,ac,v,battery,isource,line,wire,open,dot,port. "
                          "Filter: 'ac(Vin)->r(1k) as n1->line->open(Vout);n1->down->c(10n)->gnd'"},
          "title": {"type": "string", "description": "Circuit title"}},
         ["description"]),
    ]

    # SVG 尺寸常量
    W, H = 60, 30      # 标准元件尺寸
    PIN = 8            # 引脚长度
    COL_GAP = 90       # 列间距
    ROW_GAP = 64       # 行间距
    COLORS = {
        "bg": "#1a1a2e", "fg": "#e0e0e0",
        "stroke": "#7c3aed", "fill": "#2a2a4e",
        "text": "#e0e0e0", "gnd": "#3b82f6",
    }

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

    # ═══════════ 解析 ═══════════

    @staticmethod
    def _parse_chains(desc: str) -> list:
        """按 ; 拆链，处理括号嵌套。"""
        chains, cur, depth = [], "", 0
        for ch in desc:
            if ch == ";" and depth == 0:
                if cur.strip(): chains.append(cur.strip())
                cur = ""
            else:
                if ch in "[(": depth += 1
                elif ch in "])": depth -= 1
                cur += ch
        if cur.strip(): chains.append(cur.strip())
        return chains

    # ═══════════ 渲染 ═══════════

    def _render(self, description: str, title: str = "") -> str:
        chains = self._parse_chains(description)
        # 两遍：先收集所有元件和连接，再布局渲染
        grid, named, edges, svg_w, svg_h = self._layout(chains)

        svg = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {svg_w} {svg_h}",
            "width": str(svg_w), "height": str(svg_h),
        })
        ET.SubElement(svg, "rect", {
            "width": str(svg_w), "height": str(svg_h),
            "fill": self.COLORS["bg"],
        })
        if title:
            ET.SubElement(svg, "text", {
                "x": str(svg_w // 2), "y": "20",
                "text-anchor": "middle", "fill": self.COLORS["text"],
                "font-family": "monospace", "font-size": "13", "font-weight": "bold",
            }).text = title

        # 放置元件
        placed = {}
        for nid, (name, value, x, y, direction) in grid.items():
            el = self._draw_component(svg, name, value, x, y, direction)
            placed[nid] = (x, y, el)

        # 连线
        for a, b in edges:
            if a in placed and b in placed:
                ax, ay, _ = placed[a]
                bx, by, _ = placed[b]
                self._draw_wire(svg, ax, ay, bx, by)

        return ET.tostring(svg, encoding="unicode")

    # ═══════════ 布局 ═══════════

    def _layout(self, chains) -> tuple:
        """遍历所有链，分配网格位置。返回 (grid, named, edges, w, h)。"""
        grid = {}     # nid → (name, value, x, y, direction)
        named = {}    # name → nid
        edges = []    # [(from_nid, to_nid)]
        nid = 0
        global_row = 0

        for chain_desc in chains:
            chain_desc = chain_desc.strip()
            if chain_desc.startswith("connect("):
                continue  # connect 暂不在 SVG 中处理

            col, row = 0, global_row
            prev_nid = None

            # 链首是命名节点引用？
            first = chain_desc.split("->")[0].strip().split()[0] if chain_desc else ""
            if first and first in named:
                ref = named[first]
                col = grid[ref][2]  # 从命名节点的列继续
                row = grid[ref][3]
                prev_nid = ref
                chain_desc = chain_desc[len(first):].strip()
                if chain_desc.startswith("->"): chain_desc = chain_desc[2:].strip()

            # 解析 -> 分隔的元件
            tokens = self._tokenize(chain_desc)
            direction = (1, 0)  # default: right

            for tok in tokens:
                if tok in ("up", "down", "left", "right"):
                    direction = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[tok]
                    continue

                name, value, node_name = self._parse_token(tok)
                col += direction[0]
                row += direction[1]

                grid[nid] = (name, value, col, row, self._dir_name(direction))
                if prev_nid is not None:
                    edges.append((prev_nid, nid))
                if node_name:
                    named[node_name] = nid
                prev_nid = nid
                nid += 1

            global_row += 1

        if not grid:
            return {}, {}, [], 200, 100

        max_col = max(c for _, _, c, _, _ in grid.values())
        max_row = max(r for _, _, _, r, _ in grid.values())
        min_row = min(r for _, _, _, r, _ in grid.values())
        svg_w = (max_col + 2) * self.COL_GAP
        svg_h = (max_row - min_row + 2) * self.ROW_GAP
        return grid, named, edges, max(200, svg_w), max(100, svg_h)

    @staticmethod
    def _tokenize(desc: str) -> list:
        """按 -> 拆分，处理括号内 ->。"""
        tokens, cur, depth = [], "", 0
        for ch in desc:
            if ch == "-" and depth == 0 and cur.endswith("-"):
                cur = cur[:-1]
                if cur.strip(): tokens.append(cur.strip())
                cur = ""
            else:
                if ch in "[(": depth += 1
                elif ch in "])": depth -= 1
                cur += ch
        if cur.strip(): tokens.append(cur.strip())
        return tokens

    @staticmethod
    def _parse_token(tok: str) -> tuple:
        """解析 'resistor(1k) as n1' → (name, value, node_name)。"""
        node_name = None
        if " as " in tok:
            tok, node_name = tok.rsplit(" as ", 1)
            tok, node_name = tok.strip(), node_name.strip()
        if "(" in tok and ")" in tok:
            n = tok.split("(")[0].strip().lower()
            v = tok[tok.index("(") + 1:tok.index(")")].strip()
            return n, v, node_name
        return tok.strip().lower(), "", node_name

    @staticmethod
    def _dir_name(d: tuple) -> str:
        return {(1, 0): "r", (0, 1): "d", (0, -1): "u", (-1, 0): "l"}.get(d, "r")

    # ═══════════ 元件绘制 ═══════════

    def _draw_component(self, svg, name: str, value: str, col: int, row: int, direction: str):
        """在网格位置绘制单个元件。"""
        cx = col * self.COL_GAP
        cy = row * self.ROW_GAP
        g = ET.SubElement(svg, "g")

        # 映射元件名（支持缩写 r/c/l）
        name_map = {"r": "resistor", "c": "capacitor", "l": "inductor",
                     "gnd": "ground", "v": "vsource"}
        name = name_map.get(name, name)

        draw_fn = getattr(self, f"_draw_{name}", None)
        if draw_fn:
            draw_fn(g, cx, cy, value, direction)
        else:
            self._draw_unknown(g, cx, cy, name, value)

        # 标签
        label = value if value else name
        ET.SubElement(g, "text", {
            "x": str(cx), "y": str(cy + self.H // 2 + 12),
            "text-anchor": "middle", "fill": self.COLORS["text"],
            "font-family": "monospace", "font-size": "9",
        }).text = label

        return g

    # ── 无源元件 ──

    def _draw_resistor(self, g, cx, cy, value, direction):
        w, h = self.W, self.H
        x, y = cx - w // 2, cy - h // 2
        n = 5
        seg_w = w / (n + 1)
        pts = [(x, cy)]
        for i in range(1, n + 1):
            px = x + i * seg_w
            py = cy + (seg_w * 0.6 if i % 2 == 1 else -seg_w * 0.6)
            pts.append((px, py))
        pts.append((x + w, cy))
        d = "M" + " L".join(f"{px},{py}" for px, py in pts)
        self._add_path(g, d)
        # 引脚
        self._add_pin(g, cx, cy, "l")
        self._add_pin(g, cx, cy, "r")

    def _draw_capacitor(self, g, cx, cy, value, direction):
        gap = 8
        self._add_line(g, cx - self.W // 2, cy, cx - gap, cy)
        self._add_line(g, cx - gap, cy - self.H // 2, cx - gap, cy + self.H // 2)
        self._add_line(g, cx + gap, cy - self.H // 2, cx + gap, cy + self.H // 2)
        self._add_line(g, cx + gap, cy, cx + self.W // 2, cy)

    def _draw_inductor(self, g, cx, cy, value, direction):
        w = self.W
        x = cx - w // 2
        n = 4
        r = 6
        d = f"M{x},{cy}"
        for i in range(n):
            sweep = 1 if i % 2 == 0 else 0
            d += f" A{r},{r} 0 0,{sweep} {x + (i + 1) * r * 2},{cy}"
        self._add_path(g, d)
        self._add_pin(g, cx, cy, "l")
        self._add_pin(g, cx, cy, "r")

    def _draw_diode(self, g, cx, cy, value, direction):
        s = 10
        # 三角
        d = f"M{cx - s},{cy - s} L{cx + s},{cy} L{cx - s},{cy + s} Z"
        self._add_path(g, d, fill=True)
        # 竖线
        self._add_line(g, cx + s, cy - s, cx + s, cy + s)
        self._add_pin(g, cx, cy, "l")
        self._add_pin(g, cx, cy, "r")

    def _draw_led(self, g, cx, cy, value, direction):
        self._draw_diode(g, cx, cy, value, direction)
        # 两个箭头表示发光
        s = 8
        self._add_line(g, cx + s + 4, cy - s, cx + s + 12, cy - s - 4)
        self._add_line(g, cx + s + 4, cy + s, cx + s + 12, cy + s + 4)

    def _draw_ground(self, g, cx, cy, value, direction):
        w = 20
        self._add_line(g, cx, cy - self.H // 2, cx, cy)
        self._add_line(g, cx - w, cy, cx + w, cy)
        self._add_line(g, cx - w * 0.6, cy + 5, cx + w * 0.6, cy + 5)
        self._add_line(g, cx - w * 0.2, cy + 10, cx + w * 0.2, cy + 10)

    # ── 有源元件 ──

    def _draw_opamp(self, g, cx, cy, value, direction):
        w, h = self.W, self.H + 10
        x, y = cx - w // 2, cy - h // 2
        d = f"M{x},{y} L{x},{y + h} L{x + w * 0.7},{cy} Z"
        self._add_path(g, d, fill=True)
        # 引脚: in1(-) top, in2(+) bottom, out right
        self._add_line(g, x, y + h * 0.3, x - self.PIN, y + h * 0.3)  # in1
        self._add_line(g, x, y + h * 0.7, x - self.PIN, y + h * 0.7)  # in2
        self._add_line(g, x + w * 0.7, cy, x + w * 0.7 + self.PIN, cy)  # out
        # +/- 标签
        ET.SubElement(g, "text", {
            "x": str(x - self.PIN - 4), "y": str(y + h * 0.3 + 4),
            "text-anchor": "end", "fill": self.COLORS["text"],
            "font-family": "monospace", "font-size": "8",
        }).text = "-"
        ET.SubElement(g, "text", {
            "x": str(x - self.PIN - 4), "y": str(y + h * 0.7 + 4),
            "text-anchor": "end", "fill": self.COLORS["text"],
            "font-family": "monospace", "font-size": "8",
        }).text = "+"

    def _draw_npn(self, g, cx, cy, value, direction):
        r = 14
        ET.SubElement(g, "circle", {
            "cx": str(cx), "cy": str(cy), "r": str(r),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        # 内部竖线
        self._add_line(g, cx, cy - r, cx, cy + r)
        # Emitter 箭头
        self._add_line(g, cx + 6, cy + r - 4, cx, cy + r)
        self._add_line(g, cx - 6, cy + r - 4, cx, cy + r)
        # 引脚
        self._add_line(g, cx - r, cy - r * 0.5, cx - r - self.PIN, cy - r * 0.5)  # C
        self._add_line(g, cx - r, cy + r * 0.5, cx - r - self.PIN, cy + r * 0.5)  # B
        self._add_line(g, cx + r, cy, cx + r + self.PIN, cy)  # E

    def _draw_pnp(self, g, cx, cy, value, direction):
        r = 14
        ET.SubElement(g, "circle", {
            "cx": str(cx), "cy": str(cy), "r": str(r),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        self._add_line(g, cx, cy - r, cx, cy + r)
        # Emitter 箭头（反向）
        self._add_line(g, cx - 6, cy - r + 4, cx, cy - r)
        self._add_line(g, cx + 6, cy - r + 4, cx, cy - r)
        self._add_line(g, cx - r, cy - r * 0.5, cx - r - self.PIN, cy - r * 0.5)
        self._add_line(g, cx - r, cy + r * 0.5, cx - r - self.PIN, cy + r * 0.5)
        self._add_line(g, cx + r, cy, cx + r + self.PIN, cy)

    # ── 电源 ──

    def _draw_ac(self, g, cx, cy, value, direction):
        r = 14
        ET.SubElement(g, "circle", {
            "cx": str(cx), "cy": str(cy), "r": str(r),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        # 正弦波符号
        w = 16
        d = f"M{cx - w // 2},{cy - 3} Q{cx - 4},{cy - 8} {cx},{cy} Q{cx + 4},{cy + 8} {cx + w // 2},{cy + 3}"
        self._add_path(g, d)
        self._add_pin(g, cx, cy, "l")
        self._add_pin(g, cx, cy, "r")

    def _draw_vsource(self, g, cx, cy, value, direction):
        r = 14
        ET.SubElement(g, "circle", {
            "cx": str(cx), "cy": str(cy), "r": str(r),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        ET.SubElement(g, "text", {
            "x": str(cx), "y": str(cy + 4), "text-anchor": "middle",
            "fill": self.COLORS["text"], "font-family": "monospace", "font-size": "10",
        }).text = "+"
        self._add_pin(g, cx, cy, "l")
        self._add_pin(g, cx, cy, "r")

    _draw_v = _draw_vsource
    _draw_battery = _draw_vsource
    _draw_isource = _draw_ac
    _draw_source = _draw_ac
    _draw_signal = _draw_ac

    # ── 连接 ──

    _draw_line = lambda self, g, cx, cy, v, d: None  # 线本身不可见，连线处理
    _draw_wire = _draw_line
    _draw_open = _draw_line
    _draw_port = lambda self, g, cx, cy, v, d: None
    _draw_dot = lambda self, g, cx, cy, v, d: ET.SubElement(g, "circle", {
        "cx": str(cx), "cy": str(cy), "r": "3",
        "fill": self.COLORS["stroke"],
    })

    def _draw_unknown(self, g, cx, cy, name, value):
        w, h = self.W, self.H
        ET.SubElement(g, "rect", {
            "x": str(cx - w // 2), "y": str(cy - h // 2),
            "width": str(w), "height": str(h), "rx": "4",
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.2",
        })
        ET.SubElement(g, "text", {
            "x": str(cx), "y": str(cy + 4), "text-anchor": "middle",
            "fill": self.COLORS["text"], "font-family": "monospace", "font-size": "10",
        }).text = name

    # ── 绘制辅助 ──

    def _add_path(self, g, d, fill=False):
        ET.SubElement(g, "path", {
            "d": d,
            "fill": self.COLORS["fill"] if fill else "none",
            "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
            "stroke-linejoin": "round", "stroke-linecap": "round",
        })

    def _add_line(self, g, x1, y1, x2, y2):
        ET.SubElement(g, "line", {
            "x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2),
            "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
            "stroke-linecap": "round",
        })

    def _add_pin(self, g, cx, cy, side):
        """在指定侧画引脚短线。side: 'l'=left, 'r'=right"""
        if side == "l":
            self._add_line(g, cx - self.W // 2 - self.PIN, cy, cx - self.W // 2, cy)
        else:
            self._add_line(g, cx + self.W // 2, cy, cx + self.W // 2 + self.PIN, cy)

    def _draw_wire(self, svg, x1, y1, x2, y2):
        """画连线（带圆角拐弯）。"""
        mid_x = (x1 + x2) / 2
        d = f"M{x1},{y1} L{mid_x},{y1} L{mid_x},{y2} L{x2},{y2}"
        ET.SubElement(svg, "path", {
            "d": d, "fill": "none", "stroke": self.COLORS["stroke"],
            "stroke-width": "1.5", "stroke-linejoin": "round",
        })
