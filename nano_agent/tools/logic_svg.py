"""逻辑门 / RTL / 模块级 SVG 渲染器。

支持三种 DSL 格式（自动检测）:

1. 门级 netlist:
   XOR(A, B) = Sum
   AND(A, B) = Carry

2. JSON 模块描述:
   {"id":"u1","type":"FIFO","depth":1024,"width":32,"wr_clk":"clk_a","rd_clk":"clk_b"}

3. 混合: 门级 + JSON (用空行分隔)
   XOR(A, B) = Sum
   {"id":"reg0","type":"REGISTER","width":8,"clk":"clk","d":"data_in","q":"data_out"}
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# ── 模块类型定义 ──────────────────────────────────

_MODULE_DEFS = {
    "REGISTER":  {"shape": "rect", "ports": ["D", "Q", "CLK", "RST", "EN"], "label": "REG"},
    "DFF":       {"shape": "rect", "ports": ["D", "Q", "CLK", "RST"], "label": "DFF"},
    "JKFF":      {"shape": "rect", "ports": ["J", "K", "Q", "CLK", "RST"], "label": "JK"},
    "TFF":       {"shape": "rect", "ports": ["T", "Q", "CLK", "RST"], "label": "TFF"},
    "LATCH":     {"shape": "rect", "ports": ["D", "Q", "EN"], "label": "LAT"},
    "COUNTER":   {"shape": "rect", "ports": ["Q", "CLK", "RST", "EN", "LD"], "label": "CNT"},
    "MUX":       {"shape": "trapezoid", "ports": ["A", "B", "SEL", "Y"], "label": "MUX"},
    "DEMUX":     {"shape": "trapezoid", "ports": ["D", "SEL", "Y0", "Y1"], "label": "DMX"},
    "ENCODER":   {"shape": "rect", "ports": ["D", "Y"], "label": "ENC"},
    "DECODER":   {"shape": "rect", "ports": ["A", "Y"], "label": "DEC"},
    "ALU":       {"shape": "rect", "ports": ["A", "B", "OP", "Y", "FLAGS"], "label": "ALU"},
    "RAM":       {"shape": "rect", "ports": ["ADDR", "DIN", "DOUT", "WE", "CLK"], "label": "RAM"},
    "ROM":       {"shape": "rect", "ports": ["ADDR", "DOUT", "CLK"], "label": "ROM"},
    "FIFO":      {"shape": "rect", "ports": ["DIN", "DOUT", "WR_CLK", "RD_CLK", "WR_EN", "RD_EN", "FULL", "EMPTY"], "label": "FIFO"},
    "FSM":       {"shape": "rect", "ports": ["IN", "OUT", "STATE", "CLK", "RST"], "label": "FSM"},
    "BUS":       {"shape": "rect", "ports": ["M", "S0", "S1", "S2", "S3"], "label": "BUS"},
    "ARBITER":   {"shape": "rect", "ports": ["REQ", "GNT", "CLK"], "label": "ARB"},
    "PIPELINE":  {"shape": "rect", "ports": ["DIN", "DOUT", "CLK", "STALL"], "label": "PIPE"},
    # ── 新增: 算术/比较/特殊模块 ──
    "ADDER":      {"shape": "rect", "ports": ["A", "B", "CIN", "SUM", "COUT"], "label": "ADD"},
    "MULTIPLIER": {"shape": "rect", "ports": ["A", "B", "Y"], "label": "MUL"},
    "COMPARATOR": {"shape": "rect", "ports": ["A", "B", "EQ", "LT", "GT"], "label": "CMP"},
    "SHIFTER":    {"shape": "rect", "ports": ["D", "AMT", "Y", "DIR"], "label": "SHF"},
    "PRIORITY_ENC": {"shape": "rect", "ports": ["D", "Y", "VALID"], "label": "PENC"},
    "PARITY":     {"shape": "rect", "ports": ["D", "ODD", "EVEN"], "label": "PAR"},
    "TRISTATE":   {"shape": "rect", "ports": ["D", "EN", "Y"], "label": "TBUF"},
    "CLOCK_GATE": {"shape": "rect", "ports": ["CLK_IN", "EN", "CLK_OUT"], "label": "CG"},
    "EDGE_DETECT":{"shape": "rect", "ports": ["D", "CLK", "RISE", "FALL"], "label": "EDGE"},
    "PULSE_GEN":  {"shape": "rect", "ports": ["TRIG", "Q", "WIDTH"], "label": "PULSE"},
}

# 端口方向: I=input, O=output, B=bidir
_PORT_DIRS = {
    "CLK": "I", "RST": "I", "EN": "I", "WE": "I", "WR_EN": "I", "RD_EN": "I",
    "STALL": "I", "LD": "I",
    "D": "I", "DIN": "I", "A": "I", "B": "I", "OP": "I", "SEL": "I",
    "ADDR": "I", "REQ": "I", "T": "I", "J": "I", "K": "I",
    "Q": "O", "DOUT": "O", "Y": "O", "Y0": "O", "Y1": "O", "Y2": "O", "Y3": "O",
    "FLAGS": "O", "FULL": "O", "EMPTY": "O", "GNT": "O", "STATE": "O",
    "M": "B", "S0": "B", "S1": "B", "S2": "B", "S3": "B",
    # ── 新增端口方向 ──
    "CIN": "I", "COUT": "O", "SUM": "O",
    "EQ": "O", "LT": "O", "GT": "O",
    "AMT": "I", "DIR": "I",
    "VALID": "O", "ODD": "O", "EVEN": "O",
    "CLK_IN": "I", "CLK_OUT": "O",
    "RISE": "O", "FALL": "O",
    "TRIG": "I", "WIDTH": "I",
}


class LogicSVG:
    TOOLS = [
        ("draw_logic",
         "Draw digital logic diagrams (gate-level or RTL/module-level).\n"
         "Auto-detects format:\n"
         "\n"
         "**Gate netlist:** one gate per line\n"
         "`XOR(A, B) = Sum\nAND(A, B) = Carry`\n"
         "\n"
         "**JSON modules:** one JSON object per line\n"
         '`{"id":"reg0","type":"REGISTER","width":8,"clk":"clk","d":"din","q":"dout"}`\n'
         "\n"
         "**Module types:** REGISTER, DFF, JKFF, TFF, LATCH, COUNTER, "
         "MUX, DEMUX, ENCODER, DECODER, ALU, RAM, ROM, FIFO, FSM, BUS, ARBITER, PIPELINE\n"
         "\n"
         "**Attributes:** width, depth, signed, clock_domain, reset_type, bus_width, delay",
         "draw_logic",
         {"description": {"type": "string",
                          "description":
                          "Gate netlist or JSON modules. Gates: AND/OR/NOT/NAND/NOR/XOR/XNOR/BUF. "
                          "JSON: {'id':'u1','type':'REGISTER','clk':'clk','d':'in','q':'out'}. "
                          "Module types: REGISTER,DFF,JKFF,TFF,LATCH,COUNTER,MUX,DEMUX,"
                          "ENCODER,DECODER,ALU,RAM,ROM,FIFO,FSM,BUS,ARBITER,PIPELINE"},
          "title": {"type": "string", "description": "Diagram title"}},
         ["description"]),
    ]

    # SVG constants
    W, H = 80, 50
    PIN = 10
    IY = 10
    COL_GAP, ROW_GAP = 120, 80
    MOD_W, MOD_H = 120, 80
    COLORS = {"bg": "#1a1a2e", "fg": "#e0e0e0", "grid": "#333",
              "gate_fill": "#2a2a4e", "gate_stroke": "#7c3aed",
              "wire": "#7c3aed", "port_fill": "#0f172a",
              "port_stroke": "#3b82f6", "text": "#e0e0e0"}

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = Path(__file__).parent.parent.parent / "web" / "static" / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def draw_logic(self, description: str, title: str = "") -> str:
        try:
            gates, modules = self._parse_mixed(description)
            if not gates and not modules:
                return "Error: no valid gates or modules found"
            svg = self._render_mixed(gates, modules, title)
        except Exception as e:
            return f"Error drawing logic: {e}"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"logic_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"
        return f"![{title or 'Logic'}]({url})\n{url}"

    # ═══════════ 解析 ═══════════

    @staticmethod
    def _parse_mixed(desc: str) -> tuple[list, list]:
        gates, modules = [], []
        for line in desc.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("{"):
                try:
                    m = json.loads(line)
                    if "type" in m:
                        modules.append(m)
                except json.JSONDecodeError:
                    pass
            else:
                m = re.match(r'(AND|OR|NOT|NAND|NOR|XOR|XNOR|BUF)\(([^)]+)\)\s*=\s*(\w+)', line, re.IGNORECASE)
                if m:
                    gates.append({"type": m.group(1).upper(),
                                  "inputs": [x.strip() for x in m.group(2).split(",")],
                                  "output": m.group(3).strip()})
        return gates, modules

    # ═══════════ 门级 DSL 解析 (保留兼容) ═══════════

    @staticmethod
    def _parse_gate_dsl(desc: str) -> tuple[list, set, set]:
        gates = []; all_in, all_out = set(), set()
        for line in desc.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("{"):
                continue
            m = re.match(r'(AND|OR|NOT|NAND|NOR|XOR|XNOR|BUF)\(([^)]+)\)\s*=\s*(\w+)', line, re.IGNORECASE)
            if not m: continue
            g = {"type": m.group(1).upper(), "inputs": [x.strip() for x in m.group(2).split(",")], "output": m.group(3).strip()}
            gates.append(g); all_out.add(g["output"])
            for inp in g["inputs"]: all_in.add(inp)
        return gates, all_in - all_out, all_out - all_in

    # ═══════════ 混合渲染 ═══════════

    GATE_SHAPES = {"AND":("and",False),"NAND":("and",True),"OR":("or",False),"NOR":("or",True),
                   "XOR":("xor",False),"XNOR":("xor",True),"NOT":("not",True),"BUF":("buf",False)}

    def _render_mixed(self, gates, modules, title=""):
        # 预计算高度
        gate_cols = {}
        gate_max_in_col = 1
        gate_y_end = 50
        if gates:
            # 拓扑排序（直接用已解析的 gates，无需 round-trip）
            produced = {g["output"]: i for i, g in enumerate(gates)}
            depth, visiting = {}, set()
            def get_depth(gi):
                if gi in depth: return depth[gi]
                if gi in visiting: return 1  # 环路检测
                visiting.add(gi)
                d = max((get_depth(produced[i]) for i in gates[gi]["inputs"] if i in produced), default=0) + 1
                visiting.discard(gi); depth[gi] = d; return d
            for i in range(len(gates)): get_depth(i)
            for i, g in enumerate(gates):
                gate_cols.setdefault(depth[i], []).append(i)
            gate_max_in_col = max(len(v) for v in gate_cols.values()) if gate_cols else 1
            gate_y_end = 50 + gate_max_in_col * self.ROW_GAP + 20

        module_rows = (len(modules) + 2) // 3 if modules else 0
        module_height = module_rows * (self.MOD_H + 60)

        svg_w = max(800, (len(gate_cols) + 1) * self.COL_GAP + 160,
                    (((len(modules) + 2) // 3 + 1) * (self.MOD_W + 80) + 160) if modules else 0)
        svg_h = max(400, gate_y_end + module_height + 40)
        svg = ET.Element("svg", {"xmlns":"http://www.w3.org/2000/svg",
                                 "viewBox":f"0 0 {svg_w} {svg_h}",
                                 "width":str(svg_w),"height":str(svg_h)})
        ET.SubElement(svg, "rect", {"width":str(svg_w),"height":str(svg_h),"fill":self.COLORS["bg"]})
        if title:
            ET.SubElement(svg, "text", {"x":str(svg_w//2),"y":"24","text-anchor":"middle",
                                        "fill":self.COLORS["text"],"font-family":"monospace",
                                        "font-size":"14","font-weight":"bold"}).text = title

        y = 50
        placed = {}         # gate_index → (gx, gy)
        gate_outputs = {}   # output_signal_name → (gx+W//2+PIN, gy)

        # ── 门级 ──
        if gates:
            for d in sorted(gate_cols):
                gx = 80 + d * self.COL_GAP
                for ri, gi in enumerate(gate_cols[d]):
                    gy = y + ri * self.ROW_GAP
                    placed[gi] = (gx, gy)

                    # 画门间连线（先画线，门体覆盖其上）
                    nin = len(gates[gi]["inputs"])
                    for ii, inp in enumerate(gates[gi]["inputs"]):
                        src_idx = produced.get(inp)
                        if src_idx is not None and src_idx in placed:
                            src = placed[src_idx]
                            iy_off = (ii - (nin - 1) / 2) * self.IY
                            self._draw_wire(svg,
                                            src[0] + self.W // 2 + self.PIN, src[1],
                                            gx - self.W // 2 - self.PIN, gy + iy_off)

                    # 画门体
                    self._draw_gate(svg, gx, gy, gates[gi]["type"], "")

                    # 记录输出位置（用于门→模块连线）
                    gate_outputs[gates[gi]["output"]] = (gx + self.W // 2 + self.PIN, gy)

            # ── 主 I/O 标签 ──
            if gates:
                all_inputs = set()
                produced = {g["output"] for g in gates}
                for g in gates:
                    for inp in g["inputs"]:
                        if inp not in produced:
                            all_inputs.add(inp)
                io_y = 50
                for inp_sig in sorted(all_inputs):
                    ET.SubElement(svg, "text", {
                        "x": "16", "y": str(io_y), "text-anchor": "start",
                        "fill": "#3b82f6", "font-family": "monospace", "font-size": "9",
                    }).text = inp_sig
                    io_y += 18
                # 主输出信号
                all_outputs = set()
                for g in gates:
                    used_as_input = any(g["output"] in g2["inputs"] for g2 in gates if g2 != g)
                    if not used_as_input:
                        all_outputs.add(g["output"])
                if all_outputs:
                    out_x = max(v[0] for v in gate_outputs.values()) + 50 if gate_outputs else svg_w - 60
                    io_y = 50
                    for out_sig in sorted(all_outputs):
                        if out_sig in gate_outputs:
                            ox, oy = gate_outputs[out_sig]
                            ET.SubElement(svg, "text", {
                                "x": str(ox + self.W//2 + self.PIN + 8), "y": str(oy + 4),
                                "text-anchor": "start",
                                "fill": "#10b981", "font-family": "monospace", "font-size": "9",
                            }).text = out_sig

            y = gate_y_end

        # ── 门→模块连线 ──
        module_pin_positions = {}  # (mi, port_name) → (px, py, pdir)
        for mi, mod in enumerate(modules):
            mtype = mod.get("type", "REGISTER").upper()
            mid = mod.get("id", f"u{mi}")
            mdef = _MODULE_DEFS.get(mtype, {"shape": "rect", "ports": [], "label": mtype[:3]})
            ports = mdef.get("ports", [])
            mx = 80 + (mi % 3) * (self.MOD_W + 80)
            my = y + (mi // 3) * (self.MOD_H + 60)
            rx = mx - self.MOD_W // 2
            ry = my - self.MOD_H // 2
            h = self.MOD_H

            for pi, port in enumerate(ports):
                pdir = _PORT_DIRS.get(port, "I")
                py = ry + (pi + 1) * h / (len(ports) + 1)
                if pdir == "I":
                    module_pin_positions[(mi, port)] = (rx, py, pdir)
                elif pdir == "O":
                    module_pin_positions[(mi, port)] = (rx + self.MOD_W, py, pdir)
                else:
                    module_pin_positions[(mi, port)] = (rx, py, pdir)

        # 画门→模块连线
        for mi, mod in enumerate(modules):
            mtype = mod.get("type", "REGISTER").upper()
            mdef = _MODULE_DEFS.get(mtype, {"shape": "rect", "ports": [], "label": mtype[:3]})
            ports = mdef.get("ports", [])
            for port in ports:
                pdir = _PORT_DIRS.get(port, "I")
                if pdir != "I":
                    continue
                # 检查端口值是否匹配某个门输出
                signal = mod.get(port.lower()) or mod.get(port)
                if signal and signal in gate_outputs:
                    if (mi, port) in module_pin_positions:
                        gx_out, gy_out = gate_outputs[signal]
                        px, py, _ = module_pin_positions[(mi, port)]
                        # 直角走线：门输出 → 模块输入
                        mid_x = (gx_out + px) / 2
                        d = f"M{gx_out},{gy_out} L{mid_x},{gy_out} L{mid_x},{py} L{px},{py}"
                        ET.SubElement(svg, "path", {
                            "d": d, "fill": "none", "stroke": self.COLORS["wire"],
                            "stroke-width": "1.5", "stroke-linejoin": "round",
                        })

        # ── 模块→模块连线 ──
        if len(modules) >= 2:
            for mi, mod_src in enumerate(modules):
                mtype_src = mod_src.get("type", "").upper()
                for port_src in _MODULE_DEFS.get(mtype_src, {}).get("ports", []):
                    pdir_src = _PORT_DIRS.get(port_src, "I")
                    if pdir_src != "O":
                        continue
                    signal = (mod_src.get(port_src.lower()) or
                              mod_src.get(port_src))
                    if not signal:
                        continue
                    for mj, mod_dst in enumerate(modules):
                        if mi == mj:
                            continue
                        for port_dst in _MODULE_DEFS.get(
                                mod_dst.get("type", "").upper(), {}
                            ).get("ports", []):
                            if _PORT_DIRS.get(port_dst, "I") != "I":
                                continue
                            if (mod_dst.get(port_dst.lower()) or
                                mod_dst.get(port_dst)) == signal:
                                if ((mi, port_src) in module_pin_positions and
                                    (mj, port_dst) in module_pin_positions):
                                    sx, sy, _ = module_pin_positions[(mi, port_src)]
                                    dx, dy, _ = module_pin_positions[(mj, port_dst)]
                                    mid = (sx + dx) / 2
                                    d = f"M{sx},{sy} L{mid},{sy} L{mid},{dy} L{dx},{dy}"
                                    ET.SubElement(svg, "path", {
                                        "d": d, "fill": "none",
                                        "stroke": self.COLORS["wire"],
                                        "stroke-width": "1.5",
                                        "stroke-linejoin": "round",
                                    })

        # ── 模块级 ──
        for mi, mod in enumerate(modules):
            mtype = mod.get("type", "REGISTER").upper()
            mid = mod.get("id", f"u{mi}")
            mx = 80 + (mi % 3) * (self.MOD_W + 80)
            my = y + (mi // 3) * (self.MOD_H + 60)
            self._draw_module(svg, mx, my, mtype, mid, mod)

        return ET.tostring(svg, encoding="unicode")

    # ═══════════ 模块绘制 ═══════════

    def _draw_module(self, svg, x, y, mtype, mid, attrs):
        mdef = _MODULE_DEFS.get(mtype, {"shape":"rect","ports":[],"label":mtype[:3]})
        ports = mdef.get("ports", [])
        w, h = self.MOD_W, self.MOD_H
        rx = x - w//2; ry = y - h//2

        # 模块主体
        ET.SubElement(svg, "rect", {"x":str(rx),"y":str(ry),"width":str(w),"height":str(h),
                                     "rx":"6","fill":self.COLORS["gate_fill"],
                                     "stroke":self.COLORS["gate_stroke"],"stroke-width":"1.5"})
        ET.SubElement(svg, "text", {"x":str(x),"y":str(y-8),"text-anchor":"middle",
                                     "fill":self.COLORS["text"],"font-family":"monospace","font-size":"11",
                                     "font-weight":"bold"}).text = f"{mdef['label']}"
        ET.SubElement(svg, "text", {"x":str(x),"y":str(y+6),"text-anchor":"middle",
                                     "fill":self.COLORS["port_stroke"],"font-family":"monospace","font-size":"8"
                                     }).text = mid

        # 显示属性
        attr_text = []
        if "width" in attrs: attr_text.append(f"W={attrs['width']}")
        if "depth" in attrs: attr_text.append(f"D={attrs['depth']}")
        if "signed" in attrs: attr_text.append("signed" if attrs["signed"] else "unsigned")
        if "reset_type" in attrs: attr_text.append(attrs["reset_type"])
        if "clock_domain" in attrs: attr_text.append(f"@{attrs['clock_domain']}")
        if attr_text:
            ET.SubElement(svg, "text", {"x":str(x),"y":str(y+h//2-4),"text-anchor":"middle",
                                         "fill":self.COLORS["text"],"font-family":"monospace","font-size":"7"
                                         }).text = ", ".join(attr_text[:3])

        # 端口
        for pi, port in enumerate(ports):
            py = ry + (pi + 1) * h / (len(ports) + 1)
            pdir = _PORT_DIRS.get(port, "I")
            color = "#3b82f6" if pdir == "I" else "#10b981" if pdir == "O" else "#f59e0b"
            # 引脚短线
            if pdir == "I":
                self._line(svg, rx-8, py, rx, py)
            elif pdir == "O":
                self._line(svg, rx+w, py, rx+w+8, py)
            else:
                self._line(svg, rx-6, py, rx, py)
            # 端口名
            align = "end" if pdir == "I" else "start" if pdir == "O" else "end"
            tx = rx - 10 if pdir == "I" else rx + w + 10 if pdir == "O" else rx - 8
            ET.SubElement(svg, "text", {"x":str(tx),"y":str(py+3),"text-anchor":align,
                                         "fill":color,"font-family":"monospace","font-size":"7"
                                         }).text = port

    # ═══════════ 门绘制 (保留) ═══════════

    def _draw_gate(self, svg, cx, cy, gtype, label=""):
        shape, bubble = self.GATE_SHAPES.get(gtype, ("and", False))
        x, y = cx - self.W//2, cy - self.H//2
        g = ET.SubElement(svg, "g")

        if shape == "and":
            d = f"M{x},{y+self.H} L{x},{y} A{self.W},{self.H//2} 0 0,1 {x},{y+self.H} Z"
            self._add_path(g, d)
        elif shape == "or":
            r = self.W * 0.5
            d = (f"M{x},{y} Q{x+self.W*0.5},{y+self.H*0.15} {x+self.W},{cy} "
                 f"Q{x+self.W*0.5},{y+self.H*0.85} {x},{y+self.H} Q{x-r},{cy} {x},{y} Z")
            self._add_path(g, d)
        elif shape == "xor":
            r = self.W * 0.5
            d = (f"M{x},{y} Q{x+self.W*0.4},{y+self.H*0.1} {x+self.W},{cy} "
                 f"Q{x+self.W*0.4},{y+self.H*0.9} {x},{y+self.H} Q{x-r},{cy} {x},{y} Z")
            self._add_path(g, d)
            ed = f"M{x-self.W*0.02},{y} Q{x-r*0.7},{cy} {x-self.W*0.02},{y+self.H}"
            ET.SubElement(g, "path", {"d":ed,"fill":"none","stroke":self.COLORS["gate_stroke"],"stroke-width":"1.2"})
        elif shape in ("not","buf"):
            d = f"M{x},{y} L{x},{y+self.H} L{x+self.W*0.7},{cy} Z"
            self._add_path(g, d)

        if bubble:
            bx = x + self.W + 6 if shape != "not" else x + self.W * 0.8
            ET.SubElement(g, "circle", {"cx":str(bx),"cy":str(cy),"r":"4",
                                         "fill":"none","stroke":self.COLORS["gate_stroke"],"stroke-width":"1.2"})
        ET.SubElement(g, "text", {"x":str(cx),"y":str(cy+2),"text-anchor":"middle",
                                   "fill":self.COLORS["text"],"font-family":"monospace","font-size":"8",
                                   "dy":"0.3em"}).text = label or gtype[:3]

    # ═══════════ 辅助 ═══════════

    def _add_path(self, g, d):
        ET.SubElement(g, "path", {"d":d,"fill":self.COLORS["gate_fill"],
                                   "stroke":self.COLORS["gate_stroke"],"stroke-width":"1.5",
                                   "stroke-linejoin":"round","stroke-linecap":"round"})

    def _draw_wire(self, svg, x1, y1, x2, y2):
        mid = (x1+x2)/2
        d = f"M{x1},{y1} L{mid},{y1} L{mid},{y2} L{x2},{y2}"
        ET.SubElement(svg, "path", {"d":d,"fill":"none","stroke":self.COLORS["wire"],
                                     "stroke-width":"1.5","stroke-linejoin":"round"})

    @staticmethod
    def _line(svg, x1, y1, x2, y2):
        ET.SubElement(svg, "line", {"x1":str(x1),"y1":str(y1),"x2":str(x2),"y2":str(y2),
                                     "stroke":"#7c3aed","stroke-width":"1.2","stroke-linecap":"round"})
