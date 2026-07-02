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

logger = logging.getLogger("nano_agent.tools.analog_svg")

# ═══════════ Value Helpers ═══════════

_UNITS = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "μ": 1e-6,
          "m": 1e-3, "k": 1e3, "K": 1e3, "Meg": 1e6, "M": 1e6, "G": 1e9}


def _parse_value(s: str) -> float:
    """Parse SPICE-style value string to float. '1k' → 1000, '10n' → 1e-8."""
    s = str(s).strip()
    if not s:
        return 0
    for unit, scale in sorted(_UNITS.items(), key=lambda x: -len(x[0])):
        if s.endswith(unit):
            try:
                return float(s[:-len(unit)]) * scale
            except ValueError:
                pass
    try:
        return float(s)
    except ValueError:
        return 0


def _format_value(v: float) -> str:
    """Format float to compact SPICE-style string. 1590 → '1.59k', 1e-7 → '100n'."""
    if v == 0:
        return "0"
    abs_v = abs(v)
    for unit, scale in [("p", 1e-12), ("n", 1e-9), ("u", 1e-6),
                         ("m", 1e-3), ("", 1), ("k", 1e3), ("Meg", 1e6)]:
        if abs_v >= scale * 0.1 or unit == "Meg":
            val = v / scale
            if abs(val - round(val)) < 0.001 and abs(val) >= 10:
                return f"{int(round(val))}{unit}"
            if abs(val) >= 1:
                return f"{val:.2f}".rstrip("0").rstrip(".") + unit
            return f"{val:.3f}".rstrip("0").rstrip(".") + unit
    return str(v)


# ═══════════ Circuit Templates ═══════════

_CIRCUIT_TEMPLATES = {
    # ── Filters ──
    ("filter", "rc_lowpass"): {
        "name": "RC Low-Pass Filter",
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
        "guide": "An inverting op-amp amplifier. Gain = -Rf/R1.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["in", "n1"], "value": "?"},
            {"type": "R", "name": "Rf", "nodes": ["n1", "out"], "value": "?"},
            {"type": "X", "name": "U1", "nodes": ["n1", "0", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"gain": ("Voltage gain (absolute value)", "10"), "R1": ("Input resistance", "1k")},
        "calculate": "inverting_amp",
    },
    ("amplifier", "non_inverting"): {
        "name": "Non-Inverting Amplifier",
        "guide": "A non-inverting op-amp amplifier. Gain = 1 + Rf/R1.",
        "components": [
            {"type": "V", "name": "Vin", "nodes": ["in", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["n1", "0"], "value": "?"},
            {"type": "R", "name": "Rf", "nodes": ["n1", "out"], "value": "?"},
            {"type": "X", "name": "U1", "nodes": ["n1", "in", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"gain": ("Voltage gain", "11"), "R1": ("R1 resistance", "1k")},
        "calculate": "non_inverting_amp",
    },
    ("amplifier", "differential"): {
        "name": "Differential Amplifier",
        "guide": "A differential op-amp amplifier. Vout = (Rf/R1) * (V2 - V1).",
        "components": [
            {"type": "V", "name": "V1", "nodes": ["in1", "0"], "value": "AC 1"},
            {"type": "V", "name": "V2", "nodes": ["in2", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["in1", "n1"], "value": "?"},
            {"type": "R", "name": "R2", "nodes": ["in2", "n2"], "value": "?"},
            {"type": "R", "name": "Rf", "nodes": ["n1", "out"], "value": "?"},
            {"type": "R", "name": "Rg", "nodes": ["n2", "0"], "value": "?"},
            {"type": "X", "name": "U1", "nodes": ["n1", "n2", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"gain": ("Differential gain", "10"), "R1": ("Input resistance", "1k")},
        "calculate": "differential_amp",
    },
    ("amplifier", "summing_inverting"): {
        "name": "Inverting Summing Amplifier",
        "guide": "Sums multiple inputs with inversion. Vout = -Rf*(V1/R1 + V2/R2 + V3/R3).",
        "components": [
            {"type": "V", "name": "V1", "nodes": ["in1", "0"], "value": "AC 1"},
            {"type": "V", "name": "V2", "nodes": ["in2", "0"], "value": "AC 1"},
            {"type": "V", "name": "V3", "nodes": ["in3", "0"], "value": "AC 1"},
            {"type": "R", "name": "R1", "nodes": ["in1", "n1"], "value": "?"},
            {"type": "R", "name": "R2", "nodes": ["in2", "n1"], "value": "?"},
            {"type": "R", "name": "R3", "nodes": ["in3", "n1"], "value": "?"},
            {"type": "R", "name": "Rf", "nodes": ["n1", "out"], "value": "?"},
            {"type": "X", "name": "U1", "nodes": ["n1", "0", "out", "vcc", "0"], "model": "opamp"},
        ],
        "params": {"gain": ("Gain per channel (absolute)", "1"), "R1": ("Input resistance", "1k")},
        "calculate": "summing_amp",
    },
    # ── Rectifiers ──
    ("rectifier", "half_wave"): {
        "name": "Half-Wave Rectifier",
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
    return {"R1": _format_value(r1), "Rf": _format_value(rf)}


def _calc_summing_amp(tmpl: dict, params: dict) -> dict:
    gain = abs(_parse_value(str(params.get("gain", "1"))))
    r1 = _parse_value(str(params.get("R1", "1k")))
    rf = gain * r1
    return {"R1": _format_value(r1), "Rf": _format_value(rf)}


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
        val = c["value"]
        if val == "?":
            val = values.get(c["name"], "1k")
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
            lines.append(f"{name} {nid_str} {model}")

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
        if pin_idx == 0:
            return (cx - 35, cy - 12)  # in-
        elif pin_idx == 1:
            return (cx - 35, cy + 12)  # in+
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
                                "font-family": "monospace", "font-size": "8"}).text = "-"
    ET.SubElement(svg, "text", {"x": str(x0 - 12), "y": str(y0 + H * 0.7 + 4),
                                "text-anchor": "end", "fill": _SVG_COLORS["text"],
                                "font-family": "monospace", "font-size": "8"}).text = "+"
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
    ]

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def draw_analog_svg(self, description: str, title: str = "") -> str:
        """Parse NL description → template → calculate → SPICE → SVG."""
        try:
            tmpl, values = self._match_template(description)
            components = [dict(c) for c in tmpl["components"]]
            spice = _to_spice(components, values)
            svg_title = title or tmpl.get("name", "")
            svg = _render_svg(components, svg_title)
        except Exception as e:
            logger.exception(f"Analog SVG failed: {e}")
            return f"Error drawing analog circuit: {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        guide = tmpl.get("guide", "")
        spice_block = f"\n\n**SPICE Netlist:**\n```spice\n{spice}\n```" if spice else ""
        guide_text = f"\n{guide}" if guide else ""
        return f"![{svg_title}]({url})\n{url}{guide_text}{spice_block}"

    def draw_analog_spice(self, spice: str, title: str = "") -> str:
        """Parse SPICE netlist → render SVG directly (no template matching).

        Accepts any valid SPICE netlist with R/C/L/D/V/X components.
        The LLM can hand-write SPICE for custom topologies.
        """
        try:
            components = _parse_spice(spice)
            if not components:
                return "Error: no valid SPICE components found. " \
                       "Supported: R, C, L, D, V, X (op-amp subcircuit)."
            svg = _render_svg(components, title)
        except Exception as e:
            logger.exception(f"Analog SPICE render failed: {e}")
            return f"Error rendering SPICE circuit: {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        spice_block = f"\n\n**SPICE Netlist:**\n```spice\n{spice.strip()}\n```"
        return f"![{title or 'Analog Circuit'}]({url})\n{url}{spice_block}"

    @staticmethod
    def _match_template(desc: str):
        """Match NL description to template + calculate values."""
        desc_lower = desc.lower().strip()

        # Keyword matching
        matches = []
        for (cat, sub), tmpl in _CIRCUIT_TEMPLATES.items():
            score = 0
            name_lower = tmpl["name"].lower()
            if sub.replace("_", " ") in desc_lower or sub in desc_lower:
                score += 3
            for word in name_lower.split():
                if word in desc_lower or word.replace("-", " ") in desc_lower:
                    score += 1
            if cat in desc_lower:
                score += 1
            if score > 0:
                matches.append((score, cat, sub))

        if not matches:
            # Default: try to find any matching keyword
            for (cat, sub), tmpl in _CIRCUIT_TEMPLATES.items():
                if cat in desc_lower:
                    matches.append((1, cat, sub))
                    break

        if not matches:
            # Fallback: RC low-pass
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
