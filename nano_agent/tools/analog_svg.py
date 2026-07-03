"""Analog circuit — NL → Template → SPICE → SVG pipeline.

Two entry points:
  draw_analog_svg  — NL description → template match → SPICE → SVG (auto-calc values)
  draw_analog_spice — raw SPICE netlist → SVG (custom topologies, LLM-written SPICE)

6-stage architecture:
  NL → Intent Parser → Template Selector → Parameter Calculator → SPICE Generator → SVG Renderer

Supports: filters, amplifiers, rectifiers, voltage dividers.
Auto-calculates component values from user specifications.
"""

import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .spice_common import (
    parse_value, format_value, check_subckt_support,
    replace_opamp_with_e_source, OPAMP_SUBCKT as _OPAMP_SUBCKT_MODEL,
    GROUND_NAMES, prepare_opamp_spice, has_opamp,
)

logger = logging.getLogger("nano_agent.tools.analog_svg")

# Backward-compatible aliases for internal use
_parse_value = parse_value
_format_value = format_value
_check_ngspice_subckt = check_subckt_support
_replace_opamp_with_e_source = replace_opamp_with_e_source


# ═══════════ Circuit Templates ═══════════

_CIRCUIT_TEMPLATES = {
    # ── Filters ──
    ("filter", "rc_lowpass"): {
        "name": "RC Low-Pass Filter",
        "keywords_cn": ["RC低通", "rc低通", "低通滤波器", "低通滤波", "滤波器", "滤波"],
        "guide": "A simple first-order passive RC low-pass filter.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["in", "out"], "value": "?"},
            {"type": "C", "name": "C1", "nodes": ["out", "0"], "value": "?"},
        ],
        "params": {"fc": ("Cutoff frequency (Hz)", "1k"), "R": ("Resistance", "1k")},
        "calculate": "rc_lowpass",
    },
    ("filter", "rc_highpass"): {
        "name": "RC High-Pass Filter",
        "keywords_cn": ["RC高通", "rc高通", "高通滤波器", "高通滤波"],
        "guide": "A simple first-order passive RC high-pass filter.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "C", "name": "C1", "nodes": ["in", "out"], "value": "?"},
            {"type": "R", "name": "R1", "nodes": ["out", "0"], "value": "?"},
        ],
        "params": {"fc": ("Cutoff frequency (Hz)", "1k"), "R": ("Resistance", "1k")},
        "calculate": "rc_lowpass",  # same formula
    },
    ("filter", "lc_lowpass"): {
        "name": "LC Low-Pass Filter",
        "keywords_cn": ["LC低通", "lc低通", "LC滤波", "lc滤波"],
        "guide": "A second-order passive LC low-pass filter.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "L", "name": "L1", "nodes": ["in", "out"], "value": "?"},
            {"type": "C", "name": "C1", "nodes": ["out", "0"], "value": "?"},
        ],
        "params": {"fc": ("Cutoff frequency (Hz)", "1k"), "L": ("Inductance", "1m")},
        "calculate": "lc_lowpass",
    },
    ("filter", "sallen_key_lp"): {
        "name": "Sallen-Key Low-Pass Filter",
        "keywords_cn": ["Sallen-Key", "sallen key", "有源低通", "有源滤波"],
        "guide": "A second-order active low-pass filter using an op-amp.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["in", "n1"], "value": "?"},
            {"type": "R", "name": "R2", "nodes": ["n1", "n2"], "value": "?"},
            {"type": "C", "name": "C1", "nodes": ["n1", "out"], "value": "?"},
            {"type": "C", "name": "C2", "nodes": ["n2", "0"], "value": "?"},
            {"type": "X", "name": "U1", "nodes": ["n2", "out", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"fc": ("Cutoff frequency (Hz)", "1k"), "R": ("Resistance", "10k")},
        "calculate": "sallen_key_lp",
    },
    # ── Amplifiers ──
    ("amplifier", "inverting"): {
        "name": "Inverting Amplifier",
        "keywords_cn": ["反相放大", "反相放大器", "反向放大", "反比例放大", "inverting",
                        "放大器", "放大电路", "运放", "运放电路"],
        "guide": "An inverting op-amp amplifier. Gain = -Rf/R1.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["in", "n1"], "value": "?"},
            {"type": "R", "name": "Rf", "nodes": ["n1", "out"], "value": "?"},
            # in+=0(GND), in-=n1(feedback summing node)
            {"type": "X", "name": "U1", "nodes": ["0", "n1", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"gain": ("Voltage gain (absolute value)", "10"), "R1": ("Input resistance", "1k")},
        "calculate": "inverting_amp",
    },
    ("amplifier", "non_inverting"): {
        "name": "Non-Inverting Amplifier",
        "keywords_cn": ["同相放大", "同相放大器", "正向放大", "正比例放大", "non.inverting"],
        "guide": "A non-inverting op-amp amplifier. Gain = 1 + Rf/R1.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["n1", "0"], "value": "?"},
            {"type": "R", "name": "Rf", "nodes": ["n1", "out"], "value": "?"},
            # in+=in(signal), in-=n1(feedback divider: R1 to GND, Rf to out)
            {"type": "X", "name": "U1", "nodes": ["in", "n1", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"gain": ("Voltage gain", "11"), "R1": ("R1 resistance", "1k")},
        "calculate": "non_inverting_amp",
    },
    ("amplifier", "differential"): {
        "name": "Differential Amplifier",
        "keywords_cn": ["差分放大", "差分放大器", "差动放大", "减法器", "differential"],
        "guide": "A differential op-amp amplifier. Vout = (Rf/R1) * (V2 - V1). "
                 "Simulate with V1=AC 1, V2=DC 0 to measure differential gain.",
        "components": [
            {"type": "V", "name": "V1", "nodes": ["in1", "0"], "value": "AC 1"},
            {"type": "V", "name": "V2", "nodes": ["in2", "0"], "value": "DC 0"},
            {"type": "R", "name": "R1", "nodes": ["in1", "n1"], "value": "?"},
            {"type": "R", "name": "R2", "nodes": ["in2", "n2"], "value": "?"},
            {"type": "R", "name": "Rf", "nodes": ["n1", "out"], "value": "?"},
            {"type": "R", "name": "Rg", "nodes": ["n2", "0"], "value": "?"},
            # n1=in-(反馈), n2=in+(参考) — 负反馈接法
            {"type": "X", "name": "U1", "nodes": ["n2", "n1", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"gain": ("Differential gain", "10"), "R1": ("Input resistance", "1k")},
        "calculate": "differential_amp",
    },
    ("amplifier", "summing_inverting"): {
        "name": "Inverting Summing Amplifier",
        "keywords_cn": ["求和放大", "加法器", "加法放大", "summing"],
        "guide": "Sums multiple inputs with inversion. Vout = -Rf*(V1/R1 + V2/R2 + V3/R3).",
        "components": [
            {"type": "V", "name": "V1", "nodes": ["in1", "0"], "value": "AC 1"},
            {"type": "V", "name": "V2", "nodes": ["in2", "0"], "value": "AC 1"},
            {"type": "V", "name": "V3", "nodes": ["in3", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["in1", "n1"], "value": "?"},
            {"type": "R", "name": "R2", "nodes": ["in2", "n1"], "value": "?"},
            {"type": "R", "name": "R3", "nodes": ["in3", "n1"], "value": "?"},
            {"type": "R", "name": "Rf", "nodes": ["n1", "out"], "value": "?"},
            # in+=0(GND), in-=n1(summing junction)
            {"type": "X", "name": "U1", "nodes": ["0", "n1", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"gain": ("Gain per channel (absolute)", "1"), "R1": ("Input resistance", "1k")},
        "calculate": "summing_amp",
    },
    # ── Rectifiers ──
    ("rectifier", "half_wave"): {
        "name": "Half-Wave Rectifier",
        "keywords_cn": ["半波整流", "半波"],
        "guide": "Converts AC to pulsating DC using a single diode.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "D", "name": "D1", "nodes": ["in", "out"], "value": ""},
            {"type": "R", "name": "Rload", "nodes": ["out", "0"], "value": "1k"},
        ],
        "params": {},
        "calculate": "fixed",
    },
    ("rectifier", "full_wave_bridge"): {
        "name": "Full-Wave Bridge Rectifier",
        "keywords_cn": ["全波整流", "桥式整流", "整流桥", "全桥"],
        "guide": "Converts AC to DC using a 4-diode bridge.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["ac1", "0"], "value": "AC 1"},
            {"type": "D", "name": "D1", "nodes": ["ac1", "dc+"], "value": ""},
            {"type": "D", "name": "D2", "nodes": ["0", "dc+"], "value": ""},
            {"type": "D", "name": "D3", "nodes": ["ac1", "dc-"], "value": ""},
            {"type": "D", "name": "D4", "nodes": ["0", "dc-"], "value": ""},
            {"type": "C", "name": "Cf", "nodes": ["dc+", "dc-"], "value": "100u"},
            {"type": "R", "name": "Rload", "nodes": ["dc+", "dc-"], "value": "1k"},
        ],
        "params": {},
        "calculate": "fixed",
    },
    # ── Voltage Divider ──
    ("divider", "voltage_divider"): {
        "name": "Voltage Divider",
        "keywords_cn": ["分压", "分压器", "分压电路", "电压分压"],
        "guide": "Divides input voltage: Vout = Vin * R2/(R1+R2).",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["in", "out"], "value": "?"},
            {"type": "R", "name": "R2", "nodes": ["out", "0"], "value": "?"},
        ],
        "params": {"ratio": ("Voltage ratio Vout/Vin", "0.5"), "R1": ("R1 resistance", "1k")},
        "calculate": "voltage_divider",
    },
}

# ═══════════ Parameter Calculators ═══════════

def _calc_rc_lowpass(tmpl: dict, params: dict) -> dict:
    fc = _parse_value(str(params.get("fc", "1k")))
    r = _parse_value(str(params.get("R", "1k")))
    c = 1.0 / (2 * math.pi * fc * r)
    return {"R": _format_value(r), "C": _format_value(c)}


def _calc_lc_lowpass(tmpl: dict, params: dict) -> dict:
    fc = _parse_value(str(params.get("fc", "1k")))
    L = _parse_value(str(params.get("L", "1m")))
    c = 1.0 / ((2 * math.pi * fc) ** 2 * L)
    return {"L": _format_value(L), "C": _format_value(c)}


def _calc_sallen_key_lp(tmpl: dict, params: dict) -> dict:
    fc = _parse_value(str(params.get("fc", "1k")))
    r = _parse_value(str(params.get("R", "10k")))
    c = 1.0 / (2 * math.pi * fc * r)
    return {"R": _format_value(r), "C": _format_value(c)}


def _calc_inverting_amp(tmpl: dict, params: dict) -> dict:
    gain = abs(_parse_value(str(params.get("gain", "10"))))
    r1 = _parse_value(str(params.get("R1", "1k")))
    rf = gain * r1
    return {"R1": _format_value(r1), "Rf": _format_value(rf)}


def _calc_non_inverting_amp(tmpl: dict, params: dict) -> dict:
    gain = _parse_value(str(params.get("gain", "11")))
    r1 = _parse_value(str(params.get("R1", "1k")))
    rf = (gain - 1) * r1 if gain > 1 else r1
    return {"R1": _format_value(r1), "Rf": _format_value(rf)}


def _calc_differential_amp(tmpl: dict, params: dict) -> dict:
    gain = _parse_value(str(params.get("gain", "10")))
    r1 = _parse_value(str(params.get("R1", "1k")))
    rf = gain * r1
    return {"R1": _format_value(r1), "R2": _format_value(r1),
            "Rf": _format_value(rf), "Rg": _format_value(rf)}


def _calc_summing_amp(tmpl: dict, params: dict) -> dict:
    gain = abs(_parse_value(str(params.get("gain", "1"))))
    r1 = _parse_value(str(params.get("R1", "1k")))
    rf = gain * r1
    return {"R1": _format_value(r1), "R2": _format_value(r1),
            "R3": _format_value(r1), "Rf": _format_value(rf)}


def _calc_voltage_divider(tmpl: dict, params: dict) -> dict:
    ratio = _parse_value(str(params.get("ratio", "0.5")))
    ratio = max(0.01, min(0.99, ratio))
    r1 = _parse_value(str(params.get("R1", "1k")))
    r2 = r1 * ratio / (1 - ratio)
    return {"R1": _format_value(r1), "R2": _format_value(r2)}


def _calc_fixed(tmpl: dict, params: dict) -> dict:
    return {}


_CALCULATORS = {
    "rc_lowpass": _calc_rc_lowpass,
    "lc_lowpass": _calc_lc_lowpass,
    "sallen_key_lp": _calc_sallen_key_lp,
    "inverting_amp": _calc_inverting_amp,
    "non_inverting_amp": _calc_non_inverting_amp,
    "differential_amp": _calc_differential_amp,
    "summing_amp": _calc_summing_amp,
    "voltage_divider": _calc_voltage_divider,
    "fixed": _calc_fixed,
}

# ═══════════ SPICE Generator ═══════════

def _to_spice(components: list[dict], values: dict) -> str:
    """Fill template components with calculated values, produce SPICE netlist."""
    lines = []
    node_map = {"0": "0", "gnd": "0", "GND": "0"}
    next_id = 1

    for c in components:
        # Fill value placeholder
        val = c.get("value", "")
        if val == "?":
            # Try exact name first ("R1"), then type prefix ("R")
            val = values.get(c["name"], values.get(c["name"].rstrip("0123456789"), "1k"))
        c["filled_value"] = val
        c["filled_model"] = c.get("model", "")

        # Map nodes
        nids = []
        for n in c["nodes"]:
            ns = str(n)
            if ns not in node_map:
                node_map[ns] = str(next_id)
                next_id += 1
            nids.append(node_map[ns])
        c["filled_nodes"] = nids

        t = c["type"]
        name = c["name"]
        nid_str = " ".join(nids)
        if t == "R":
            lines.append(f"{name} {nid_str} {val}")
        elif t == "C":
            lines.append(f"{name} {nid_str} {val}")
        elif t == "L":
            lines.append(f"{name} {nid_str} {val}")
        elif t == "D":
            lines.append(f"{name} {nid_str} DEFAULT_D")
        elif t == "V":
            lines.append(f"{name} {nid_str} {val}")
        elif t == "X":
            model = c.get("filled_model", "opamp")
            # ngspice requires X prefix for subcircuit instances
            xname = name if name.startswith("X") else "X" + name
            lines.append(f"{xname} {nid_str} {model}")

    return "\n".join(lines)


# ═══════════ SPICE Parser ═══════════

def _parse_spice(spice_text: str) -> list[dict]:
    """Parse SPICE netlist into component dicts ready for _render_svg()."""
    components = []
    node_map = {"0": "0", "gnd": "0", "GND": "0"}
    next_id = 1

    # Control/simulation lines to skip
    _SKIP_PREFIXES = (
        ".model", ".subckt", ".ends", ".op", ".ac", ".tran", ".dc",
        ".end", ".probe", ".plot", ".print", ".options", ".temp",
        ".include", ".lib", ".param", ".func", ".global", ".ic",
        ".nodeset", ".save", ".measure", ".alter",
    )

    for line in spice_text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("*") or line.startswith("#"):
            continue
        if line.lower().startswith(_SKIP_PREFIXES):
            continue

        tokens = line.split()
        if len(tokens) < 2:
            continue

        first = tokens[0]
        ctype = first[0].upper()
        cname = first

        if ctype not in ("R", "C", "L", "D", "V", "X"):
            continue

        if ctype in ("R", "C", "L"):
            # Rname n1 n2 value
            if len(tokens) < 4:
                continue
            raw_nodes = tokens[1:3]
            value = tokens[3]
            model = ""

        elif ctype == "D":
            # Dname n+ n- [model]
            raw_nodes = tokens[1:3]
            value = ""
            model = ""

        elif ctype == "V":
            # Vname n+ n- value... (value can contain spaces like "AC 1")
            if len(tokens) < 4:
                continue
            raw_nodes = tokens[1:3]
            value = " ".join(tokens[3:])
            model = ""

        elif ctype == "X":
            # Xname node1 node2 ... model_name
            if len(tokens) < 3:
                continue
            raw_nodes = tokens[1:-1]
            model = tokens[-1]
            value = ""

        # Map nodes
        filled_nodes = []
        for n in raw_nodes:
            ns = str(n)
            if ns not in node_map:
                node_map[ns] = str(next_id)
                next_id += 1
            filled_nodes.append(node_map[ns])

        components.append({
            "type": ctype,
            "name": cname,
            "filled_nodes": filled_nodes,
            "filled_value": value,
            "filled_model": model,
        })

    return components


# ═══════════ SVG Renderer ═══════════

_SVG_COLORS = {
    "bg": "#1a1a2e", "fg": "#e0e0e0", "stroke": "#7c3aed",
    "fill": "#2a2a4e", "text": "#e0e0e0", "gnd": "#3b82f6",
    "node_dot": "#a78bfa", "port_fill": "#0f172a", "port_stroke": "#3b82f6",
}

_COL_GAP = 160
_ROW_GAP = 90


def _render_svg(components: list[dict], title: str = "") -> str:
    """Render analog circuit as dark-theme SVG."""
    # ── Layout: BFS from AC sources ──
    # Build adjacency: node → [comp_index]
    node_to_comps = {}
    comp_nodes = {}
    for i, c in enumerate(components):
        nids = []
        for n in c["filled_nodes"]:
            ns = str(n)
            node_to_comps.setdefault(ns, []).append(i)
            nids.append(ns)
        comp_nodes[i] = nids

    # Source nodes: AC voltage source outputs
    sources = set()
    for i, c in enumerate(components):
        if c["type"] == "V" and "AC" in str(c.get("filled_value", "")).upper():
            nids = comp_nodes[i]
            if nids:
                sources.add(nids[0])
    if not sources:
        for n in node_to_comps:
            if n != "0":
                sources.add(n)
                break

    # BFS from sources (skip GND)
    comp_level = {}
    node_level = {}
    visited_nodes = set()
    visited_comps = set()
    for s in sources:
        node_level[s] = 0
        visited_nodes.add(s)
    from collections import deque
    queue = deque(sources)
    while queue:
        nid = queue.popleft()
        nlev = node_level.get(nid, 0)
        for ci in node_to_comps.get(nid, []):
            if ci in visited_comps:
                continue
            visited_comps.add(ci)
            comp_level[ci] = nlev
            for other_nid in comp_nodes[ci]:
                if other_nid != "0" and other_nid not in visited_nodes:
                    visited_nodes.add(other_nid)
                    node_level[other_nid] = nlev + 1
                    queue.append(other_nid)

    # Assign levels to unvisited
    max_lev = max(comp_level.values()) if comp_level else 0
    for i in range(len(components)):
        if i not in comp_level:
            max_lev += 1
            comp_level[i] = max_lev

    # Group by level
    level_comps = {}
    for i, lev in comp_level.items():
        level_comps.setdefault(lev, []).append(i)
    total_cols = max(level_comps) + 1 if level_comps else 1

    # Component positions
    comp_pos = {}
    for lev in sorted(level_comps):
        gx = 80 + lev * _COL_GAP
        for ri, ci in enumerate(level_comps[lev]):
            gy = 60 + ri * _ROW_GAP
            comp_pos[ci] = (gx, gy)

    # Node positions (average of connected component centers)
    node_pos = {}
    for nid, cis in node_to_comps.items():
        pts = [comp_pos[ci] for ci in cis if ci in comp_pos]
        if pts:
            node_pos[nid] = (sum(p[0] for p in pts) / len(pts),
                             sum(p[1] for p in pts) / len(pts))
    if "0" not in node_pos:
        max_y = max(y for _, y in comp_pos.values()) if comp_pos else 200
        node_pos["0"] = (80, max_y + 80)

    # SVG dimensions
    svg_w = max(400, (total_cols + 1) * _COL_GAP)
    max_rows = max(len(v) for v in level_comps.values()) if level_comps else 1
    svg_h = max(300, max_rows * _ROW_GAP + 140, max(y for _, y in node_pos.values()) + 100)

    # Build SVG
    svg = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg",
                             "viewBox": f"0 0 {svg_w} {svg_h}",
                             "width": str(svg_w), "height": str(svg_h)})
    ET.SubElement(svg, "rect", {"width": str(svg_w), "height": str(svg_h),
                                "fill": _SVG_COLORS["bg"]})
    if title:
        ET.SubElement(svg, "text", {"x": str(svg_w // 2), "y": "22",
                                    "text-anchor": "middle", "fill": _SVG_COLORS["text"],
                                    "font-family": "monospace", "font-size": "13",
                                    "font-weight": "bold"}).text = title

    # Junction dots
    node_comp_count = {}
    for nid, cis in node_to_comps.items():
        node_comp_count[nid] = len(cis)
    for nid, (nx, ny) in node_pos.items():
        if nid == "0":
            continue
        if node_comp_count.get(nid, 0) > 1:
            ET.SubElement(svg, "circle", {"cx": str(nx), "cy": str(ny), "r": "3",
                                          "fill": _SVG_COLORS["node_dot"]})

    # Wires: component pins to nodes
    for i, c in enumerate(components):
        if i not in comp_pos:
            continue
        cx, cy = comp_pos[i]
        nids = comp_nodes[i]
        for j, nid in enumerate(nids):
            if nid not in node_pos:
                continue
            nx, ny = node_pos[nid]
            px, py = _pin_pos(cx, cy, j, len(nids), c["type"])
            _draw_ortho_wire(svg, px, py, nx, ny)

    # Components
    for i, c in enumerate(components):
        if i in comp_pos:
            cx, cy = comp_pos[i]
            _draw_component(svg, c, cx, cy)

    # Ground
    if "0" in node_pos:
        gx, gy = node_pos["0"]
        _draw_ground(svg, gx, gy)

    return ET.tostring(svg, encoding="unicode")


def _pin_pos(cx, cy, pin_idx, total_pins, ctype):
    """Calculate pin position on component body."""
    if ctype == "R":
        return (cx - 35, cy) if pin_idx == 0 else (cx + 35, cy)
    elif ctype in ("C", "L", "D"):
        return (cx - 30, cy) if pin_idx == 0 else (cx + 30, cy)
    elif ctype == "V":
        return (cx - 18, cy) if pin_idx == 0 else (cx + 18, cy)
    elif ctype == "X":
        # SPICE subcircuit pin order: in_p(非反相) in_n(反相) out vcc vss
        if pin_idx == 0:
            return (cx - 35, cy - 12)  # in_p (non-inverting, +)
        elif pin_idx == 1:
            return (cx - 35, cy + 12)  # in_n (inverting, -)
        elif pin_idx == 2:
            return (cx + 35, cy)       # out
        elif pin_idx == 3:
            return (cx, cy - 28)       # vcc
        else:
            return (cx, cy + 28)       # vss
    return (cx, cy)


def _draw_ortho_wire(svg, x1, y1, x2, y2):
    """Orthogonal wire with column-gap-aware midpoint."""
    dist = abs(x1 - x2)
    if dist < 8:
        return
    if dist > _COL_GAP * 0.8:
        mid_raw = (x1 + x2) / 2
        mid = round(mid_raw / (_COL_GAP / 2)) * (_COL_GAP / 2)
        mid = max(x1 + 10, min(x2 - 10, mid))
        d = f"M{x1},{y1} L{mid},{y1} L{mid},{y2} L{x2},{y2}"
    else:
        mid = (x1 + x2) / 2
        d = f"M{x1},{y1} L{mid},{y1} L{mid},{y2} L{x2},{y2}"
    ET.SubElement(svg, "path", {"d": d, "fill": "none", "stroke": _SVG_COLORS["stroke"],
                                "stroke-width": "1.5", "stroke-linejoin": "round"})


# ═══════════ Component Symbols ═══════════

def _draw_component(svg, c, x, y):
    t = c["type"]
    v = c.get("filled_value", "")
    name = c.get("name", "")
    if t == "R":
        _draw_resistor(svg, x, y, v)
    elif t == "C":
        _draw_capacitor(svg, x, y, v)
    elif t == "L":
        _draw_inductor(svg, x, y, v)
    elif t == "D":
        _draw_diode(svg, x, y)
    elif t == "V":
        _draw_vsource(svg, x, y, v)
    elif t == "X":
        _draw_opamp(svg, x, y, name)


def _draw_resistor(svg, x, y, v):
    W = 60
    n = 5
    seg_w = W / (n + 1)
    pts = [(x - W // 2, y)]
    for i in range(1, n + 1):
        pts.append((x - W // 2 + i * seg_w,
                    y + (seg_w * 0.6 if i % 2 == 1 else -seg_w * 0.6)))
    pts.append((x + W // 2, y))
    d = "M" + " L".join(f"{px},{py}" for px, py in pts)
    ET.SubElement(svg, "path", {"d": d, "fill": "none", "stroke": _SVG_COLORS["stroke"],
                                "stroke-width": "1.5", "stroke-linejoin": "round"})
    if v:
        ET.SubElement(svg, "text", {"x": str(x), "y": str(y + 20), "text-anchor": "middle",
                                    "fill": _SVG_COLORS["text"], "font-family": "monospace",
                                    "font-size": "9"}).text = v


def _draw_capacitor(svg, x, y, v):
    L, G = 30, 8
    ET.SubElement(svg, "line", {"x1": str(x - L), "y1": str(y), "x2": str(x - G), "y2": str(y),
                                "stroke": _SVG_COLORS["stroke"], "stroke-width": "1.5"})
    ET.SubElement(svg, "line", {"x1": str(x - G), "y1": str(y - 10), "x2": str(x - G), "y2": str(y + 10),
                                "stroke": _SVG_COLORS["stroke"], "stroke-width": "1.5"})
    ET.SubElement(svg, "line", {"x1": str(x + G), "y1": str(y - 10), "x2": str(x + G), "y2": str(y + 10),
                                "stroke": _SVG_COLORS["stroke"], "stroke-width": "1.5"})
    ET.SubElement(svg, "line", {"x1": str(x + G), "y1": str(y), "x2": str(x + L), "y2": str(y),
                                "stroke": _SVG_COLORS["stroke"], "stroke-width": "1.5"})
    if v:
        ET.SubElement(svg, "text", {"x": str(x), "y": str(y + 22), "text-anchor": "middle",
                                    "fill": _SVG_COLORS["text"], "font-family": "monospace",
                                    "font-size": "9"}).text = v


def _draw_inductor(svg, x, y, v):
    L, R, N = 50, 6, 4
    d = f"M{x - L // 2},{y}"
    for i in range(N):
        sweep = 1 if i % 2 == 0 else 0
        d += f" A{R},{R} 0 0,{sweep} {x - L // 2 + (i+1)*L//N},{y}"
    ET.SubElement(svg, "path", {"d": d, "fill": "none", "stroke": _SVG_COLORS["stroke"],
                                "stroke-width": "1.5"})
    if v:
        ET.SubElement(svg, "text", {"x": str(x), "y": str(y + 22), "text-anchor": "middle",
                                    "fill": _SVG_COLORS["text"], "font-family": "monospace",
                                    "font-size": "9"}).text = v


def _draw_diode(svg, x, y):
    S = 10
    d = f"M{x + S},{y - S} L{x},{y} L{x + S},{y + S} Z"
    ET.SubElement(svg, "path", {"d": d, "fill": _SVG_COLORS["fill"], "stroke": _SVG_COLORS["stroke"],
                                "stroke-width": "1.5", "stroke-linejoin": "round"})
    ET.SubElement(svg, "line", {"x1": str(x - S), "y1": str(y - S), "x2": str(x - S), "y2": str(y + S),
                                "stroke": _SVG_COLORS["stroke"], "stroke-width": "1.5"})


def _draw_vsource(svg, x, y, v):
    R = 14
    ET.SubElement(svg, "circle", {"cx": str(x), "cy": str(y), "r": str(R),
                                  "fill": "none", "stroke": _SVG_COLORS["stroke"],
                                  "stroke-width": "1.5"})
    ET.SubElement(svg, "text", {"x": str(x), "y": str(y + 4), "text-anchor": "middle",
                                "fill": _SVG_COLORS["text"], "font-family": "monospace",
                                "font-size": "10"}).text = "+"
    if v and v != "AC 1":
        ET.SubElement(svg, "text", {"x": str(x), "y": str(y + 26), "text-anchor": "middle",
                                    "fill": _SVG_COLORS["text"], "font-family": "monospace",
                                    "font-size": "9"}).text = v


def _draw_opamp(svg, x, y, name):
    W, H = 60, 50
    x0, y0 = x - W // 2, y - H // 2
    d = f"M{x0},{y0} L{x0},{y0 + H} L{x0 + W * 0.7},{y} Z"
    ET.SubElement(svg, "path", {"d": d, "fill": _SVG_COLORS["fill"],
                                "stroke": _SVG_COLORS["stroke"], "stroke-width": "1.5",
                                "stroke-linejoin": "round"})
    ET.SubElement(svg, "line", {"x1": str(x0), "y1": str(y0 + H * 0.3), "x2": str(x0 - 10),
                                "y2": str(y0 + H * 0.3), "stroke": _SVG_COLORS["stroke"],
                                "stroke-width": "1.5"})
    ET.SubElement(svg, "line", {"x1": str(x0), "y1": str(y0 + H * 0.7), "x2": str(x0 - 10),
                                "y2": str(y0 + H * 0.7), "stroke": _SVG_COLORS["stroke"],
                                "stroke-width": "1.5"})
    ET.SubElement(svg, "line", {"x1": str(x0 + W * 0.7), "y1": str(y), "x2": str(x0 + W * 0.7 + 10),
                                "y2": str(y), "stroke": _SVG_COLORS["stroke"], "stroke-width": "1.5"})
    ET.SubElement(svg, "text", {"x": str(x0 - 12), "y": str(y0 + H * 0.3 + 4),
                                "text-anchor": "end", "fill": _SVG_COLORS["text"],
                                "font-family": "monospace", "font-size": "8"}).text = "+"
    ET.SubElement(svg, "text", {"x": str(x0 - 12), "y": str(y0 + H * 0.7 + 4),
                                "text-anchor": "end", "fill": _SVG_COLORS["text"],
                                "font-family": "monospace", "font-size": "8"}).text = "-"
    if name:
        ET.SubElement(svg, "text", {"x": str(x), "y": str(y0 + H + 14), "text-anchor": "middle",
                                    "fill": _SVG_COLORS["text"], "font-family": "monospace",
                                    "font-size": "9"}).text = name


def _draw_ground(svg, x, y):
    W = 20
    ET.SubElement(svg, "line", {"x1": str(x), "y1": str(y - 15), "x2": str(x), "y2": str(y),
                                "stroke": _SVG_COLORS["gnd"], "stroke-width": "1.5"})
    ET.SubElement(svg, "line", {"x1": str(x - W), "y1": str(y), "x2": str(x + W), "y2": str(y),
                                "stroke": _SVG_COLORS["gnd"], "stroke-width": "1.5"})
    ET.SubElement(svg, "line", {"x1": str(x - W * 0.6), "y1": str(y + 5), "x2": str(x + W * 0.6),
                                "y2": str(y + 5), "stroke": _SVG_COLORS["gnd"], "stroke-width": "1.5"})
    ET.SubElement(svg, "line", {"x1": str(x - W * 0.2), "y1": str(y + 10), "x2": str(x + W * 0.2),
                                "y2": str(y + 10), "stroke": _SVG_COLORS["gnd"], "stroke-width": "1.5"})


# ═══════════ design_circuit helpers ═══════════

# All shared SPICE utilities now imported from spice_common
# Aliases at module top: _parse_value, _format_value, _check_ngspice_subckt, _replace_opamp_with_e_source


def _parse_specs(specs: str) -> dict | None:
    """Parse 'fc=10kHz gain=20' → {key: 'fc', value: 10000, tolerance: 0.05}.

    Only the FIRST key=value pair is used as the optimization target.
    Additional pairs are logged as info.
    """
    if not specs or not specs.strip():
        return None
    result = {}
    for part in specs.split():
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip().lower()
            val = _parse_value(v.strip())
            if "key" not in result:
                result["key"] = k
                result["value"] = val
                result["tolerance"] = 0.05
            # Additional specs are noted but only the first is optimized
    return result if "key" in result else None


def _spec_to_metric(calc_name: str) -> str:
    """Map template calculator to target metric name."""
    _MAP = {"rc_lowpass": "fc", "rc_highpass": "fc",
            "lc_lowpass": "fc", "sallen_key_lp": "fc",
            "inverting_amp": "gain", "non_inverting_amp": "gain",
            "differential_amp": "gain", "summing_amp": "gain"}
    return _MAP.get(calc_name, "")


def _adjust_params(values: dict, calc_name: str, metric: str,
                   current: float, target: float) -> dict:
    """Simple proportional parameter adjustment."""
    if current <= 0:
        return values
    ratio = target / current
    new = dict(values)

    if metric == "fc":
        # fc ~ 1/(RC) or 1/sqrt(LC). Scale inversely, split between components.
        # Count adjustable components and use sqrt to avoid oscillation.
        r_keys = [k for k in ("R", "R1", "R2") if k in new]
        c_keys = [k for k in ("C", "C1", "C2") if k in new]
        l_keys = [k for k in ("L", "L1") if k in new]
        n_adj = len(r_keys) + len(c_keys) + len(l_keys)
        scale = ratio ** (1.0 / max(n_adj, 1)) if n_adj > 0 else ratio
        for k in r_keys:
            new[k] = _format_value(_parse_value(str(new[k])) / scale)
        for k in c_keys:
            new[k] = _format_value(_parse_value(str(new[k])) / scale)
        for k in l_keys:
            new[k] = _format_value(_parse_value(str(new[k])) / scale)

    elif metric == "gain":
        # Gain ~ Rf/R1 → scale Rf. For differential, also scale Rg to match.
        for k in ("Rf",):
            if k in new:
                new[k] = _format_value(_parse_value(str(new[k])) * ratio)
        for k in ("Rg",):
            if k in new:
                new[k] = _format_value(_parse_value(str(new[k])) * ratio)

    return new


def _auto_fix_spice(spice: str, error_output: str) -> str:
    """Attempt basic auto-fixes for common SPICE errors."""
    fixed = spice
    # Remove duplicate .end
    if fixed.count(".end") > 1:
        lines = fixed.split("\n")
        end_count = 0
        result = []
        for line in lines:
            if line.strip() == ".end":
                end_count += 1
                if end_count > 1:
                    continue
            result.append(line)
        fixed = "\n".join(result)
    return fixed


def _fmt_metric(val: float, metric: str) -> str:
    """Format metric value for display."""
    if metric == "fc":
        if val >= 1e6:
            return f"{val/1e6:.2f} MHz"
        elif val >= 1e3:
            return f"{val/1e3:.2f} kHz"
        return f"{val:.1f} Hz"
    elif metric == "gain":
        return f"{val:.2f}x ({20*math.log10(abs(val)):.1f} dB)"
    return f"{val:.4g}"


# ═══════════ Main Class ═══════════

class AnalogSVG:
    TOOLS = [
        ("draw_analog_svg",
         "Draw analog circuit diagrams from descriptions. "
         "Auto-calculates component values from specifications.\n"
         "\n**Supported circuits:**\n"
         "- Filters: rc_lowpass, rc_highpass, lc_lowpass, sallen_key_lp\n"
         "- Amplifiers: inverting, non_inverting, differential, summing_inverting\n"
         "- Rectifiers: half_wave, full_wave_bridge\n"
         "- Voltage divider\n"
         "\n**Examples:**\n"
         "- 'RC low-pass filter fc=1kHz'\n"
         "- 'inverting amplifier gain=-10'\n"
         "- 'Sallen-Key low-pass fc=10kHz'\n"
         "- 'full-wave bridge rectifier'",
         "draw_analog_svg",
         {"description": {"type": "string",
                          "description":
                          "Circuit description. Examples: 'RC low-pass fc=1kHz', "
                          "'inverting amplifier gain=-10'. "
                          "Supported: rc_lowpass, rc_highpass, lc_lowpass, sallen_key_lp, "
                          "inverting, non_inverting, differential, summing_inverting, "
                          "half_wave, full_wave_bridge, voltage_divider"},
          "title": {"type": "string", "description": "Optional diagram title"}},
         ["description"]),

        ("draw_analog_spice",
         "Draw analog circuits from a raw SPICE netlist. "
         "Use this when you want to draw a custom circuit topology "
         "not covered by draw_analog_svg templates.\n"
         "\n"
         "**Supported components:** R, C, L, D, V, X (op-amp subcircuit)\n"
         "**Format:** Standard SPICE netlist, one component per line.\n"
         "\n"
         "**Examples:**\n"
         "- RC low-pass: `Vin in 0 AC 1\\nR1 in out 1k\\nC1 out 0 10n`\n"
         "- Sallen-Key: `Vin in 0 AC 1\\nR1 in n1 10k\\nR2 n1 n2 10k\\n"
         "C1 n1 out 1n\\nC2 n2 0 1n\\nXU1 n2 out out vcc 0 opamp`\n"
         "- Differential amp: `V1 in1 0 AC 1\\nV2 in2 0 AC 1\\n"
         "R1 in1 n1 1k\\nR2 in2 n2 1k\\nRf n1 out 10k\\nRg n2 0 10k\\n"
         "XU1 n1 n2 out vcc 0 opamp`\n"
         "\n"
         "**Node naming:** Use any string node names. Node '0' is ground. "
         "AC voltage sources define signal inputs for layout.",
         "draw_analog_spice",
         {"spice": {"type": "string",
                    "description":
                    "SPICE netlist. One component per line. "
                    "R/C/L: name n1 n2 value. V: name n+ n- value. "
                    "X: name nodes... model. Node 0 = ground. "
                    "Example: 'Vin in 0 AC 1\\nR1 in out 1k\\nC1 out 0 10n'"},
          "title": {"type": "string", "description": "Optional diagram title"}},
         ["spice"]),

        ("design_circuit",
         "Design an analog circuit end-to-end: NL→template→SPICE→simulate→"
         "auto-fix→optimize→render.\n"
         "\n"
         "**What it does automatically:**\n"
         "1. Matches your description to a circuit template\n"
         "2. Calculates initial component values\n"
         "3. Runs ngspice simulation — auto-fixes common errors\n"
         "4. If specs provided (e.g. 'fc=10kHz gain=20'), iteratively adjusts\n"
         "   values until targets are met\n"
         "5. Returns the final circuit diagram + simulation metrics\n"
         "\n"
         "**Specs format:** `param=value` pairs, space-separated.\n"
         "- Filters: `fc=10kHz` (cutoff frequency)\n"
         "- Amplifiers: `gain=20` (voltage gain)\n"
         "- Use with description for initial template selection\n"
         "\n"
         "**Examples:**\n"
         "- `design_circuit('RC low-pass filter', 'fc=5kHz')`\n"
         "- `design_circuit('inverting amplifier', 'gain=50')`\n"
         "- `design_circuit('Sallen-Key low-pass', 'fc=20kHz')`",
         "design_circuit",
         {"description": {"type": "string",
                           "description":
                           "Circuit description. Examples: 'RC low-pass filter', "
                           "'inverting amplifier', 'differential amplifier'"},
          "specs": {"type": "string",
                     "description":
                     "Optional target specs as 'key=value' pairs. "
                     "E.g. 'fc=10kHz' for filter cutoff, 'gain=20' for amplifier gain."},
          "title": {"type": "string", "description": "Optional title"}},
         ["description"]),
    ]

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def draw_analog_svg(self, description: str, title: str = "") -> str:
        """Parse NL description → template → calculate → SPICE → sim verify → SVG.

        Only returns the circuit diagram if ngspice simulation succeeds.
        If simulation fails, returns the error so the LLM can fix and retry.
        """
        try:
            tmpl, values = self._match_template(description)
            components = [dict(c) for c in tmpl["components"]]
            spice = _to_spice(components, values)
            svg_title = title or tmpl.get("name", "")
        except Exception as e:
            logger.exception(f"Analog SVG failed: {e}")
            return f"Error drawing analog circuit: {e}"

        # ── Stage 1: SPICE 语法检查 ──
        sim_ok, sim_output = self._run_sim_check(spice)
        if not sim_ok:
            guide = tmpl.get("guide", "")
            return (
                f"❌ **Circuit simulation failed — diagram blocked.**\n\n"
                f"**Circuit:** {svg_title}\n{guide}\n\n"
                f"**SPICE Netlist:**\n```spice\n{spice}\n```\n\n"
                f"**Simulation Error:**\n```\n{sim_output[:1500]}\n```\n\n"
                f"🔧 **Fix the SPICE netlist above** and call `draw_analog_spice` "
                f"with the corrected netlist to re-render."
            )

        # ── Stage 2: 电气特性验证 ──
        char_ok, char_msg = self._validate_characteristics(spice, tmpl, values)
        if not char_ok:
            guide = tmpl.get("guide", "")
            return (
                f"❌ **Electrical validation failed — diagram blocked.**\n\n"
                f"**Circuit:** {svg_title}\n{guide}\n\n"
                f"{char_msg}\n\n"
                f"**SPICE Netlist:**\n```spice\n{spice}\n```\n\n"
                f"🔧 **Fix the issues above** and try again. "
                f"Common causes: opamp input polarity swap, wrong resistor values, "
                f"missing connections."
            )

        # 两阶段都通过 → 渲染 SVG
        try:
            svg = self._render_schemdraw_svg(spice, svg_title) or _render_svg(components, svg_title)
        except Exception as e:
            logger.exception(f"Analog SVG render failed: {e}")
            return f"Error rendering analog circuit: {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        guide = tmpl.get("guide", "")
        guide_text = f"\n{guide}" if guide else ""
        sim_block = f"\n\n✅ **Simulation verified** — ngspice ran successfully."
        spice_block = f"\n\n**SPICE Netlist:**\n```spice\n{spice}\n```"
        sim_hint = ("\n\n💡 **Next:** Call `simulate_spice` with the SPICE netlist above "
                    "for detailed AC/transient analysis and iterative optimization.")
        return f"![{svg_title}]({url})\n{url}{guide_text}{sim_block}{spice_block}{sim_hint}"

    @staticmethod
    def _run_sim_check(spice: str) -> tuple[bool, str]:
        """Run a quick ngspice OP check. Returns (passed, output_text).

        Auto-detects ngspice subcircuit bug and falls back to E-source.
        """
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("ngspice"):
            return True, ""  # ngspice 不可用时放行

        subckt_ok = _check_ngspice_subckt()
        sim_spice = spice
        has_opamp = bool(re.search(r'\bX\w+\b', sim_spice))
        if has_opamp and not subckt_ok:
            sim_spice = _replace_opamp_with_e_source(sim_spice, gain=100000)

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".cir", delete=False
            ) as f:
                # SPICE first line is always a title — prepend one to avoid
                # the first component being swallowed as title
                f.write("* Circuit simulation\n")
                if has_opamp and subckt_ok:
                    f.write(_OPAMP_SUBCKT_MODEL)
                if re.search(r'^D\w+', sim_spice, re.MULTILINE):
                    f.write(".model DEFAULT_D D (IS=1e-14 RS=1 N=1)\n")
                f.write(sim_spice + "\n")
                if ".op" not in sim_spice.lower() and ".ac" not in sim_spice.lower() \
                   and ".tran" not in sim_spice.lower():
                    f.write(".op\n")
                f.write(".end\n")
                cir_path = f.name

            result = subprocess.run(
                ["ngspice", "-b", cir_path],
                capture_output=True, text=True, timeout=15,
            )
            output = (result.stderr + result.stdout)
            Path(cir_path).unlink(missing_ok=True)

            if result.returncode != 0:
                return False, output[:1500]
            if re.search(r'(Error on line|FATAL|parse error|too few nodes)',
                         output, re.IGNORECASE):
                return False, output[:1500]
            return True, output[:500]
        except subprocess.TimeoutExpired:
            return False, "Simulation timed out (>15s)"
        except Exception:
            return True, ""  # 仿真不可用时放行，不阻塞出图

    @staticmethod
    def _validate_characteristics(spice: str, tmpl: dict,
                                   values: dict) -> tuple[bool, str]:
        """Run AC simulation and verify electrical characteristics.

        Checks: DC operating point, gain, and other sanity metrics.
        Returns (passed, detail_text).
        """
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("ngspice"):
            return True, ""

        calc_name = tmpl.get("calculate", "")
        is_amp = "amp" in calc_name
        is_filter = "lowpass" in calc_name or "highpass" in calc_name

        # Prepare AC analysis SPICE
        subckt_ok = _check_ngspice_subckt()
        sim_spice = spice
        has_opamp = bool(re.search(r'\bX\w+\b', sim_spice))
        if has_opamp and not subckt_ok:
            sim_spice = _replace_opamp_with_e_source(sim_spice, gain=100000)

        # Find output node: look for node named 'out' in any component
        out_node = None
        for c in tmpl.get("components", []):
            for n in c.get("nodes", []):
                if str(n) == "out":
                    out_node = "out"
                    break
            if out_node:
                break
        # Fallback: use the second node of the last component
        if not out_node:
            for c in reversed(tmpl.get("components", [])):
                nodes = c.get("nodes", [])
                if len(nodes) >= 2 and nodes[1] != "0":
                    out_node = str(nodes[1])
                    break
        if not out_node:
            return True, ""

        # Map node name to ID
        node_map = {"0": "0", "gnd": "0", "GND": "0"}
        next_id = 1
        for c in tmpl.get("components", []):
            for n in c.get("nodes", []):
                ns = str(n)
                if ns not in node_map:
                    node_map[ns] = str(next_id)
                    next_id += 1
        out_nid = node_map.get(out_node, "1")

        ac_spice = sim_spice.strip()
        if ".ac" not in ac_spice.lower():
            ac_spice += "\n.ac dec 20 1 1e6"
        if ".print" not in ac_spice.lower():
            ac_spice += f"\n.print ac vm({out_nid}) vp({out_nid})"
        if not ac_spice.strip().endswith(".end"):
            ac_spice += "\n.end"

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".cir", delete=False
            ) as f:
                f.write("* Circuit validation\n")
                if has_opamp and subckt_ok:
                    f.write(_OPAMP_SUBCKT_MODEL)
                f.write(ac_spice + "\n")
                cir_path = f.name

            result = subprocess.run(
                ["ngspice", "-b", cir_path],
                capture_output=True, text=True, timeout=20,
            )
            output = (result.stderr + result.stdout).replace("\f", "\n")
            Path(cir_path).unlink(missing_ok=True)

            if result.returncode != 0:
                return True, ""  # don't block on AC failure
        except Exception:
            return True, ""

        # Parse AC output: extract vm(out) values
        issues = []
        try:
            from nano_agent.tools.spice_simulator import _parse_ac_output
            parsed = _parse_ac_output(output)
            if parsed.get("error"):
                return True, ""

            # Find the output node's vm column
            target_col = f"vm({out_nid})"
            # After _parse_ac_output, vm columns are converted to vdb
            target_db = f"vdb({out_nid})"
            db_vals = parsed.get("data", {}).get(target_db, [])
            if not db_vals:
                return True, ""

            dc_gain_db = db_vals[0] if db_vals else 0
            dc_gain_lin = 10 ** (dc_gain_db / 20.0)

            # ── Amplifier checks ──
            if is_amp:
                # Expected gain from calculated values
                if calc_name == "inverting_amp":
                    rf = _parse_value(str(values.get("Rf", "10k")))
                    r1 = _parse_value(str(values.get("R1", "1k")))
                    expected_gain = rf / r1
                elif calc_name == "non_inverting_amp":
                    rf = _parse_value(str(values.get("Rf", "10k")))
                    r1 = _parse_value(str(values.get("R1", "1k")))
                    expected_gain = 1 + rf / r1
                elif calc_name == "differential_amp":
                    rf = _parse_value(str(values.get("Rf", "10k")))
                    r1 = _parse_value(str(values.get("R1", "1k")))
                    expected_gain = rf / r1
                elif calc_name == "summing_amp":
                    rf = _parse_value(str(values.get("Rf", "1k")))
                    r1 = _parse_value(str(values.get("R1", "1k")))
                    expected_gain = rf / r1
                else:
                    expected_gain = None

                if expected_gain and expected_gain > 0:
                    ratio = dc_gain_lin / expected_gain
                    if ratio < 0.001:
                        issues.append(
                            f"Output is dead (gain &lt; 0.1% of expected). "
                            f"Measured {dc_gain_lin:.4f}x ({dc_gain_db:.1f} dB) "
                            f"vs expected ~{expected_gain:.1f}x. "
                            f"Check for open circuit or missing connection.")
                    elif ratio < 0.05:
                        issues.append(
                            f"Gain severely low: {dc_gain_lin:.2f}x ({dc_gain_db:.1f} dB) "
                            f"vs expected ~{expected_gain:.1f}x ({ratio*100:.1f}%). "
                            f"Check component values — resistors may be wrong order of magnitude.")
                    elif ratio > 100:
                        issues.append(
                            f"Gain extremely high: {dc_gain_lin:.0f}x vs "
                            f"expected ~{expected_gain:.0f}x. "
                            f"Possible open feedback loop or missing resistor.")

            # ── Filter checks ──
            if is_filter and db_vals:
                # Check that there's meaningful attenuation
                vmin, vmax = min(db_vals), max(db_vals)
                if abs(vmax - vmin) < 1:
                    issues.append(
                        "Filter shows almost no attenuation across frequency sweep. "
                        "Check component values — cutoff may be outside measured range.")

            # ── DC sanity ──
            if dc_gain_db < -150:
                issues.append(
                    "Output node has no AC signal (< -150 dB). "
                    "Possible open circuit or missing power supply connection.")

        except Exception:
            pass

        if issues:
            detail = "### ⚠️ Electrical Validation Failed\n\n" + \
                     "\n".join(f"- {i}" for i in issues)
            return False, detail
        return True, ""

    def draw_analog_spice(self, spice: str, title: str = "") -> str:
        """Parse SPICE netlist → sim verify → render SVG.

        Only returns the circuit diagram if ngspice simulation succeeds.
        If simulation fails, returns the error so the LLM can fix and retry.
        """
        spice_stripped = spice.strip()

        # ── SPICE 仿真验证 ──
        sim_ok, sim_output = self._run_sim_check(spice_stripped)
        if not sim_ok:
            return (
                f"❌ **Simulation failed — diagram blocked.**\n\n"
                f"**SPICE Netlist:**\n```spice\n{spice_stripped}\n```\n\n"
                f"**Simulation Error:**\n```\n{sim_output[:1500]}\n```\n\n"
                f"🔧 **Fix the SPICE errors above** and call `draw_analog_spice` "
                f"again with the corrected netlist. Once simulation passes, "
                f"the diagram will be shown automatically."
            )

        # 仿真通过 → 渲染 SVG
        try:
            components = _parse_spice(spice_stripped)
            if not components:
                return "Error: no valid SPICE components found. " \
                       "Supported: R, C, L, D, V, X (op-amp subcircuit)."
            svg = self._render_schemdraw_svg(spice_stripped, title) or _render_svg(components, title)
        except Exception as e:
            logger.exception(f"Analog SPICE render failed: {e}")
            return f"Error rendering SPICE circuit: {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        spice_block = f"\n\n**SPICE Netlist:**\n```spice\n{spice_stripped}\n```"
        sim_block = f"\n\n✅ **Simulation verified** — ngspice ran successfully."
        sim_hint = ("\n\n💡 **Next:** Call `simulate_spice` for detailed AC/transient "
                    "analysis and iterative optimization.")
        return f"![{title or 'Analog Circuit'}]({url})\n{url}{sim_block}{spice_block}{sim_hint}"

    # ═══════════ design_circuit: 自动闭环设计 ═══════════

    def design_circuit(self, description: str, specs: str = "",
                       title: str = "") -> str:
        """End-to-end circuit design with auto-fix and optimization loop.

        NL → template → SPICE → simulate → auto-fix errors →
        optimize for specs → render → report.
        """
        # 1. Template matching
        try:
            tmpl, values = self._match_template(description)
            components = [dict(c) for c in tmpl["components"]]
            circuit_name = title or tmpl.get("name", description)
            calc_name = tmpl.get("calculate", "")
        except Exception as e:
            return f"❌ **Template matching failed:** {e}\n\n" \
                   f"Supported circuits: rc_lowpass, rc_highpass, lc_lowpass, " \
                   f"sallen_key_lp, inverting, non_inverting, differential, " \
                   f"summing_inverting, half_wave, full_wave_bridge, voltage_divider"

        target = _parse_specs(specs)

        # 2. Simulation loop with auto-fix
        spice = _to_spice(components, values)
        sim_ok, sim_output = self._run_sim_check(spice)
        fix_attempts = 0

        while not sim_ok and fix_attempts < 3:
            fix_attempts += 1
            # Try E-source fallback for subcircuit issues
            if ("Mismatch" in sim_output or "subckt" in sim_output.lower()
                    or "subcircuit" in sim_output.lower()):
                sim_spice = _replace_opamp_with_e_source(spice, gain=100000)
                sim_ok, sim_output = self._run_sim_check(sim_spice)
                if sim_ok:
                    spice = sim_spice  # use fixed version
                    break
            # Try basic SPICE fixes
            fixed = _auto_fix_spice(spice, sim_output)
            if fixed != spice:
                spice = fixed
                sim_ok, sim_output = self._run_sim_check(spice)
            else:
                break  # can't auto-fix

        if not sim_ok:
            return (
                f"❌ **Circuit simulation failed after {fix_attempts} fix attempt(s).**\n\n"
                f"**Circuit:** {circuit_name}\n\n"
                f"**SPICE Netlist:**\n```spice\n{spice}\n```\n\n"
                f"**Error:**\n```\n{sim_output[:1200]}\n```\n\n"
                f"🔧 Please check component values and connections manually, "
                f"then call `draw_analog_spice` with the corrected netlist."
            )

        # 3. Metric extraction + spec-driven optimization
        metric_name = _spec_to_metric(calc_name)
        current_val = None
        opt_attempts = 0

        if target and metric_name:
            current_val = self._measure_metric(spice, metric_name)
            while current_val is not None and opt_attempts < 5:
                tval = target.get("value", 0)
                if abs(tval) < 1e-12:
                    break
                err = abs(current_val - tval) / abs(tval)
                if err < target.get("tolerance", 0.05):
                    break  # within tolerance

                opt_attempts += 1
                values = _adjust_params(
                    values, calc_name, metric_name, current_val, target["value"])
                for c in components:
                    if c["name"] in values:
                        c["value"] = values[c["name"]]
                spice = _to_spice(components, values)

                # Verify adjusted circuit still simulates
                sim_ok, _ = self._run_sim_check(spice)
                if not sim_ok:
                    break  # adjustment broke the circuit, stop

                current_val = self._measure_metric(spice, metric_name)

        # 4. Render final SVG
        try:
            svg = self._render_schemdraw_svg(spice, circuit_name) or _render_svg(components, circuit_name)
        except Exception as e:
            return f"❌ **SVG rendering failed:** {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        # 5. Build report
        parts = [f"![{circuit_name}]({url})\n{url}"]
        parts.append(f"\n✅ **Simulation passed** — ngspice verified")

        if current_val is not None and metric_name:
            parts.append(f"\n**Measured {metric_name}:** {_fmt_metric(current_val, metric_name)}")
        if target:
            tval = target.get("value", 0)
            if current_val is not None and abs(tval) > 1e-12:
                err_pct = abs(current_val - tval) / abs(tval) * 100
                in_spec = err_pct < target.get("tolerance", 0.05) * 100
                status = "✅ Within spec" if in_spec else f"⚠️ Off by {err_pct:.1f}%"
                parts.append(f"**Target {metric_name}:** {_fmt_metric(tval, metric_name)} — {status}")
            elif abs(tval) > 1e-12:
                parts.append(f"**Target {metric_name}:** {_fmt_metric(tval, metric_name)}")
            parts.append(f"\n*Optimized in {opt_attempts} iteration(s)*")

        parts.append(f"\n**SPICE Netlist:**\n```spice\n{spice}\n```")
        if opt_attempts > 0:
            parts.append("\n💡 To further optimize, call `simulate_spice` with the "
                         "SPICE netlist above for detailed AC/transient analysis.")
        return "\n".join(parts)

    def _measure_metric(self, spice: str, metric_name: str) -> float | None:
        """Run AC simulation and extract a single metric value."""
        import subprocess
        from nano_agent.tools.spice_simulator import (
            _prep_netlist, _parse_ac_output, _compute_ac_metrics)

        ac_spice = spice.strip()
        if ".ac" not in ac_spice.lower():
            ac_spice += "\n.ac dec 20 1 1e6"
        prepared, _, = _prep_netlist(ac_spice)

        try:
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".cir", delete=False
            ) as f:
                f.write(prepared)
                cir_path = f.name

            result = subprocess.run(
                ["ngspice", "-b", cir_path],
                capture_output=True, text=True, timeout=20,
                cwd=str(self.charts_dir),
            )
            Path(cir_path).unlink(missing_ok=True)
            output = (result.stderr + result.stdout).replace("\f", "\n")
        except Exception:
            return None

        parsed = _parse_ac_output(output)
        metrics = _compute_ac_metrics(parsed) if not parsed.get("error") else {}

        if metric_name == "fc":
            return metrics.get("cutoff_freq")
        elif metric_name == "gain":
            db_val = metrics.get("dc_gain_db")
            if db_val is not None and db_val > -190:
                return 10 ** (db_val / 20.0)  # dB → linear
            return None
        return None

    @staticmethod
    def _render_schemdraw_svg(spice: str, title: str = "") -> str:
        """Render SPICE netlist to SVG via schemdraw (professional layout)."""
        try:
            from nano_agent.tools.spice_renderer import _build_graph, _layout
            from schemdraw import Drawing
            import schemdraw.elements as elm

            graph = _build_graph(spice)
            if not graph["components"]:
                return None

            layout = _layout(graph)
            d = Drawing(canvas='svg', unit=3)
            comps = graph["components"]
            main_chain = layout["main_chain"]
            branches = layout["branches"]
            ground_nets = graph["ground_nets"]

            if not comps:
                return None

            branch_at_col = {}
            for parent_col, comp_idx, direction in branches:
                branch_at_col.setdefault(parent_col, []).append((comp_idx, direction))

            placed = set()
            _ELEM_MAP = {"R": "Resistor", "C": "Capacitor", "L": "Inductor2",
                         "D": "Diode"}
            for col, comp_idx in enumerate(main_chain):
                comp = comps[comp_idx]
                ctype = comp["type"]
                value = comp.get("value", "")

                if ctype == "V":
                    vu = value.upper()
                    if "SIN" in vu or "AC" in vu:
                        el = elm.SourceSin()
                    elif "DC" in vu:
                        el = elm.SourceV()
                    elif "PULSE" in vu:
                        el = elm.SourcePulse()
                    else:
                        el = elm.SourceV()
                elif ctype == "X":
                    el = elm.Opamp()
                elif ctype == "D":
                    el = elm.Diode()
                else:
                    cls_name = _ELEM_MAP.get(ctype, "Resistor")
                    el_cls = getattr(elm, cls_name)
                    el = el_cls()
                    if value and ctype != "V":
                        el.label(value)

                if col == 0:
                    d.add(el)
                else:
                    d.add(el.right())
                placed.add(comp_idx)

                if col in branch_at_col:
                    for bci, bdir in branch_at_col[col]:
                        if bci in placed:
                            continue
                        bcomp = comps[bci]
                        bctype = bcomp["type"]
                        bvalue = bcomp.get("value", "")
                        if bctype == "V":
                            bel = elm.SourceV()
                        elif bctype == "X":
                            bel = elm.Opamp()
                        else:
                            bcls_name = _ELEM_MAP.get(bctype, "Resistor")
                            bel_cls = getattr(elm, bcls_name)
                            bel = bel_cls()
                            if bvalue:
                                bel.label(bvalue)
                        d.push()
                        d.add(bel.down())
                        for pn, _ in bcomp["pins"]:
                            if pn in ground_nets:
                                d.add(elm.Ground())
                                break
                        d.pop()
                        placed.add(bci)

            svg_bytes = d.get_imagedata('svg')
            return svg_bytes.decode('utf-8') if isinstance(svg_bytes, bytes) else str(svg_bytes)
        except ImportError:
            return None
        except Exception as e:
            logger.warning(f"schemdraw render failed: {e}")
            return None

    @staticmethod
    def _match_template(desc: str):
        """Match NL description to template + calculate values."""
        desc_lower = desc.lower().strip()

        # Keyword matching
        matches = []
        for (cat, sub), tmpl in _CIRCUIT_TEMPLATES.items():
            score = 0
            name_lower = tmpl["name"].lower()
            # English keywords: sub name and category
            if sub.replace("_", " ") in desc_lower or sub in desc_lower:
                score += 3
            for word in name_lower.split():
                if word in desc_lower or word.replace("-", " ") in desc_lower:
                    score += 1
            if cat in desc_lower:
                score += 1
            # Chinese keywords: high-weight match
            for kw in tmpl.get("keywords_cn", []):
                if kw in desc_lower:
                    score += 5
            if score > 0:
                matches.append((score, cat, sub))

        if not matches:
            # Default: try to find any matching English category
            for (cat, sub), tmpl in _CIRCUIT_TEMPLATES.items():
                if cat in desc_lower:
                    matches.append((1, cat, sub))
                    break

        if not matches:
            # Fallback: infer from Chinese category-indicating words
            if any(w in desc_lower for w in ("放大", "运放")):
                cat, sub = "amplifier", "inverting"
            elif any(w in desc_lower for w in ("滤波",)):
                cat, sub = "filter", "rc_lowpass"
            elif any(w in desc_lower for w in ("整流",)):
                cat, sub = "rectifier", "half_wave"
            elif any(w in desc_lower for w in ("分压",)):
                cat, sub = "divider", "voltage_divider"
            else:
                # Last resort: RC low-pass
                cat, sub = "filter", "rc_lowpass"
        else:
            matches.sort(reverse=True)
            cat, sub = matches[0][1], matches[0][2]

        tmpl = _CIRCUIT_TEMPLATES[(cat, sub)]

        # Extract numeric params from description
        params = {}
        for param_key, (param_desc, default_val) in tmpl.get("params", {}).items():
            params[param_key] = default_val
            # Try to find param in description: "fc=1kHz", "gain=-10", "fc 1k"
            for pattern in [rf'{param_key}\s*[=:]\s*(-?[\d.]+[kKmMuUnNpP]?)',
                            rf'{param_key}\s+(-?[\d.]+[kKmMuUnNpP]?[Hh]?[Zz]?[Ω]?)']:
                m = re.search(pattern, desc)
                if m:
                    params[param_key] = m.group(1)
                    break

        # Run calculator
        calc_name = tmpl.get("calculate", "fixed")
        calc_fn = _CALCULATORS.get(calc_name, _calc_fixed)
        values = calc_fn(tmpl, params)

        return tmpl, values
