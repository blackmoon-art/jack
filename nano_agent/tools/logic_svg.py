"""逻辑门 SVG 渲染器 — 零依赖，纯 Python stdlib。

DSL 格式 (每行一个门):
  GATE(input1, input2, ...) = output_name

支持的门:
  AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF

示例 — 半加器:
  XOR(A, B) = Sum
  AND(A, B) = Carry

示例 — 全加器:
  XOR(A, B) = g1
  XOR(g1, Cin) = Sum
  AND(A, B) = g2
  AND(g1, Cin) = g3
  OR(g2, g3) = Cout

示例 — 2级同步器:
  BUF(async_in) = s1
  BUF(s1, clk) = synced
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


class LogicSVG:
    TOOLS = [
        ("draw_logic",
         "Draw digital logic circuit diagrams as SVG. "
         "Pure logic gate netlist. One gate per line.\n"
         "\n"
         "**Format:** `GATE(input1, input2, ...) = output`\n"
         "**Gates:** AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF\n"
         "NOT has 1 input. BUF can have 1 or 2 (with clk).\n"
         "First use of a name = input port. Reuse = internal wire.\n"
         "\n"
         "**Half-adder:**\n"
         "`XOR(A, B) = Sum\nAND(A, B) = Carry`\n"
         "\n"
         "**Full-adder:**\n"
         "`XOR(A, B) = g1\nXOR(g1, Cin) = Sum\nAND(A, B) = g2\nAND(g1, Cin) = g3\nOR(g2, g3) = Cout`\n"
         "\n"
         "**Synchronizer:**\n"
         "`BUF(async_in, clk) = s1\nBUF(s1, clk) = synced`",
         "draw_logic",
         {"description": {"type": "string",
                          "description":
                          "Logic gate netlist. One gate per line. "
                          "GATE(input1, input2) = output. "
                          "Gates: AND,OR,NOT,NAND,NOR,XOR,XNOR,BUF. "
                          "Example: 'XOR(A,B)=Sum\\nAND(A,B)=Carry' for half-adder"},
          "title": {"type": "string", "description": "Diagram title"}},
         ["description"]),
    ]

    # ── 门形状定义 ──────────────────────────────────
    GATE_SHAPES = {
        "AND":   ("and",   False),
        "NAND":  ("and",   True),
        "OR":    ("or",    False),
        "NOR":   ("or",    True),
        "XOR":   ("xor",   False),
        "XNOR":  ("xor",   True),
        "NOT":   ("not",   True),
        "BUF":   ("buf",   False),
    }

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            web_static = Path(__file__).parent.parent.parent / "web" / "static"
            self.charts_dir = web_static / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def draw_logic(self, description: str, title: str = "") -> str:
        """解析 DSL → 布局 → 渲染 SVG。"""
        try:
            gates, inputs, outputs = self._parse(description)
            if not gates:
                return "Error: no valid gates found. Format: GATE(a,b) = out"
            svg = self._render(gates, inputs, outputs, title)
        except Exception as e:
            return f"Error drawing logic: {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"logic_{ts}.svg"
        filepath = self.charts_dir / filename
        filepath.write_text(svg, encoding="utf-8")
        url = f"/charts/{filename}"
        return f"![{title or 'Logic'}]({url})\n{url}"

    # ── DSL 解析 ───────────────────────────────────

    @staticmethod
    def _parse(desc: str) -> tuple[list, set, set]:
        """解析 DSL，返回 (gates, inputs, outputs)。"""
        gates = []
        all_inputs = set()
        all_outputs = set()
        internal = set()

        for line in desc.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(
                r'(AND|OR|NOT|NAND|NOR|XOR|XNOR|BUF)'
                r'\(([^)]+)\)\s*=\s*(\w+)', line, re.IGNORECASE)
            if not m:
                continue
            gtype = m.group(1).upper()
            raw_inputs = [x.strip() for x in m.group(2).split(",")]
            output = m.group(3).strip()

            gate = {"type": gtype, "inputs": raw_inputs, "output": output}
            gates.append(gate)
            all_outputs.add(output)
            for inp in raw_inputs:
                all_inputs.add(inp)

        # 纯输入 = 在 inputs 中但不在 outputs 中
        true_inputs = all_inputs - all_outputs
        # 纯输出 = 在 outputs 中但不在 inputs 中
        true_outputs = all_outputs - all_inputs
        # 都在 = 内部
        internal = all_inputs & all_outputs

        return gates, true_inputs, true_outputs

    # ── SVG 渲染 ───────────────────────────────────

    W, H = 80, 50       # 门尺寸
    IX, IY = 2, 10      # 输入引脚间距
    PIN = 10            # 引脚突出长度
    PORT_W, PORT_H = 48, 28  # 端口尺寸
    COL_GAP = 120       # 列间距
    ROW_GAP = 80        # 行间距
    WIRE_SPREAD = 12    # 多线时的垂直展开距离
    COLORS = {
        "bg": "#1a1a2e", "fg": "#e0e0e0", "grid": "#333",
        "gate_fill": "#2a2a4e", "gate_stroke": "#7c3aed",
        "wire": "#7c3aed", "port_fill": "#0f172a",
        "port_stroke": "#3b82f6", "text": "#e0e0e0",
        "title": "#e0e0e0",
    }
    FONT = "monospace"

    def _render(self, gates, inputs, outputs, title="") -> str:
        """布局 + 渲染 SVG。"""
        LogicSVG._wire_seq = 0  # 重置连线序号

        # ── 布局：拓扑排序 + 分层 ──
        # 构建 name → 生成它的 gate index
        produced_by = {}
        for i, g in enumerate(gates):
            produced_by[g["output"]] = i

        # 计算每个 gate 的深度（最长输入路径 + 1），处理环形
        depth = {}
        visiting = set()
        def get_depth(gi):
            if gi in depth:
                return depth[gi]
            if gi in visiting:
                return 1  # 环形回路：放在第一层
            visiting.add(gi)
            g = gates[gi]
            max_in = 0
            for inp in g["inputs"]:
                if inp in produced_by:
                    max_in = max(max_in, get_depth(produced_by[inp]))
            visiting.discard(gi)
            depth[gi] = max_in + 1
            return depth[gi]

        for i in range(len(gates)):
            get_depth(i)

        # 按深度分组 → 每列的行号
        cols = {}
        for i, g in enumerate(gates):
            d = depth[i]
            if d not in cols:
                cols[d] = []
            cols[d].append(i)

        # 输入端口在深度 0
        max_depth = max(cols.keys()) if cols else 0
        total_cols = max_depth + 1
        if inputs:
            total_cols += 1  # 输入列

        # 计算 SVG 尺寸
        max_gates_in_col = max(len(v) for v in cols.values()) if cols else 1
        svg_w = total_cols * self.COL_GAP + 100
        svg_h = max(max_gates_in_col, len(inputs), len(outputs)) * self.ROW_GAP + 80

        # ── 构建 SVG ──
        svg = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {svg_w} {svg_h}",
            "width": str(svg_w), "height": str(svg_h),
        })
        ET.SubElement(svg, "rect", {
            "width": str(svg_w), "height": str(svg_h),
            "fill": self.COLORS["bg"],
        })

        # 标题
        if title:
            ET.SubElement(svg, "text", {
                "x": str(svg_w // 2), "y": "24",
                "text-anchor": "middle", "fill": self.COLORS["title"],
                "font-family": self.FONT, "font-size": "14",
                "font-weight": "bold",
            }).text = title

        # ── 放置 gate 和记录位置 ──
        gate_positions = {}  # gate_index → (cx, cy)
        port_positions = {}  # port_name → (x, y, is_input)

        # 输入列 (深度 -1)
        input_col_x = 60
        input_names = sorted(inputs)
        for ri, name in enumerate(input_names):
            y = 50 + ri * self.ROW_GAP + self.ROW_GAP // 2
            port_positions[name] = (input_col_x, y, True)
            self._draw_port(svg, input_col_x, y, name, True)

        # Gate 列
        for d in sorted(cols.keys()):
            gx = 60 + (d + (1 if inputs else 0)) * self.COL_GAP
            for ri, gi in enumerate(cols[d]):
                g = gates[gi]
                gy = 50 + ri * self.ROW_GAP + self.ROW_GAP // 2
                gate_positions[gi] = (gx, gy)
                self._draw_gate(svg, gx, gy, g["type"], g.get("label", ""))
                # 输出端口
                out_name = g["output"]
                out_x = gx + self.W // 2 + self.PIN
                out_y = gy
                port_positions[out_name] = (out_x, out_y, False)

        # 输出列
        output_col_x = 60 + (max_depth + (1 if inputs else 0)) * self.COL_GAP + 40
        output_names = sorted(outputs)
        for ri, name in enumerate(output_names):
            y = 50 + ri * self.ROW_GAP + self.ROW_GAP // 2
            port_positions[name] = (output_col_x, y, False)
            self._draw_port(svg, output_col_x, y, name, False)

        # ── 连线 ──
        for gi, g in enumerate(gates):
            gx, gy = gate_positions[gi]
            # 输入线
            nin = len(g["inputs"])
            for ii, inp_name in enumerate(g["inputs"]):
                if inp_name in port_positions:
                    px, py, _ = port_positions[inp_name]
                    # 门输入引脚位置
                    iy_off = (ii - (nin - 1) / 2) * self.IY
                    gix = gx - self.W // 2 - self.PIN
                    giy = gy + iy_off
                    self._draw_wire(svg, px, py, gix, giy)

            # 输出线 → 连接到使用该输出的门
            out_name = g["output"]
            if out_name in port_positions:
                ox, oy, _ = port_positions[out_name]
            else:
                ox, oy = gx + self.W // 2, gy
            # 检查是否有 gate 用这个输出作为输入
            for gj, g2 in enumerate(gates):
                if out_name in g2["inputs"]:
                    break

        # 输出端口连线 (从最后的 gate 输出到输出端口)
        for gi, g in enumerate(gates):
            out_name = g["output"]
            if out_name in outputs and out_name in port_positions:
                gx, gy = gate_positions[gi]
                ox, oy, _ = port_positions[out_name]
                self._draw_wire(svg, gx + self.W // 2 + self.PIN, gy,
                                ox - self.PORT_W // 2, oy)

        return ET.tostring(svg, encoding="unicode")

    # ── 门形状绘制 ─────────────────────────────────

    def _draw_gate(self, svg, cx, cy, gtype, label=""):
        shape, bubble = self.GATE_SHAPES.get(gtype, ("and", False))
        x = cx - self.W // 2
        y = cy - self.H // 2

        g = ET.SubElement(svg, "g")

        if shape == "and":
            # D-shape
            d = (f"M{x},{y + self.H} L{x},{y} "
                 f"A{self.W},{self.H // 2} 0 0,1 {x},{y + self.H} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })
        elif shape == "or":
            # Shield — 左(x)宽，右(x+W)尖
            r = self.W * 0.5
            d = (f"M{x},{y} "
                 f"Q{x + self.W * 0.5},{y + self.H * 0.15} {x + self.W},{cy} "
                 f"Q{x + self.W * 0.5},{y + self.H * 0.85} {x},{y + self.H} "
                 f"Q{x - r},{cy} {x},{y} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })
        elif shape == "xor":
            r = self.W * 0.5
            d = (f"M{x},{y} "
                 f"Q{x + self.W * 0.4},{y + self.H * 0.1} {x + self.W},{cy} "
                 f"Q{x + self.W * 0.4},{y + self.H * 0.9} {x},{y + self.H} "
                 f"Q{x - r},{cy} {x},{y} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })
            # XOR 额外输入弧线
            ed = (f"M{x - self.W * 0.02},{y} "
                  f"Q{x - r * 0.7},{cy} {x - self.W * 0.02},{y + self.H}")
            ET.SubElement(g, "path", {
                "d": ed, "fill": "none",
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.2",
            })
        elif shape in ("not", "buf"):
            # Triangle
            d = (f"M{x},{y} L{x},{y + self.H} L{x + self.W * 0.7},{cy} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })

        # 气泡 (在输出侧，右侧)
        if bubble:
            bx = x + self.W + 6 if shape != "not" else x + self.W * 0.8
            ET.SubElement(g, "circle", {
                "cx": str(bx), "cy": str(cy), "r": "4",
                "fill": "none", "stroke": self.COLORS["gate_stroke"],
                "stroke-width": "1.2",
            })

        # 标签
        if label:
            ET.SubElement(g, "text", {
                "x": str(cx), "y": str(cy + 2), "text-anchor": "middle",
                "fill": self.COLORS["text"], "font-family": self.FONT,
                "font-size": "8", "dy": "0.3em",
            }).text = label
        else:
            ET.SubElement(g, "text", {
                "x": str(cx), "y": str(cy + 2), "text-anchor": "middle",
                "fill": self.COLORS["text"], "font-family": self.FONT,
                "font-size": "8", "dy": "0.3em",
            }).text = gtype[:3] if gtype != "BUF" else "BUF"

    # ── 端口 ────────────────────────────────────────

    def _draw_port(self, svg, x, y, name, is_input):
        pw, ph = self.PORT_W, self.PORT_H
        if is_input:
            px = x - pw // 2
        else:
            px = x - pw // 2
        py = y - ph // 2

        ET.SubElement(svg, "rect", {
            "x": str(px), "y": str(py), "width": str(pw), "height": str(ph),
            "rx": "4", "fill": self.COLORS["port_fill"],
            "stroke": self.COLORS["port_stroke"], "stroke-width": "1.2",
        })
        ET.SubElement(svg, "text", {
            "x": str(x), "y": str(y + 2), "text-anchor": "middle",
            "fill": self.COLORS["port_stroke"], "font-family": self.FONT,
            "font-size": "10", "dy": "0.35em", "font-weight": "bold",
        }).text = name

    # ── 连线 ────────────────────────────────────────

    _wire_seq = 0  # 全局连线序号，用于错开避免重叠

    def _draw_wire(self, svg, x1, y1, x2, y2):
        """画正交连线（水平→垂直→水平），自动错开避免重叠。"""
        LogicSVG._wire_seq += 1
        spread = (LogicSVG._wire_seq % 5 - 2) * self.WIRE_SPREAD * 0.3
        mid_x = (x1 + x2) / 2 + spread
        d = f"M{x1},{y1} L{mid_x},{y1} L{mid_x},{y2} L{x2},{y2}"
        ET.SubElement(svg, "path", {
            "d": d, "fill": "none", "stroke": self.COLORS["wire"],
            "stroke-width": "1.5", "stroke-linejoin": "round",
        })
