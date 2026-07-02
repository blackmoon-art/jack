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

布局策略: BFS 信号流 — 从源节点 (GND + V/I 源输出) 出发，按距离分配列位置。
"""

import logging
import re
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.analog_svg")


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
    COL_GAP = 160
    ROW_GAP = 80
    COLORS = {"bg": "#1a1a2e", "fg": "#e0e0e0", "stroke": "#7c3aed",
              "fill": "#2a2a4e", "text": "#e0e0e0", "gnd": "#3b82f6",
              "node_dot": "#a78bfa"}

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
            logger.exception(f"Analog SVG rendering failed: {e}")
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
                # V<name> <n+> <n-> [AC|DC] [value]
                if len(rest) >= 3 and rest[2].upper() == "AC":
                    val = "AC " + (rest[3] if len(rest) > 3 else "1")
                else:
                    val = " ".join(rest[2:]) if len(rest) > 2 else "DC 0"
                comps.append({"type": "vsource", "name": name,
                               "nodes": rest[:2], "value": val})
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

    # ═══════════ 节点 ID — 稳定映射，无 hash 碰撞 ═══════════

    @staticmethod
    def _make_node_id_map(comps: list) -> dict:
        """为所有非整数节点名分配稳定的整数 ID，不使用 hash()。"""
        mapping = {}
        next_id = 1000  # 从 1000 开始，避免和 SPICE 数值节点冲突
        for c in comps:
            for n in c["nodes"]:
                try:
                    mapping[n] = int(n)
                except (ValueError, TypeError):
                    if n not in mapping:
                        mapping[n] = next_id
                        next_id += 1
        return mapping

    def _node_id(self, n: str, node_map: dict = None) -> int:
        """返回稳定的节点 ID（需要预先构建 node_map）。"""
        if node_map is not None:
            return node_map.get(n, 0)
        # fallback for backward compat
        try:
            return int(n)
        except (ValueError, TypeError):
            logger.warning(f"Non-integer node '{n}' without node_map; using fallback hash")
            return abs(hash(str(n))) % 100000 + 10000

    # ═══════════ 布局 — BFS 信号流 ═══════════

    def _layout(self, comps):
        """BFS 信号流布局。

        1. 构建 node↔component 邻接表
        2. 从源节点 (GND + V/I 源输出节点) BFS
        3. 按 BFS 层级分配列位置
        4. 节点放在列边界，元件放在列内
        """
        node_map = self._make_node_id_map(comps)

        # 解析节点 ID
        comp_nids = {}  # comp_name → [nid, ...]
        node_comps = defaultdict(list)  # nid → [comp_name, ...]
        for c in comps:
            nids = [node_map[n] for n in c["nodes"]]
            comp_nids[c["name"]] = nids
            for nid in nids:
                node_comps[nid].append(c["name"])

        # 找源节点: AC 信号源的输出节点（GND 不作为源，避免短路 BFS）
        sources = set()
        for c in comps:
            if c["type"] == "vsource":
                val = c.get("value", "").upper()
                if "AC" in val or "SIN" in val or "PULSE" in val:
                    nids = comp_nids[c["name"]]
                    if len(nids) >= 2:
                        sources.add(nids[0])  # positive terminal
        # 无信号源时，从所有非 GND 节点出发（兜底）
        if not sources:
            for cname, nids in comp_nids.items():
                for nid in nids:
                    if nid != 0:
                        sources.add(nid)
            if sources:
                sources = {min(sources)}  # 只取最小，避免多源全在 level 0

        # BFS 分配层级（GND=0 不参与传播，避免所有元件通过 GND 短路到 level 0）
        comp_level = {}
        node_level = {}
        visited_nodes = set(sources)
        visited_comps = set()
        for s in sources:
            node_level[s] = 0

        queue = deque(sources)
        while queue:
            nid = queue.popleft()
            nlev = node_level.get(nid, 0)
            for cname in node_comps.get(nid, []):
                if cname in visited_comps:
                    continue
                visited_comps.add(cname)
                comp_level[cname] = nlev
                for other_nid in comp_nids[cname]:
                    if other_nid != 0 and other_nid not in visited_nodes:
                        visited_nodes.add(other_nid)
                        node_level[other_nid] = nlev + 1
                        queue.append(other_nid)

        # 未访问的元件（孤立）：放在最后
        max_lev = max(comp_level.values()) if comp_level else 0
        for c in comps:
            if c["name"] not in comp_level:
                max_lev += 1
                comp_level[c["name"]] = max_lev

        # 按层级分组
        level_comps = defaultdict(list)
        for c in comps:
            level_comps[comp_level[c["name"]]].append(c["name"])

        # 分配位置
        comp_pos = {}   # comp_name → (cx, cy)
        node_pos = {}   # nid → (x, y)

        for lev in sorted(level_comps):
            comps_in_lev = level_comps[lev]
            col_x = 120 + lev * self.COL_GAP
            for ri, cname in enumerate(comps_in_lev):
                cy = 80 + ri * self.ROW_GAP
                comp_pos[cname] = (col_x, cy)

        # 节点位置: 放在共享该节点的元件中心平均值处
        for nid, cnames in node_comps.items():
            if cnames:
                avg_x = sum(comp_pos[c][0] for c in cnames if c in comp_pos) / len(cnames)
                avg_y = sum(comp_pos[c][1] for c in cnames if c in comp_pos) / len(cnames)
                node_pos[nid] = (avg_x, avg_y)

        # GND 节点放在底部
        max_y = max(y for _, y in comp_pos.values()) if comp_pos else 100
        if 0 not in node_pos:
            node_pos[0] = (120, max_y + 80)

        # 计算 SVG 尺寸
        svg_w = (max(level_comps.keys()) + 2) * self.COL_GAP + 60 if level_comps else 400
        max_rows = max(len(v) for v in level_comps.values()) if level_comps else 1
        svg_h = max(300, max_rows * self.ROW_GAP + 120, max_y + 100)

        # 元件→节点连接 (comp_name → [(nid, pin_side)])
        # pin_side: 'L'=left, 'R'=right, 'T'=top, 'B'=bottom
        comp_conns = defaultdict(list)
        for c in comps:
            nids = comp_nids[c["name"]]
            ctype = c["type"]
            if ctype in ("resistor", "capacitor", "inductor", "diode"):
                # 2-terminal: left→n1, right→n2
                comp_conns[c["name"]].append((nids[0], "L"))
                comp_conns[c["name"]].append((nids[1], "R"))
            elif ctype == "vsource":
                comp_conns[c["name"]].append((nids[0], "L"))
                comp_conns[c["name"]].append((nids[1], "R"))
            elif ctype == "isource":
                comp_conns[c["name"]].append((nids[0], "L"))
                comp_conns[c["name"]].append((nids[1], "R"))
            elif ctype in ("npn", "pnp"):
                # 3-terminal: C=top, B=left, E=bottom
                comp_conns[c["name"]].append((nids[0], "T"))  # collector
                comp_conns[c["name"]].append((nids[1], "L"))  # base
                comp_conns[c["name"]].append((nids[2], "B"))  # emitter
            elif ctype == "opamp":
                # 5-terminal: in-=left-top, in+=left-bot, out=right, vcc=top, vss=bottom
                if len(nids) >= 3:
                    comp_conns[c["name"]].append((nids[0], "L"))  # in-
                    comp_conns[c["name"]].append((nids[1], "L"))  # in+
                    comp_conns[c["name"]].append((nids[2], "R"))  # out
                if len(nids) >= 5:
                    comp_conns[c["name"]].append((nids[3], "T"))  # vcc
                    comp_conns[c["name"]].append((nids[4], "B"))  # vss

        return node_map, comp_pos, node_pos, comp_conns, comp_nids, svg_w, svg_h

    # ═══════════ 渲染 ═══════════

    def _render(self, description: str, title: str = "") -> str:
        comps = self._parse_spice(description)
        if not comps:
            return "Error: no valid SPICE components found"

        node_map, comp_pos, node_pos, comp_conns, comp_nids, svg_w, svg_h = self._layout(comps)

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
                "x": str(svg_w // 2), "y": "24", "text-anchor": "middle",
                "fill": self.COLORS["text"], "font-family": "monospace",
                "font-size": "13", "font-weight": "bold",
            }).text = title

        # 统计每个节点被多少元件共享（用于画连接点）
        node_comp_count = defaultdict(int)
        for cname, nids in comp_nids.items():
            for nid in nids:
                node_comp_count[nid] += 1

        # 1. 画节点连接点 (junction dots) — 共享节点画小圆点
        for nid, (nx, ny) in node_pos.items():
            if nid == 0:
                continue  # GND 单独画
            if node_comp_count[nid] > 1:
                ET.SubElement(svg, "circle", {
                    "cx": str(nx), "cy": str(ny), "r": "3",
                    "fill": self.COLORS["node_dot"],
                    "stroke": "none",
                })

        # 2. 画元件 → 节点连线
        for comp in comps:
            cname = comp["name"]
            if cname not in comp_pos:
                continue
            cx, cy = comp_pos[cname]
            for nid, pin_side in comp_conns.get(cname, []):
                if nid not in node_pos:
                    continue
                nx, ny = node_pos[nid]
                # 计算元件侧的引脚位置
                px, py = self._pin_pos(cx, cy, pin_side, comp["type"])
                self._draw_component_wire(svg, px, py, nx, ny)

        # 3. 画元件（覆盖在线上）
        for comp in comps:
            if comp["name"] in comp_pos:
                x, y = comp_pos[comp["name"]]
                self._draw_component(svg, comp, x, y)

        # 4. 画地符号（节点 0）
        if 0 in node_pos:
            gx, gy = node_pos[0]
            self._draw_ground(svg, gx, gy)

        return ET.tostring(svg, encoding="unicode")

    @staticmethod
    def _pin_pos(cx, cy, side, ctype):
        """返回元件上指定侧的引脚坐标。"""
        offsets = {
            "L": (-35, 0),
            "R": (35, 0),
            "T": (0, -28),
            "B": (0, 28),
        }
        dx, dy = offsets.get(side, (0, 0))
        return (cx + dx, cy + dy)

    def _draw_component_wire(self, svg, x1, y1, x2, y2):
        """画从元件引脚到节点的连线（直角走线）。"""
        if abs(x1 - x2) < 3 and abs(y1 - y2) < 3:
            return  # 太近，不画线
        if abs(x1 - x2) < 5 or abs(y1 - y2) < 5:
            d = f"M{x1},{y1} L{x2},{y2}"
        else:
            mid = (x1 + x2) / 2
            d = f"M{x1},{y1} L{mid},{y1} L{mid},{y2} L{x2},{y2}"
        ET.SubElement(svg, "path", {
            "d": d, "fill": "none", "stroke": self.COLORS["stroke"],
            "stroke-width": "1.5", "stroke-linejoin": "round",
        })

    # ═══════════ 元件绘制 ═══════════

    def _draw_component(self, svg, comp, x, y):
        """绘制单个元件，在其中心 (x, y) 处。"""
        t = comp["type"]
        v = comp.get("value", "")
        name = comp.get("name", "")
        draw_fn = getattr(self, f"_draw_{t}", None)
        if draw_fn:
            draw_fn(svg, x, y, v, name)

    def _draw_resistor(self, svg, x, y, v, name):
        W = 60
        n = 5
        seg_w = W / (n + 1)
        pts = [(x - W // 2, y)]
        for i in range(1, n + 1):
            pts.append((x - W // 2 + i * seg_w,
                        y + (seg_w * 0.6 if i % 2 == 1 else -seg_w * 0.6)))
        pts.append((x + W // 2, y))
        d = "M" + " L".join(f"{px},{py}" for px, py in pts)
        self._path(svg, d)
        self._label(svg, x, y + 22, v)

    def _draw_capacitor(self, svg, x, y, v, name):
        L, G = 30, 8
        self._line(svg, x - L, y, x - G, y)
        self._line(svg, x - G, y - 10, x - G, y + 10)
        self._line(svg, x + G, y - 10, x + G, y + 10)
        self._line(svg, x + G, y, x + L, y)
        self._label(svg, x, y + 22, v)

    def _draw_inductor(self, svg, x, y, v, name):
        L, R, N = 40, 5, 4
        d = f"M{x - L // 2},{y}"
        for i in range(N):
            sweep = 1 if i % 2 == 0 else 0
            d += f" A{R},{R} 0 0,{sweep} {x - L // 2 + (i+1)*L//N},{y}"
        self._path(svg, d)
        self._label(svg, x, y + 22, v)

    def _draw_diode(self, svg, x, y, v, name):
        S = 10
        d = f"M{x + S},{y - S} L{x},{y} L{x + S},{y + S} Z"
        self._path(svg, d, fill=True)
        self._line(svg, x - S, y - S, x - S, y + S)
        self._label(svg, x, y + 22, v)

    def _draw_vsource(self, svg, x, y, v, name):
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

    def _draw_isource(self, svg, x, y, v, name):
        """电流源 — 双圈符号，区别于电压源的单圈。"""
        R1, R2 = 14, 11
        ET.SubElement(svg, "circle", {
            "cx": str(x), "cy": str(y), "r": str(R1),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        ET.SubElement(svg, "circle", {
            "cx": str(x), "cy": str(y), "r": str(R2),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.2",
        })
        ET.SubElement(svg, "text", {
            "x": str(x), "y": str(y + 4), "text-anchor": "middle",
            "fill": self.COLORS["text"], "font-family": "monospace", "font-size": "9",
        }).text = "↑"
        self._label(svg, x, y + 28, v)

    def _draw_npn(self, svg, x, y, v, name):
        R = 14
        ET.SubElement(svg, "circle", {
            "cx": str(x), "cy": str(y), "r": str(R),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        self._line(svg, x, y - R, x, y + R)
        self._line(svg, x + 6, y + R - 4, x, y + R)
        self._line(svg, x - 6, y + R - 4, x, y + R)
        self._label(svg, x, y + 28, v)

    def _draw_pnp(self, svg, x, y, v, name):
        R = 14
        ET.SubElement(svg, "circle", {
            "cx": str(x), "cy": str(y), "r": str(R),
            "fill": "none", "stroke": self.COLORS["stroke"], "stroke-width": "1.5",
        })
        self._line(svg, x, y - R, x, y + R)
        self._line(svg, x - 6, y - R + 4, x, y - R)
        self._line(svg, x + 6, y - R + 4, x, y - R)
        self._label(svg, x, y + 28, v)

    def _draw_opamp(self, svg, x, y, v, name):
        W, H = 60, 50
        x0, y0 = x - W // 2, y - H // 2
        d = f"M{x0},{y0} L{x0},{y0 + H} L{x0 + W * 0.7},{y} Z"
        self._path(svg, d, fill=True)
        # 引脚短线
        self._line(svg, x0, y0 + H * 0.3, x0 - 10, y0 + H * 0.3)
        self._line(svg, x0, y0 + H * 0.7, x0 - 10, y0 + H * 0.7)
        self._line(svg, x0 + W * 0.7, y, x0 + W * 0.7 + 10, y)
        ET.SubElement(svg, "text", {
            "x": str(x0 - 12), "y": str(y0 + H * 0.3 + 4),
            "text-anchor": "end", "fill": self.COLORS["text"],
            "font-family": "monospace", "font-size": "8",
        }).text = "-"
        ET.SubElement(svg, "text", {
            "x": str(x0 - 12), "y": str(y0 + H * 0.7 + 4),
            "text-anchor": "end", "fill": self.COLORS["text"],
            "font-family": "monospace", "font-size": "8",
        }).text = "+"
        self._label(svg, x, y + 34, name)

    def _draw_ground(self, svg, x, y):
        W = 20
        self._line(svg, x, y - 15, x, y)
        self._line(svg, x - W, y, x + W, y)
        self._line(svg, x - W * 0.6, y + 5, x + W * 0.6, y + 5)
        self._line(svg, x - W * 0.2, y + 10, x + W * 0.2, y + 10)

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
