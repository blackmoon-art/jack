"""电路图工具 — 基于 schemdraw 渲染专业电路图。

三个独立工具:
  draw_digital — 数字逻辑电路 (门电路、触发器、同步器、FIFO 等)
  draw_analog  — 模拟电路 (滤波器、放大器、运放电路等)
  draw_block   — 系统框图 (RF 信号链、混合信号架构等)

支持语法:
  - 串联: A -> B -> C
  - 并联: [branch1, branch2]  (分支在两端汇合)
  - 节点命名: component(value) as NODE_NAME
  - 多链: chain1 ; chain2  (用分号分隔独立链路)
  - 节点引用: NODE_NAME -> component  (从已命名节点继续画)
  - 锚点连接: NODE_NAME.emitter -> component  (从晶体管的发射极继续)
            component@anchor  (以指定锚点连接新元件)
  - 方向控制: up, down  (改变后续元件绘制方向, right 恢复默认)
  - 连接线: connect(N1, N2)  (两个命名节点间画连接线)
  - 编号元件: r1→resistor, c2→capacitor, led1→LED (自动匹配)

元件锚点:
  npn/pnp/transistor: base, emitter, collector
  opamp: in1, in2, out, vdd, vss
  所有元件: start, end
"""

import logging
import math
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.circuit")


# ── IEEE 逻辑门形状工厂 ────────────────────────────────

def _make_ieee_gate(gate_type: str, label: str, elm_module):
    """用 schemdraw Segment 构建标准 IEEE 逻辑门形状。

    返回一个已配置好形状和锚点的 schemdraw Element。
    锚点: in1, in2 (输入), out (输出), start (左中点), end (右中点)
    """
    from schemdraw.segments import Segment, SegmentArc, SegmentCircle

    w, h = 1.2, 0.8
    gate = elm_module.Element()

    # ── AND/NAND: D-shape ──
    if gate_type in ("and_gate", "nand_gate"):
        gate.segments.append(Segment([(0, h/2), (0, -h/2)]))
        gate.segments.append(SegmentArc((0, 0), width=w*0.85, height=h,
                                         theta1=-90, theta2=90))
        gate.anchors['in1'] = (0, h/4)
        gate.anchors['in2'] = (0, -h/4)
        bx, by = w * 0.7, 0

    # ── OR/NOR/XOR/XNOR: shield shape ──
    elif gate_type in ("or_gate", "nor_gate", "xor_gate", "xnor_gate"):
        n = 12
        pts_left, pts_right = [], []
        for i in range(n + 1):
            t = -0.5 + i / n
            x = -0.15 + 0.45 * (1 - (2*t)**2)
            pts_left.append((x, t * h))
            x2 = 0.55 - 0.35 * (1 - (2*t)**2)
            pts_right.append((x2, t * h))
        gate.segments.append(Segment(pts_left))
        gate.segments.append(Segment(list(reversed(pts_right))))
        # XOR: extra curve on input side
        if gate_type in ("xor_gate", "xnor_gate"):
            pts_extra = [(-0.25 + 0.15 * (1-(2*t)**2), t * h)
                         for t in [j/12 - 0.5 for j in range(13)]]
            gate.segments.append(Segment(pts_extra))
        gate.anchors['in1'] = (-0.25, h/4)
        gate.anchors['in2'] = (-0.25, -h/4)
        bx, by = 0.65, 0

    # ── NOT/Buffer: triangle ──
    elif gate_type in ("not_gate", "buffer"):
        gate.segments.append(Segment([(-w/3, h/2), (-w/3, -h/2), (w/3, 0),
                                       (-w/3, h/2)]))
        gate.anchors['in1'] = (-w/3, 0)
        gate.anchors['in2'] = (-w/3, 0)
        bx, by = w/3, 0

    else:
        gate.segments.append(Segment([(0, h/2), (0, -h/2), (w, -h/2), (w, h/2), (0, h/2)]))
        gate.anchors['in1'] = (0, h/4)
        gate.anchors['in2'] = (0, -h/4)
        bx, by = w, 0

    # 输出气泡 (NAND/NOR/XNOR/NOT)
    if gate_type in ("nand_gate", "nor_gate", "xnor_gate", "not_gate"):
        gate.segments.append(SegmentCircle((bx + 0.12, by), 0.08))
        gate.anchors['out'] = (bx + 0.2, by)
    else:
        gate.anchors['out'] = (bx, by)

    gate.anchors['start'] = (gate.anchors.get('in1', (-w/3, 0))[0], 0)
    gate.anchors['end'] = gate.anchors['out']
    gate.params['label'] = label
    gate.params['lblloc'] = 'center'
    return gate


# ── 元件分组 ──────────────────────────────────────────

_COMMON_COMPS = ("line", "wire", "open", "dot")

_DIGITAL_COMPS = (
    # 逻辑门
    "and_gate", "or_gate", "not_gate", "nand_gate", "nor_gate",
    "xor_gate", "xnor_gate", "buffer",
    # 触发器 / 时序
    "dff", "jkff", "latch", "register", "shift_reg",
    # 功能块
    "ram", "counter", "comparator", "gray_code",
    "mux", "decoder", "encoder", "tristate", "alu",
    # 端口
    "port", "terminal",
) + _COMMON_COMPS

_ANALOG_COMPS = (
    # 电源 / 信号源
    "ac", "v", "battery", "source", "signal",
    "isource", "current_source",
    # 无源元件
    "resistor", "r", "capacitor", "c", "inductor", "l",
    "diode", "d", "led",
    # 有源元件
    "opamp", "transistor", "npn", "pnp",
    # 开关 / 负载
    "switch", "spst", "ground", "gnd",
    "fuse", "lamp", "motor", "speaker", "microphone", "antenna",
    # 端口
    "port", "terminal", "open",
) + _COMMON_COMPS

_BLOCK_COMPS = (
    # RF / 信号链
    "mixer", "lna", "amp", "amplifier",
    "adc", "dac", "oscillator", "lo",
    "filter_box", "filter",
    "combiner", "splitter", "rf",
    # 通用
    "block", "port", "terminal",
    # 基础元件 (框图也可用)
    "resistor", "r", "capacitor", "c", "inductor", "l",
    "opamp", "ground", "gnd", "ac", "v", "battery",
    # 数字 (框图也可用)
    "and_gate", "or_gate", "not_gate", "nand_gate", "nor_gate",
    "xor_gate", "xnor_gate", "buffer", "dff", "jkff",
    "ram", "counter", "comparator", "gray_code",
    "mux", "decoder", "encoder", "latch", "register",
    "tristate", "alu", "shift_reg", "npn", "pnp",
    "diode", "led", "isource", "antenna",
    "fuse", "lamp", "motor", "speaker", "microphone",
    "switch", "spst",
) + _COMMON_COMPS


def _names_str(comps):
    return ", ".join(sorted(set(comps)))

_DIGITAL_NAMES_STR = _names_str(_DIGITAL_COMPS)
_ANALOG_NAMES_STR = _names_str(_ANALOG_COMPS)
_BLOCK_NAMES_STR = _names_str(_BLOCK_COMPS)

# ── 共享常量 ──────────────────────────────────────────
_DIRECTIONS = {"up", "down", "left", "right"}
_BLOCK_FACTORY = "__block__"
_GATE_FACTORY = "__gate__"


class _AnchorRef:
    """让 Anchor 伪装成有 .end 的元素。"""
    def __init__(self, anchor):
        self.end = anchor


class Circuit:
    TOOLS = [
        ("draw_digital",
         "Draw digital logic circuit diagrams. "
         "For: logic gates, flip-flops, synchronizers, FIFO, counters, adders, "
         "multiplexers, decoders, state machines.\n"
         "\n"
         "**Valid components:** " + _DIGITAL_NAMES_STR + "\n"
         "\n"
         "**Syntax:** Series `A->B->C`, Parallel `[b1,b2]`, "
         "Named nodes `comp(val) as N1`, Multi-chain `;`, "
         "Connect `connect(N1,N2)`.\n"
         "\n"
         "**2-stage synchronizer:**\n"
         "`port(async_in) -> dff(FF1) -> dff(FF2) -> port(synced) ; "
         "port(clk) -> dff(FF1) ; port(clk) -> dff(FF2)`\n"
         "\n"
         "**Half-adder:**\n"
         "`port(A) -> xor_gate as g1 ; port(B) -> g1 ; "
         "g1 -> port(Sum) ; port(A) -> and_gate as g2 ; port(B) -> g2 ; "
         "g2 -> port(Carry)`",
         "draw_digital",
         {"description": {"type": "string",
                          "description":
                          "Digital circuit. Valid names: " + _DIGITAL_NAMES_STR + ". "
                          "Synchronizer: 'port(in) -> dff(FF1) -> dff(FF2) -> port(out)'"},
          "title": {"type": "string", "description": "Circuit title"}},
         ["description"]),

        ("draw_analog",
         "Draw analog circuit diagrams. "
         "For: filters (RC/LC/RLC), amplifiers (op-amp, transistor), "
         "differential pairs, power supplies, sensor circuits.\n"
         "\n"
         "**Valid components:** " + _ANALOG_NAMES_STR + "\n"
         "Numbered variants (r1, c2, led1, etc.) auto-match to base names.\n"
         "\n"
         "**Syntax:** Series `A->B->C`, Parallel `[b1,b2]`, "
         "Named nodes `comp(val) as N1`, Multi-chain `;`, "
         "Anchor refs `N1.emitter->` or `comp@base`, "
         "Directions `up/down`, Connect `connect(N1,N2)`.\n"
         "npn anchors: base/emitter/collector; opamp: in1/in2/out.\n"
         "\n"
         "**RC low-pass filter:**\n"
         "`ac(Vin) -> resistor(1k) as n1 -> line -> open(Vout) ; "
         "n1 -> down -> capacitor(10n) -> ground`\n"
         "  (signal path horizontal right, filter branch down to ground)\n"
         "\n"
         "**RC high-pass filter:**\n"
         "`ac(Vin) -> capacitor(10n) as n1 -> line -> open(Vout) ; "
         "n1 -> down -> resistor(1k) -> ground`\n"
         "\n"
         "**Active filter (Sallen-Key low-pass):**\n"
         "`ac(Vin) -> resistor(R1) as n1 -> resistor(R2) as n2 ; "
         "n2 -> opamp(OP1)@in1 ; opamp(OP1)@out as op_out ; "
         "connect(op_out, OP1.in2) ; "
         "n1 -> down -> capacitor(C1) as c1_end ; connect(op_out, c1_end) ; "
         "n2 -> down -> capacitor(C2) -> ground`\n"
         "  (signal path top row, R1-R2 needs opamp as follower, C1 feedback from output to R1-R2, C2 to ground)\n"
         "\n"
         "**Differential amplifier:**\n"
         "`ac(Vin+) -> npn(Q1)@base as q1 ; ac(Vin-) -> npn(Q2)@base as q2 ; "
         "q1.emitter -> line -> q2.emitter ; "
         "q1.emitter -> down -> isource(1mA) -> ground ; "
         "q1.collector -> up -> resistor(10k) -> line -> v(VCC) ; "
         "q2.collector -> up -> resistor(10k) -> line -> v(VCC)`",
         "draw_analog",
         {"description": {"type": "string",
                          "description":
                          "Analog circuit. Valid names: " + _ANALOG_NAMES_STR + ". "
                          "Filter: 'ac(Vin)->r(1k) as n1->line->open(Vout) ; n1->down->c(10n)->gnd' (signal right, cap down to GND)"},
          "title": {"type": "string", "description": "Circuit title"}},
         ["description"]),

        ("draw_block",
         "Draw system block diagrams and signal-processing chain diagrams. "
         "For: RF signal chains, mixed-signal architectures, communication systems, "
         "radar IF processing, audio processing pipelines.\n"
         "\n"
         "**Valid components:** " + _BLOCK_NAMES_STR + "\n"
         "Block elements (LNA, mixer, ADC, etc.) render as labeled boxes.\n"
         "Also includes all basic analog and digital components.\n"
         "\n"
         "**Syntax:** Series `A->B->C`, Multi-chain `;`, Directions `up/down`.\n"
         "\n"
         "**FMCW radar IF chain:**\n"
         "`rf(RF_in) -> lna(LNA) -> mixer as m1 ; lo(f0) -> m1 ; "
         "m1 -> amp(IF_Amp) -> filter_box(LPF) -> adc(ADC) -> port(DSP)`\n"
         "\n"
         "**Async FIFO:**\n"
         "`port(wr_clk) -> counter(WrPtr) -> gray_code as WrGray ; "
         "port(rd_clk) -> counter(RdPtr) -> gray_code as RdGray ; "
         "port(wr_data) -> ram(DPRAM) ; "
         "WrGray -> dff(Sync1) -> dff(Sync2) as synced ; "
         "synced -> comparator(CMP) -> port(empty)`",
         "draw_block",
         {"description": {"type": "string",
                          "description":
                          "Block diagram. Valid names: " + _BLOCK_NAMES_STR + ". "
                          "RF chain: 'rf(In)->lna->mixer as m1 ; lo(f0)->m1 ; m1->filter_box->adc->port(Out)'"},
          "title": {"type": "string", "description": "Diagram title"}},
         ["description"]),
    ]

    def __init__(self, work_dir: str, charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            web_static = Path(__file__).parent.parent.parent / "web" / "static"
            self.charts_dir = web_static / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    # ── 三个公开入口 ──────────────────────────────────

    def draw_digital(self, description: str, title: str = "") -> str:
        """绘制数字逻辑电路。"""
        return self._draw(description, title, "digital")

    def draw_analog(self, description: str, title: str = "") -> str:
        """绘制模拟电路。"""
        return self._draw(description, title, "analog")

    def draw_block(self, description: str, title: str = "") -> str:
        """绘制系统框图。"""
        return self._draw(description, title, "block")

    # ── 元件名解析 ──────────────────────────────────────

    @staticmethod
    def _resolve_comp_name(name: str, comp_map: dict):
        if name in comp_map:
            return comp_map[name]
        stripped = name.rstrip("0123456789")
        if stripped and stripped != name and stripped in comp_map:
            return comp_map[stripped]
        return None, {}

    @staticmethod
    def _parse_comp(comp_str: str) -> tuple:
        node_name = None
        comp_anchor = None
        c = comp_str.strip()
        if " as " in c:
            c, node_name = c.rsplit(" as ", 1)
            c, node_name = c.strip(), node_name.strip()
        if "@" in c:
            c, comp_anchor = c.rsplit("@", 1)
            c, comp_anchor = c.strip(), comp_anchor.strip()
        if "(" in c and ")" in c:
            n = c.split("(")[0].strip().lower()
            v = c[c.index("(") + 1:c.index(")")].strip()
            return n, v, c, node_name, comp_anchor
        return c.lower(), "", c, node_name, comp_anchor

    # ── 核心渲染引擎 ──────────────────────────────────

    def _draw(self, description: str, title: str, comp_set: str) -> str:
        """统一渲染引擎，按 comp_set 过滤可用元件。"""
        import schemdraw
        import schemdraw.elements as elm
        from schemdraw import Drawing

        # 选择元件集
        if comp_set == "digital":
            allowed = set(_DIGITAL_COMPS)
            valid_names_str = _DIGITAL_NAMES_STR
        elif comp_set == "analog":
            allowed = set(_ANALOG_COMPS)
            valid_names_str = _ANALOG_NAMES_STR
        else:
            allowed = set(_BLOCK_COMPS)
            valid_names_str = _BLOCK_NAMES_STR

        try:
            d = Drawing(show=False)
            if title:
                d.config(fontsize=14)

            # 元件映射表（所有类型共用）
            full_map = {
                "battery": (elm.Battery, {}), "v": (elm.SourceV, {}),
                "source": (elm.SourceV, {}), "signal": (elm.SourceV, {}),
                "ac": (self._get_ac_source(elm), {}),
                "resistor": (elm.Resistor, {}), "r": (elm.Resistor, {}),
                "capacitor": (elm.Capacitor, {}), "c": (elm.Capacitor, {}),
                "inductor": (elm.Inductor, {}), "l": (elm.Inductor, {}),
                "diode": (elm.Diode, {}), "d": (elm.Diode, {}),
                "led": (elm.LED, {}), "switch": (elm.Switch, {}),
                "spst": (elm.Switch, {}), "ground": (elm.Ground, {}),
                "gnd": (elm.Ground, {}), "antenna": (elm.Antenna, {}),
                "opamp": (elm.Opamp, {}),
                "transistor": (elm.BjtNpn, {}), "npn": (elm.BjtNpn, {}),
                "pnp": (elm.BjtPnp, {}), "isource": (elm.SourceI, {}),
                "current_source": (elm.SourceI, {}),
                "fuse": (elm.Fuse, {}), "lamp": (elm.Lamp, {}),
                "motor": (elm.Motor, {}), "speaker": (elm.Speaker, {}),
                "microphone": (elm.Mic, {}),
                "line": (elm.Line, {}), "wire": (elm.Line, {}),
                "open": (elm.Dot, {}), "dot": (elm.Dot, {}),
                # 框图/数字元件
                "mixer": (_BLOCK_FACTORY, {}), "lna": (_BLOCK_FACTORY, {}),
                "amp": (_BLOCK_FACTORY, {}), "amplifier": (_BLOCK_FACTORY, {}),
                "adc": (_BLOCK_FACTORY, {}), "dac": (_BLOCK_FACTORY, {}),
                "oscillator": (_BLOCK_FACTORY, {}), "lo": (_BLOCK_FACTORY, {}),
                "filter_box": (_BLOCK_FACTORY, {}), "filter": (_BLOCK_FACTORY, {}),
                "block": (_BLOCK_FACTORY, {}), "port": (_BLOCK_FACTORY, {}),
                "terminal": (_BLOCK_FACTORY, {}), "combiner": (_BLOCK_FACTORY, {}),
                "splitter": (_BLOCK_FACTORY, {}), "rf": (_BLOCK_FACTORY, {}),
                "and_gate": (_GATE_FACTORY, {}), "or_gate": (_GATE_FACTORY, {}),
                "not_gate": (_GATE_FACTORY, {}), "nand_gate": (_GATE_FACTORY, {}),
                "nor_gate": (_GATE_FACTORY, {}), "xor_gate": (_GATE_FACTORY, {}),
                "xnor_gate": (_GATE_FACTORY, {}), "buffer": (_GATE_FACTORY, {}),
                "dff": (_BLOCK_FACTORY, {}), "jkff": (_BLOCK_FACTORY, {}),
                "ram": (_BLOCK_FACTORY, {}), "counter": (_BLOCK_FACTORY, {}),
                "comparator": (_BLOCK_FACTORY, {}), "gray_code": (_BLOCK_FACTORY, {}),
                "mux": (_BLOCK_FACTORY, {}), "decoder": (_BLOCK_FACTORY, {}),
                "encoder": (_BLOCK_FACTORY, {}), "latch": (_BLOCK_FACTORY, {}),
                "register": (_BLOCK_FACTORY, {}), "tristate": (_BLOCK_FACTORY, {}),
                "alu": (_BLOCK_FACTORY, {}), "shift_reg": (_BLOCK_FACTORY, {}),
            }

            # 过滤：只保留 allowed 集中的元件
            comp_map = {k: v for k, v in full_map.items() if k in allowed}

            named: dict[str, object] = {}
            last = None
            direction = "right"
            _input_usage: dict[int, int] = {}  # 多输入元件的输入追踪

            chains = self._split_chains(description)
            if not chains:
                return "Error: no components in circuit description"

            for chain_desc in chains:
                # 每条新链重置方向为 right，避免继承上一条链的方向
                # （上一条链从 down 结束时，新链不应从 down 开始）
                last, direction = self._draw_chain(
                    chain_desc, comp_map, named, last, d, elm, "right",
                    valid_names_str, _input_usage)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            prefix = {"digital": "digital", "analog": "analog", "block": "block"}
            filename = f"{prefix.get(comp_set, 'circuit')}_{ts}.png"
            filepath = self.charts_dir / filename
            d.save(str(filepath))
            logger.info(f"Circuit saved: {filename}")

            img_url = f"/charts/{filename}"
            title_alt = title or comp_set.title()
            return f"![{title_alt}]({img_url})\n{img_url}"

        except ImportError:
            return "Error: schemdraw is not installed. Run: pip install schemdraw"
        except Exception as e:
            logger.exception(f"Circuit drawing failed: {e}")
            return f"Error drawing circuit: {e}"

    # ── 链拆分 ────────────────────────────────────────

    @staticmethod
    def _split_chains(description: str) -> list:
        chains = []
        current = ""
        depth = 0
        for ch in description:
            if ch == ";" and depth == 0:
                c = current.strip()
                if c: chains.append(c)
                current = ""
            else:
                if ch == "[": depth += 1
                elif ch == "]": depth = max(0, depth - 1)
                current += ch
        c = current.strip()
        if c: chains.append(c)
        return chains

    # ── 锚点引用解析 ──────────────────────────────────

    @staticmethod
    def _resolve_named_anchor(token: str, named: dict):
        token = token.strip()
        if "." in token:
            base, anchor_name = token.split(".", 1)
            base, anchor_name = base.strip(), anchor_name.strip()
            if base in named:
                try: return getattr(named[base], anchor_name)
                except AttributeError: return Circuit._get_anchor(named[base])
        if token in named:
            return Circuit._get_anchor(named[token])
        return None

    @staticmethod
    def _parse_node_ref(first_token: str, named: dict):
        token = first_token.rstrip("->").strip()
        if not token: return None, None
        if "." in token:
            node_name, anchor_name = token.split(".", 1)
            if node_name in named:
                elem = named[node_name]
                try: anchor = getattr(elem, anchor_name)
                except AttributeError: anchor = elem.end
                return _AnchorRef(anchor), token
        elif token in named:
            return named[token], token
        return None, None

    # ── 单链绘制 ──────────────────────────────────────

    def _try_connect_node(self, part_data: str, named: dict, last, d, elm,
                          _input_usage: dict | None = None) -> tuple:
        """如果是节点引用，画线连接到该节点。返回 (new_last, was_handled)。

        对于多输入元件（逻辑门等），自动追踪输入使用情况：
          第一次连接 → in1, 第二次 → in2, 超出则回退到 start。
        """
        if _input_usage is None:
            _input_usage = {}

        token = part_data.strip()

        def _pick_anchor(elem):
            """选择下一个可用的输入锚点（优先 in1→in2→start→_get_anchor）。"""
            key = id(elem)
            used = _input_usage.get(key, 0)
            # 尝试 in1, in2, in3...
            for n in range(1, used + 2):
                anchor_name = f"in{n}"
                try:
                    anchor = getattr(elem, anchor_name)
                    _input_usage[key] = n
                    return anchor
                except AttributeError:
                    break
            # 回退：使用 start（通用输入侧）或 _get_anchor
            try:
                return elem.start
            except AttributeError:
                return self._get_anchor(elem)

        if token in named:
            elem = named[token]
            in_anchor = _pick_anchor(elem)
            if last is not None:
                d.add(elm.Line().at(self._get_anchor(last)).to(in_anchor))
            # 后续元件应从该元件的输出侧开始，而非输入侧
            out_anchor = self._get_output_anchor(elem)
            return _AnchorRef(out_anchor), True

        if "." in token:
            base, anchor_name = token.split(".", 1)
            base, anchor_name = base.strip(), anchor_name.strip()
            if base in named:
                elem = named[base]
                try: anchor = getattr(elem, anchor_name)
                except AttributeError: anchor = self._get_anchor(elem)
                if last is not None:
                    d.add(elm.Line().at(self._get_anchor(last)).to(anchor))
                # 显式锚点引用，用该锚点作为出点
                out_anchor = self._get_output_anchor(elem)
                return _AnchorRef(out_anchor), True
        return None, False

    def _draw_chain(self, chain_desc: str, comp_map: dict,
                    named: dict, last, d, elm, direction: str,
                    valid_names_str: str, _input_usage: dict | None = None):
        chain_desc = chain_desc.strip()

        # connect(N1, N2): 两个命名节点间画连接线
        if chain_desc.startswith("connect("):
            m = re.match(r'connect\(\s*(\S+?)\s*,\s*(\S+?)\s*\)', chain_desc)
            if m:
                a_str, b_str = m.group(1), m.group(2)
                anchor_a = self._resolve_named_anchor(a_str, named)
                anchor_b = self._resolve_named_anchor(b_str, named)
                if anchor_a is not None and anchor_b is not None:
                    d.add(elm.Line().at(anchor_a).to(anchor_b))
                return last, direction

        # 链首：命名节点引用（可带锚点）
        # 先按 -> 或空格分割取第一个 token，处理无空格箭头如 q1.emitter->line
        first_token = chain_desc.split("->")[0].strip().split()[0] if chain_desc else ""
        ref_obj, ref_token = self._parse_node_ref(first_token, named)
        if ref_obj is not None:
            last = ref_obj
            chain_desc = chain_desc[len(ref_token):].strip()
            if chain_desc.startswith("->"): chain_desc = chain_desc[2:].strip()
        else:
            # 链首不是节点引用 → 独立信号路径，重置 last
            last = None

        if not chain_desc: return last, direction

        parts = self._parse_parts(chain_desc)
        if not parts: return last, direction

        for part_type, part_data in parts:
            if part_type == "direction":
                direction = part_data
            elif part_type == "series":
                ref_last, handled = self._try_connect_node(
                    part_data, named, last, d, elm, _input_usage)
                if handled:
                    last = ref_last
                    continue
                n, v, lbl, node_name, comp_anchor = self._parse_comp(part_data)
                el_obj = self._place_component(
                    n, v, lbl, last, d, elm, comp_map, direction, comp_anchor, valid_names_str)
                last = el_obj
                if node_name:
                    if comp_anchor:
                        named[node_name] = _AnchorRef(
                            getattr(el_obj, comp_anchor,
                                    self._get_anchor(el_obj)))
                    else:
                        named[node_name] = el_obj
            elif part_type == "parallel":
                last = self._draw_parallel(part_data, comp_map, named, last, d, elm, direction, valid_names_str)

        return last, direction

    # ── 组件列表解析 ──────────────────────────────────

    @staticmethod
    def _parse_parts(description: str) -> list:
        parts = []
        remaining = description.strip()
        while remaining:
            remaining = remaining.strip()
            if remaining.startswith("["):
                depth = 0; end = -1
                for idx, ch in enumerate(remaining):
                    if ch == "[": depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0: end = idx; break
                if end < 0: break
                bracket_content = remaining[1:end]
                branches = Circuit._split_branches(bracket_content)
                parts.append(("parallel", branches))
                remaining = remaining[end + 1:]
                if remaining.startswith("->"): remaining = remaining[2:]
            elif "->" in remaining:
                idx = remaining.index("->")
                part = remaining[:idx].strip()
                if part:
                    if part.lower() in _DIRECTIONS: parts.append(("direction", part.lower()))
                    else: parts.append(("series", part))
                remaining = remaining[idx + 2:]
            else:
                part = remaining.strip()
                if part:
                    if part.lower() in _DIRECTIONS: parts.append(("direction", part.lower()))
                    else: parts.append(("series", part))
                break
        return parts

    @staticmethod
    def _split_branches(content: str) -> list:
        branches = []
        depth = 0; current = ""
        for ch in content + ",":
            if ch == "[": depth += 1
            elif ch == "]": depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                b = current.strip()
                if b: branches.append(b)
                current = ""
            else: current += ch
        return branches

    # ── 元件放置 ──────────────────────────────────────

    @staticmethod
    def _make_box(label_text: str, elm):
        try: return elm.Box().label(label_text)
        except AttributeError: return elm.Line().label(f"[{label_text}]")

    @staticmethod
    def _get_output_anchor(elem):
        """获取元件的输出锚点（优先 out → end → center → (0,0)）。"""
        try: return elem.out
        except AttributeError:
            try: return elem.end
            except AttributeError:
                try: return elem.center
                except AttributeError: return (0, 0)

    @staticmethod
    def _get_anchor(last, anchor: str = "end"):
        try: return getattr(last, anchor)
        except AttributeError:
            try: return last.center
            except AttributeError:
                logger.warning(f"Anchor '{anchor}' not found on element, falling back to (0,0)")
                return (0, 0)

    def _place_component(self, name: str, value: str, label_text: str,
                         last, d, elm, comp_map: dict,
                         direction: str = "right", comp_anchor: str = None,
                         valid_names_str: str = ""):
        if name not in comp_map:
            raise ValueError(
                f"Unknown component '{name}' for this circuit type. "
                f"Valid: {valid_names_str[:200]}")

        comp_cls, kwargs = comp_map[name]

        if value: kwargs = dict(kwargs); kwargs["label"] = value

        try:
            if comp_cls == _GATE_FACTORY:
                # IEEE 标准逻辑门形状
                gate_label = value if value else name.upper()
                gate = _make_ieee_gate(name, gate_label, elm)
                if direction != "right":
                    dir_method = getattr(gate, direction, None)
                    if dir_method is not None:
                        gate = dir_method()
                if last is not None:
                    gate = gate.at(self._get_anchor(last))
                d.add(gate); return gate

            if comp_cls == _BLOCK_FACTORY:
                box_label = value if value else name.upper()
                box_label = box_label.replace("_", " ").title()
                box = self._make_box(box_label, elm)
                if direction != "right":
                    dir_method = getattr(box, direction, None)
                    if dir_method is not None:
                        box = dir_method()
                if last is not None:
                    box = box.at(self._get_anchor(last))
                d.add(box); return box

            if last is None:
                el_obj = comp_cls(**kwargs)
            elif name in ("ground", "gnd"):
                el_obj = comp_cls(**kwargs)
                if direction != "right":
                    dir_method = getattr(el_obj, direction, None)
                    if dir_method is not None:
                        el_obj = dir_method()
                el_obj = el_obj.at(self._get_anchor(last))
            elif comp_anchor:
                el_obj = comp_cls(**kwargs)
                if direction != "right":
                    dir_method = getattr(el_obj, direction, None)
                    if dir_method is not None:
                        el_obj = dir_method()
                el_obj = el_obj.anchor(comp_anchor).at(self._get_anchor(last))
            else:
                el_obj = comp_cls(**kwargs)
                if direction != "right":
                    dir_method = getattr(el_obj, direction, None)
                    if dir_method is not None: el_obj = dir_method()
                el_obj = el_obj.at(self._get_anchor(last))
            d.add(el_obj); return el_obj
        except Exception as e:
            logger.warning(f"Failed to add {name}: {e}")
            fallback_anchor = self._get_anchor(last) if last else (0, 0)
            el_obj = elm.Element().label(f"${label_text}$").right().at(fallback_anchor)
            d.add(el_obj); return el_obj

    # ── 并联分支绘制 ──────────────────────────────────

    def _draw_parallel(self, branches: list, comp_map: dict,
                       named: dict, last, d, elm, direction: str,
                       valid_names_str: str):
        if not last: last = elm.Line(); d.add(last)
        split_point = last.end
        d.add(elm.Dot().at(split_point))
        branch_ends = []

        for bi, branch_str in enumerate(branches):
            branch_comps = [c.strip() for c in branch_str.split("->") if c.strip()]
            if bi == 0:
                draw_dir = getattr(elm, direction)
                first_line = draw_dir().at(split_point); d.add(first_line)
                br_last = first_line
                for comp_str in branch_comps:
                    n, v, lbl, node_name, comp_anchor = self._parse_comp(comp_str)
                    br_last = self._place_component(n, v, lbl, br_last, d, elm, comp_map, direction, comp_anchor, valid_names_str)
                    if node_name: named[node_name] = br_last
                branch_ends.append(br_last.end); last = br_last
            else:
                offset = 0.6 * bi
                d.add(elm.Line().at(split_point).up(offset)); d.add(elm.Dot())
                draw_dir = getattr(elm, direction)
                br_start = draw_dir(); d.add(br_start)
                br_last = br_start
                for comp_str in branch_comps:
                    n, v, lbl, node_name, comp_anchor = self._parse_comp(comp_str)
                    br_last = self._place_component(n, v, lbl, br_last, d, elm, comp_map, direction, comp_anchor, valid_names_str)
                    if node_name: named[node_name] = br_last
                branch_ends.append(br_last.end)
                d.add(elm.Line().down(offset)); d.add(elm.Dot())

        if len(branch_ends) > 1:
            max_x = max(pt[0] for pt in branch_ends)
            main_y = branch_ends[0][1]
            join_point = (max_x + 0.5, main_y)
            for bi, end_pt in enumerate(branch_ends):
                if bi > 0: d.add(elm.Line().at(end_pt).to(join_point))
            d.add(elm.Dot().at(join_point))
            draw_dir = getattr(elm, direction)
            last_line = draw_dir().at(join_point); d.add(last_line)
            last = last_line
        return last

    # ── 辅助 ───────────────────────────────────────────

    @staticmethod
    def _get_ac_source(elm):
        try: return elm.SourceSin
        except AttributeError:
            try:
                from schemdraw.elements.sources import SourceSin
                return SourceSin
            except ImportError: return elm.SourceV
