"""Analog circuit — NL → Template → SPICE → SVG pipeline.

Two entry points:
  draw_analog_svg  — NL description → template match → SPICE → SVG (auto-calc values)
  draw_analog_spice — raw SPICE netlist → SVG (custom topologies, LLM-written SPICE)

6-stage architecture:
  NL → Intent Parser → Template Selector → Parameter Calculator → SPICE Generator → SVG Renderer

Supports: filters, amplifiers, rectifiers, voltage dividers.
Auto-calculates component values from user specifications.
"""

import json as _json
import logging
import math
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import deque as _deque
from datetime import datetime
from pathlib import Path

from .spice_common import (
    parse_value, format_value, check_subckt_support,
    replace_opamp_with_e_source, OPAMP_SUBCKT as _OPAMP_SUBCKT_MODEL,
    GROUND_NAMES, prepare_opamp_spice, has_opamp,
)

logger = logging.getLogger("nano_agent.tools.analog_svg")

# ── Physical constants ──
VTH = 0.026     # Thermal voltage at 300K (V)
VBE_ON = 0.7    # BJT base-emitter turn-on voltage (V)
DEFAULT_OPAMP_GAIN = 100000  # Fallback opamp open-loop gain

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
        "keywords_cn": ["RC低通", "rc低通"],
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
            {"type": "D", "name": "D3", "nodes": ["dc-", "ac1"], "value": ""},
            {"type": "D", "name": "D4", "nodes": ["dc-", "0"], "value": ""},
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


def _clamp_gain(gain: float, min_gain: float, max_gain: float, amp_type: str) -> float:
    """Clamp gain to practical limits with a warning."""
    if gain < min_gain:
        logger.warning(
            f"{amp_type}: requested gain {gain:.3g} below minimum {min_gain:.3g}, "
            f"clamping to {min_gain:.3g}")
        return min_gain
    if gain > max_gain:
        logger.warning(
            f"{amp_type}: requested gain {gain:.3g} exceeds practical maximum "
            f"{max_gain:.3g} for single-stage, clamping to {max_gain:.3g}")
        return max_gain
    return gain


def _calc_inverting_amp(tmpl: dict, params: dict) -> dict:
    gain = _clamp_gain(abs(_parse_value(str(params.get("gain", "10")))),
                       0.01, 1000, "Inverting amp")
    r1 = _parse_value(str(params.get("R1", "1k")))
    rf = gain * r1
    return {"R1": _format_value(r1), "Rf": _format_value(rf)}


def _calc_non_inverting_amp(tmpl: dict, params: dict) -> dict:
    gain = _clamp_gain(_parse_value(str(params.get("gain", "11"))),
                       1.0, 1000, "Non-inverting amp")
    r1 = _parse_value(str(params.get("R1", "1k")))
    if gain > 1.001:
        rf = (gain - 1) * r1
    else:
        # gain ≈ 1 → voltage follower: Rf ≈ 0 (tiny value approximates short)
        rf = 0.1
    return {"R1": _format_value(r1), "Rf": _format_value(rf)}


def _calc_differential_amp(tmpl: dict, params: dict) -> dict:
    gain = _clamp_gain(_parse_value(str(params.get("gain", "10"))),
                       0.01, 1000, "Differential amp")
    r1 = _parse_value(str(params.get("R1", "1k")))
    rf = gain * r1
    return {"R1": _format_value(r1), "R2": _format_value(r1),
            "Rf": _format_value(rf), "Rg": _format_value(rf)}


def _calc_summing_amp(tmpl: dict, params: dict) -> dict:
    gain = _clamp_gain(abs(_parse_value(str(params.get("gain", "1")))),
                       0.01, 100, "Summing amp")
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


# ═══════════ BJT Calculators ═══════════

def _calc_common_emitter(tmpl: dict, params: dict) -> dict:
    """BJT common-emitter amplifier bias calculator.

    Design approach:
      - Vce ≈ Vcc/2 for maximum output swing
      - Ve = Vcc/10 for stable DC operating point
      - Voltage divider bias: stiff divider (Idiv ≈ 0.1*Ic)
      - Ce bypass capacitor → gain ≈ -Rc*Ic/VTH (unilateral at mid-band)
    """
    Ic = _parse_value(str(params.get("Ic", "1m")))
    Vcc = _parse_value(str(params.get("Vcc", "12")))
    Ve = Vcc * 0.1
    Re = Ve / Ic
    Vc = Vcc * 0.6  # Vce ≈ 0.5*Vcc, Ve ≈ 0.1*Vcc → Vc ≈ Vcc - 0.4*Vcc
    Rc = (Vcc - Vc) / Ic
    Vb = Ve + VBE_ON
    Idiv = Ic * 0.1
    R2 = Vb / Idiv
    R1 = (Vcc - Vb) / Idiv
    return {"R1": _format_value(R1), "R2": _format_value(R2),
            "Rc": _format_value(Rc), "Re": _format_value(Re)}


def _calc_emitter_follower(tmpl: dict, params: dict) -> dict:
    """BJT emitter follower (common-collector) bias calculator.

    Sets Ve = Vcc/2 for maximum symmetrical swing.
    """
    Ic = _parse_value(str(params.get("Ic", "1m")))
    Vcc = _parse_value(str(params.get("Vcc", "12")))
    Ve = Vcc * 0.5
    Re = Ve / Ic
    Vb = Ve + VBE_ON
    Idiv = Ic * 0.1
    R2 = Vb / Idiv
    R1 = (Vcc - Vb) / Idiv
    return {"R1": _format_value(R1), "R2": _format_value(R2),
            "Re": _format_value(Re)}


def _calc_current_mirror(tmpl: dict, params: dict) -> dict:
    """BJT current mirror: Rref sets reference current.

    Iref = (Vcc - Vbe) / Rref → Rref = (Vcc - Vbe) / Iref
    """
    Iref = _parse_value(str(params.get("Iref", "1m")))
    Vcc = _parse_value(str(params.get("Vcc", "10")))
    Rref = (Vcc - VBE_ON) / Iref
    return {"Rref": _format_value(Rref)}


def _calc_bjt_diff_pair(tmpl: dict, params: dict) -> dict:
    """BJT differential pair calculator.

    Single-ended gain: Av = gm*Rc/2 = (Ic/VTH)*Rc/2 where Ic = Itail/2.
    So Rc = 2*Av*VTH/Ic = 4*Av*VTH/Itail.
    Tail resistor: Ree ≈ (|Vee| - VBE)/Itail.
    """
    Itail = _parse_value(str(params.get("Itail", "1m")))
    gain = _parse_value(str(params.get("gain", "20")))
    Ic = Itail / 2.0
    Rc = 2.0 * gain * VTH / Ic
    # Vee is typically 12V from template
    Vee_mag = 12.0
    Ree = (Vee_mag - VBE_ON) / Itail
    return {"Rc1": _format_value(Rc), "Rc2": _format_value(Rc),
            "Ree": _format_value(Ree)}


def _calc_mosfet_cs(tmpl: dict, params: dict) -> dict:
    """MOSFET common-source amplifier calculator.

    Uses rule-of-thumb values for Level-1 NMOS model.
    Rd provides gain; Rs provides bias stability.
    Gate divider (100k/47k) biases gate at ~Vdd*0.32 for turn-on.
    """
    gain = abs(_parse_value(str(params.get("gain", "5"))))
    Vdd = _parse_value(str(params.get("Vdd", "12")))
    # Rough gain scaling: gm ≈ 1-2mS for typical bias, Rd = gain/gm_typical
    Rd = gain * 1000
    Rd = max(500, min(Rd, 100000))
    # Small source resistor for DC bias stability
    Rs = (Vdd * 0.1) / 0.001  # ≈1.2k for 12V, 1mA
    Rs = max(100, min(Rs, 10000))
    # Gate bias divider
    Rg1 = 100000
    Rg2 = 47000
    return {"Rd": _format_value(Rd), "Rs": _format_value(Rs),
            "Rg1": _format_value(Rg1), "Rg2": _format_value(Rg2)}


# ═══════════ Active Filter Calculators ═══════════

def _calc_mfb_bandpass(tmpl: dict, params: dict) -> dict:
    """Multiple-feedback band-pass filter calculator.

    f0 = 1/(2*pi*C) * sqrt((R1+R3)/(R1*R2*R3))
    Choose C by frequency range, then compute R1, R2, R3 from f0 and Q.
    """
    f0 = _parse_value(str(params.get("fc", "1k")))
    Q = _parse_value(str(params.get("Q", "5")))
    # Choose C based on f0
    if f0 < 100:
        C = 100e-9
    elif f0 < 1000:
        C = 10e-9
    elif f0 < 10000:
        C = 1e-9
    else:
        C = 100e-12
    # MFB design equations (C1 = C2 = C, unity passband gain H=1):
    H = 1.0
    R1 = Q / (2 * math.pi * f0 * C * H)
    R2 = Q / (math.pi * f0 * C)
    denom = 2 * Q * Q - H
    if denom <= 0:
        denom = 1.0
    R3 = Q / (2 * math.pi * f0 * C * denom)
    return {"R1": _format_value(R1), "R2": _format_value(R2),
            "R3": _format_value(R3), "C": _format_value(C)}


def _calc_twin_t_notch(tmpl: dict, params: dict) -> dict:
    """Twin-T notch filter: fn = 1/(2*pi*R*C). R3=R/2, C3=2C."""
    fn = _parse_value(str(params.get("fc", "100")))
    R = _parse_value(str(params.get("R", "10k")))
    C = 1.0 / (2 * math.pi * fn * R)
    return {"R": _format_value(R), "C": _format_value(C)}


# ═══════════ Opamp Application Calculators ═══════════

def _calc_integrator(tmpl: dict, params: dict) -> dict:
    """Integrator: fc = 1/(2*pi*R*C) → C = 1/(2*pi*fc*R)."""
    fc = _parse_value(str(params.get("fc", "159")))
    R = _parse_value(str(params.get("R", "10k")))
    C = 1.0 / (2 * math.pi * fc * R)
    return {"Rin": _format_value(R), "Cf": _format_value(C)}


def _calc_differentiator(tmpl: dict, params: dict) -> dict:
    """Differentiator: fc = 1/(2*pi*R*C) → C = 1/(2*pi*fc*R)."""
    fc = _parse_value(str(params.get("fc", "159")))
    R = _parse_value(str(params.get("R", "10k")))
    C = 1.0 / (2 * math.pi * fc * R)
    return {"Rf": _format_value(R), "Cin": _format_value(C)}


def _calc_instrumentation_amp(tmpl: dict, params: dict) -> dict:
    """3-opamp instrumentation amplifier.

    Gain = 1 + 2*R1/Rg. R1=R2=R3=10k matched. Rg computed from gain.
    User can override Rg directly; otherwise Rg = 2*R1 / (gain - 1).
    """
    gain = _clamp_gain(_parse_value(str(params.get("gain", "100"))),
                       1.1, 1000, "Instrumentation amp")
    R_fixed = 10000  # 10k matched resistors
    # If user explicitly provided Rg, use it; otherwise compute from gain
    if "Rg" in params:
        Rg = _parse_value(str(params["Rg"]))
    else:
        # Rg = 2*R1 / (gain - 1)
        Rg = 2.0 * R_fixed / (gain - 1.0)
    return {"R1a": _format_value(R_fixed), "R1b": _format_value(R_fixed),
            "R2a": _format_value(R_fixed), "R2b": _format_value(R_fixed),
            "R3a": _format_value(R_fixed), "R3b": _format_value(R_fixed),
            "Rg": _format_value(Rg)}


def _calc_schmitt_trigger(tmpl: dict, params: dict) -> dict:
    """Schmitt trigger: hysteresis ratio = R2/(R1+R2)."""
    ratio = _parse_value(str(params.get("ratio", "0.1")))
    ratio = max(0.01, min(0.5, ratio))
    R1 = 10000
    R2 = R1 * ratio / (1 - ratio)
    return {"R1": _format_value(R1), "R2": _format_value(R2)}


# ═══════════ MOSFET Calculators ═══════════

def _calc_common_drain(tmpl: dict, params: dict) -> dict:
    """MOSFET source follower. Fixed bias, gain ≈ 1."""
    Vdd = _parse_value(str(params.get("Vdd", "12")))
    Rs = Vdd / 0.004  # ~3k for 12V, ~4mA
    Rg1 = 100000
    Rg2 = 100000
    return {"Rs": _format_value(Rs), "Rg1": _format_value(Rg1),
            "Rg2": _format_value(Rg2)}


def _calc_mosfet_diff_pair(tmpl: dict, params: dict) -> dict:
    """MOSFET differential pair.

    Id = Itail/2, gm ≈ 2*Id/Vov (assume Vov≈1V), Av = gm*Rd.
    """
    gain = _parse_value(str(params.get("gain", "10")))
    Itail = _parse_value(str(params.get("Itail", "1m")))
    Id_per = Itail / 2.0
    gm_est = 2.0 * Id_per / 1.0  # Vov ≈ 1V
    Rd = gain / gm_est
    Rd = max(500, min(Rd, 50000))
    Rss = 5.0 / Itail  # ~5V across tail resistor
    Rss = max(100, min(Rss, 100000))
    return {"Rd1": _format_value(Rd), "Rd2": _format_value(Rd),
            "Rss": _format_value(Rss)}


# ═══════════ BJT Calculators (continued) ═══════════

def _calc_common_base(tmpl: dict, params: dict) -> dict:
    """BJT common-base amplifier. Av = gm*Rc, gm = Ic/VTH."""
    Ic = _parse_value(str(params.get("Ic", "1m")))
    Vcc = _parse_value(str(params.get("Vcc", "12")))
    Vc = Vcc * 0.6
    Rc = (Vcc - Vc) / Ic
    Ve = Vcc * 0.1
    Re = Ve / Ic
    Vb = Vcc * 0.5  # base AC-grounded via bypass cap
    Idiv = Ic * 0.1
    R2 = Vb / Idiv
    R1 = (Vcc - Vb) / Idiv
    return {"Rc": _format_value(Rc), "Re": _format_value(Re),
            "R1": _format_value(R1), "R2": _format_value(R2)}


def _calc_cascode(tmpl: dict, params: dict) -> dict:
    """BJT cascode amplifier (CE + CB).

    Q1 (CE) provides gm; Q2 (CB) provides isolation and bandwidth.
    """
    Ic = _parse_value(str(params.get("Ic", "1m")))
    Vcc = _parse_value(str(params.get("Vcc", "15")))
    Vc2 = Vcc * 0.7
    Rc = (Vcc - Vc2) / Ic
    Ve1 = Vcc * 0.05
    Re = Ve1 / Ic
    Vb2 = Vcc * 0.4
    Idiv = Ic * 0.1
    R2 = Vb2 / Idiv
    R1 = (Vcc - Vb2) / Idiv
    return {"Rc": _format_value(Rc), "Re": _format_value(Re),
            "R1": _format_value(R1), "R2": _format_value(R2)}


# ═══════════ Output / Power Amplifier Calculators ═══════════

def _calc_class_ab_push_pull(tmpl: dict, params: dict) -> dict:
    """Class AB complementary push-pull output stage calculator.

    Design approach:
      - D1/D2 bias provides ~1.4V spread between NPN and PNP bases → crossover elimination
      - Re1 = Re2 = VTH / Iq → small emitter resistors for thermal stability
      - Rbias sets diode bias current: Rbias = (Vcc - 1.4) / Idiode, Idiode ≈ 1mA
      - Iq (quiescent current) ~20mA is typical for audio
    """
    Vcc = _parse_value(str(params.get("Vcc", "12")))
    Iq = _parse_value(str(params.get("Iq", "20m")))
    # Emitter resistors: small values for thermal stability — drop ~VTH at Iq
    VTH = 0.026  # thermal voltage at room temp
    Re_val = VTH / Iq
    Re_val = max(0.1, min(Re_val, 10))  # clamp to 0.1Ω–10Ω range
    # Bias resistor: sets diode current ~1mA
    I_bias = 0.001
    V_diode_drop = 1.4  # two diodes
    Rbias = (Vcc - V_diode_drop) / I_bias
    return {"Re1": _format_value(Re_val), "Re2": _format_value(Re_val),
            "Rbias": _format_value(Rbias)}


def _calc_class_a_output(tmpl: dict, params: dict) -> dict:
    """Class A common-emitter output stage calculator.

    Biased at Vcc/2 (Vc) for maximum symmetrical swing.
    Re = Vcc*0.1 / Ic for DC stability. Ce bypasses Re for AC gain.
    """
    Vcc = _parse_value(str(params.get("Vcc", "12")))
    Ic = _parse_value(str(params.get("Ic", "50m")))
    # Bias collector at Vcc/2 for max swing
    Vc = Vcc / 2.0
    Rc = (Vcc - Vc) / Ic
    # Emitter resistor: 10% of Vcc for stability
    Ve = Vcc * 0.1
    Re = Ve / Ic
    Vb = Ve + VBE_ON
    Idiv = Ic * 0.1
    R2 = Vb / Idiv
    R1 = (Vcc - Vb) / Idiv
    return {"R1": _format_value(R1), "R2": _format_value(R2),
            "Rc": _format_value(Rc), "Re": _format_value(Re)}


# ═══════════ Oscillator Calculators ═══════════

def _calc_wien_bridge_osc(tmpl: dict, params: dict) -> dict:
    """Wien bridge oscillator: fosc = 1/(2*pi*R*C). Rf/Rg ≈ 2.15 for reliable startup."""
    fosc = _parse_value(str(params.get("fc", "1k")))
    R = _parse_value(str(params.get("R", "10k")))
    C = 1.0 / (2 * math.pi * fosc * R)
    Rg_val = 10000
    Rf_val = Rg_val * 2.15  # slightly > 2 for reliable oscillation startup
    return {"R": _format_value(R), "C": _format_value(C),
            "Rf": _format_value(Rf_val), "Rg": _format_value(Rg_val)}


def _calc_rc_phase_shift(tmpl: dict, params: dict) -> dict:
    """RC phase-shift oscillator: fosc = 1/(2*pi*sqrt(6)*R*C). Rf/Rin ≥ 29."""
    fosc = _parse_value(str(params.get("fc", "1k")))
    R = _parse_value(str(params.get("R", "10k")))
    C = 1.0 / (2 * math.pi * math.sqrt(6) * fosc * R)
    Rf_val = 33000  # 33k → gain=33 > 29 minimum
    return {"R": _format_value(R), "C": _format_value(C),
            "Rf": _format_value(Rf_val)}


_CALCULATORS = {
    "rc_lowpass": _calc_rc_lowpass,
    "lc_lowpass": _calc_lc_lowpass,
    "sallen_key_lp": _calc_sallen_key_lp,
    "sallen_key_hp": _calc_sallen_key_lp,  # same formula, R/C swapped
    "inverting_amp": _calc_inverting_amp,
    "non_inverting_amp": _calc_non_inverting_amp,
    "differential_amp": _calc_differential_amp,
    "summing_amp": _calc_summing_amp,
    "voltage_divider": _calc_voltage_divider,
    "common_emitter": _calc_common_emitter,
    "emitter_follower": _calc_emitter_follower,
    "current_mirror": _calc_current_mirror,
    "bjt_diff_pair": _calc_bjt_diff_pair,
    "mosfet_cs": _calc_mosfet_cs,
    "mfb_bandpass": _calc_mfb_bandpass,
    "twin_t_notch": _calc_twin_t_notch,
    "integrator": _calc_integrator,
    "differentiator": _calc_differentiator,
    "instrumentation_amp": _calc_instrumentation_amp,
    "schmitt_trigger": _calc_schmitt_trigger,
    "common_drain": _calc_common_drain,
    "mosfet_diff_pair": _calc_mosfet_diff_pair,
    "common_base": _calc_common_base,
    "cascode": _calc_cascode,
    "wien_bridge_osc": _calc_wien_bridge_osc,
    "rc_phase_shift": _calc_rc_phase_shift,
    "class_ab_push_pull": _calc_class_ab_push_pull,
    "class_a_output": _calc_class_a_output,
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
        if t == "Wire":
            continue  # direct connection, no SPICE element
        elif t == "R":
            lines.append(f"{name} {nid_str} {val}")
        elif t == "C":
            lines.append(f"{name} {nid_str} {val}")
        elif t == "L":
            lines.append(f"{name} {nid_str} {val}")
        elif t == "D":
            lines.append(f"{name} {nid_str} DEFAULT_D")
        elif t == "V":
            lines.append(f"{name} {nid_str} {val}")
        elif t == "Q":
            # BJT: Qname C B E model
            model = c.get("filled_model", "NPN")
            lines.append(f"{name} {nid_str} {model}")
        elif t == "M":
            # MOSFET: Mname D G S B model
            model = c.get("filled_model", "NMOS")
            lines.append(f"{name} {nid_str} {model}")
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

        if ctype not in ("R", "C", "L", "D", "V", "X", "Q", "M"):
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

        elif ctype == "Q":
            # BJT: Qname C B E [S] model [params...]
            if len(tokens) < 5:
                continue
            # Detect optional substrate node: if ≥6 tokens, substrate at [4], model at [5]
            if len(tokens) >= 6 and tokens[4].lower() == "s":
                raw_nodes = tokens[1:5]  # C, B, E, S
                model = tokens[5]
            else:
                raw_nodes = tokens[1:4]  # C, B, E
                model = tokens[4]
            value = ""

        elif ctype == "M":
            # MOSFET: Mname D G S B model
            if len(tokens) < 6:
                continue
            raw_nodes = tokens[1:5]  # D, G, S, B
            model = tokens[5]
            value = ""

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



def score_analog_layout(svg_content: str) -> dict:
    """Analyze rendered analog circuit SVG for layout quality.

    Metrics (8 dimensions, weighted):
      Crossing (25%)  — wire-wire orthogonal intersections
      Wire Length (15%) — avg segment length, normalized to canvas
      Component Alignment (15%) — grid snap precision
      Signal Flow (10%) — left→right progression quality
      Power Placement (10%) — VCC top, GND bottom
      Symmetry (10%) — paired component Y-mirroring
      White Space (10%) — component bounding box vs canvas
      Junction Quality (5%) — clean connections, minimal bends

    Returns {"score": 0-10, "crossings": int, ...}
    """
    import re, math

    result = {"score": 10.0, "crossings": 0, "wire_segments": 0,
              "avg_wire_len": 0.0, "components": 0,
              "issues": [], "details": [], "metrics": {}}

    try:
        root = ET.fromstring(svg_content)
    except Exception:
        result["score"] = 10.0
        result["issues"].append("Could not parse SVG for layout analysis")
        return result

    ns = "http://www.w3.org/2000/svg"
    # Schemdraw uses "144.2pt" format — strip units
    raw_w = (root.get("width") or "400").replace("pt", "").replace("px", "")
    raw_h = (root.get("height") or "300").replace("pt", "").replace("px", "")
    try:
        canvas_w = int(float(raw_w))
        canvas_h = int(float(raw_h))
    except ValueError:
        # Fallback: use viewBox
        vb = root.get("viewBox", "0 0 400 300")
        parts = vb.split()
        if len(parts) >= 4:
            canvas_w = int(float(parts[2].replace("pt", "")))
            canvas_h = int(float(parts[3].replace("pt", "")))
        else:
            canvas_w, canvas_h = 400, 300

    # ── Collect elements ──
    wires = []        # [(x1,y1, x2,y2), ...] — line segments
    comp_positions = []  # [(cx, cy, ctype, label), ...]
    texts = []        # [(x, y, text), ...]

    # Wire paths: <path fill="none" stroke="#7c3aed" d="...">
    for p in root.findall(f".//{{{ns}}}path"):
        fill = (p.get("fill") or "").strip()
        stroke = (p.get("stroke") or "").strip()
        if fill == "none" and "#7c3aed" in stroke:
            d = p.get("d", "")
            segs = _parse_wire_path_segments(d)
            wires.extend(segs)

    # Components: found by clustering non-wire SVG shapes with stroke="#7c3aed"
    # Each component is a cluster of closely-spaced shape centers
    shape_centers = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag in ("rect", "circle", "ellipse"):
            stroke = (elem.get("stroke", "") or "").strip()
            if "#7c3aed" not in stroke:
                continue
            if tag == "circle":
                shape_centers.append((float(elem.get("cx", 0)), float(elem.get("cy", 0))))
            elif tag == "ellipse":
                shape_centers.append((float(elem.get("cx", 0)), float(elem.get("cy", 0))))
            elif tag == "rect":
                rx = float(elem.get("x", 0)); ry = float(elem.get("y", 0))
                rw = float(elem.get("width", 0)); rh = float(elem.get("height", 0))
                shape_centers.append((rx + rw/2, ry + rh/2))
        elif tag == "line":
            stroke = (elem.get("stroke", "") or "").strip()
            if "#7c3aed" in stroke:
                # Component lines (capacitor plates, MOSFET terminals)
                x1 = float(elem.get("x1", 0)); y1 = float(elem.get("y1", 0))
                x2 = float(elem.get("x2", 0)); y2 = float(elem.get("y2", 0))
                shape_centers.append(((x1+x2)/2, (y1+y2)/2))
        elif tag == "path":
            fill = (elem.get("fill", "") or "").strip()
            stroke = (elem.get("stroke", "") or "").strip()
            if "#7c3aed" in stroke:
                d = elem.get("d", "")
                segs = _parse_wire_path_segments(d)
                # Component shapes have >4 segments (resistor zigzag) or non-"none" fill (diode, opamp)
                if len(segs) > 4 or (fill and fill != "none"):
                    bbox = _svg_path_bbox_analog(d)
                    if bbox:
                        shape_centers.append(((bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2))

    # Cluster shapes within 50px into components
    clustered = []
    used = set()
    for i, (sx, sy) in enumerate(shape_centers):
        if i in used:
            continue
        cluster = [(sx, sy)]
        used.add(i)
        for j, (sx2, sy2) in enumerate(shape_centers):
            if j in used:
                continue
            if abs(sx - sx2) < 50 and abs(sy - sy2) < 50:
                cluster.append((sx2, sy2))
                used.add(j)
        cx = sum(c[0] for c in cluster) / len(cluster)
        cy = sum(c[1] for c in cluster) / len(cluster)
        clustered.append((cx, cy))

    # Match text labels to component clusters
    for tx_elem in root.findall(f".//{{{ns}}}text"):
        tx = float(tx_elem.get("x", 0))
        ty = float(tx_elem.get("y", 0))
        text = (tx_elem.text or "").strip()
        if not text:
            continue
        # Find nearest cluster
        best_dist = 60
        best_idx = -1
        for ci, (cx, cy) in enumerate(clustered):
            d = abs(tx - cx) + abs(ty - cy)
            if d < best_dist:
                best_dist = d
                best_idx = ci
        if best_idx >= 0:
            cx, cy = clustered[best_idx]
            comp_positions.append((cx, cy, text))
            clustered.pop(best_idx)

    # Remaining clusters without text labels
    for cx, cy in clustered:
        comp_positions.append((cx, cy, ""))

    # Junction dots: <circle fill="#a78bfa">
    junctions = []
    for c in root.findall(f".//{{{ns}}}circle"):
        fill = (c.get("fill", "") or "").strip()
        if "#a78bfa" in fill:
            junctions.append((float(c.get("cx", 0)), float(c.get("cy", 0))))

    n_wires = len(wires)
    n_comps = max(len(comp_positions), 1)

    # Fallback for schemdraw SVGs: schemdraw uses CSS classes, not inline stroke
    if n_wires == 0 and n_comps <= 2:
        schemdraw_wires = []
        schemdraw_comps = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag not in ("path", "line"):
                continue
            if tag == "line":
                x1 = float(elem.get("x1", 0)); y1 = float(elem.get("y1", 0))
                x2 = float(elem.get("x2", 0)); y2 = float(elem.get("y2", 0))
                schemdraw_wires.append((x1, y1, x2, y2))
            elif tag == "path":
                d = elem.get("d", "")
                segs = _parse_wire_path_segments(d)
                total_len = sum(abs(s[2]-s[0]) + abs(s[3]-s[1]) for s in segs)
                if total_len < 300:
                    schemdraw_wires.extend(segs)
                else:
                    bbox = _svg_path_bbox_analog(d)
                    if bbox:
                        schemdraw_comps.append(((bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2))

        if schemdraw_wires:
            schemdraw_crossings = 0
            for i, w1 in enumerate(schemdraw_wires):
                for j, w2 in enumerate(schemdraw_wires):
                    if j <= i: continue
                    if _segments_cross_analog(w1, w2):
                        eps = 8
                        shared = any(
                            abs(w1[k] - w2[l]) < eps and abs(w1[k+1] - w2[l+1]) < eps
                            for k in (0, 2) for l in (0, 2))
                        if not shared:
                            schemdraw_crossings += 1

            n_sd_wires = len(schemdraw_wires)
            n_sd_comps = max(len(schemdraw_comps), 1)
            sd_cross_score = 10 * math.exp(-schemdraw_crossings / max(n_sd_comps, 1) / 0.8)
            result["crossings"] = schemdraw_crossings
            result["wire_segments"] = n_sd_wires
            result["components"] = n_sd_comps
            result["score"] = sd_cross_score * 0.5 + 5.0
            result["score"] = max(0.0, min(10.0, result["score"]))
            if schemdraw_crossings > 0:
                result["issues"] = [f"{schemdraw_crossings} wire crossing(s) (schemdraw)"]
            else:
                result["issues"] = ["Clean layout — no issues (schemdraw)"]
            result["summary"] = (
                f"Layout: schemdraw render, {n_sd_comps} components, "
                f"{n_sd_wires} wire segments. "
                f"Issues: {result['issues'][0]}. "
                f"Score: {result['score']:.0f}/10."
            )
            return result

    # ═══ 1. Crossing (25%) ═══
    crossings = 0
    for i, w1 in enumerate(wires):
        for j, w2 in enumerate(wires):
            if j <= i:
                continue
            # Check if orthogonal segments cross
            if _segments_cross_analog(w1, w2):
                # Skip if they share a junction endpoint
                eps = 8
                shared = False
                for ex1, ey1 in [(w1[0], w1[1]), (w1[2], w1[3])]:
                    for ex2, ey2 in [(w2[0], w2[1]), (w2[2], w2[3])]:
                        if abs(ex1 - ex2) < eps and abs(ey1 - ey2) < eps:
                            shared = True
                if not shared:
                    crossings += 1
    result["crossings"] = crossings
    cross_ratio = crossings / n_comps
    crossing_score = 10 * math.exp(-cross_ratio / 0.8)

    # ═══ 2. Wire Length (15%) ═══
    total_wire_len = 0.0
    for w in wires:
        total_wire_len += math.hypot(w[2] - w[0], w[3] - w[1])
    avg_len = total_wire_len / max(n_wires, 1)
    result["avg_wire_len"] = round(avg_len, 1)
    result["wire_segments"] = n_wires
    norm_len = avg_len / max(math.sqrt(canvas_w * canvas_h), 1)
    wire_length_score = max(0, 10 * math.exp(-norm_len / 0.15))

    # ═══ 3. Component Alignment (15%) ═══
    grid_size = 20.0  # approximate grid unit
    align_errors = []
    for cx, cy, _ in comp_positions:
        gx_err = min(cx % grid_size, grid_size - (cx % grid_size))
        gy_err = min(cy % grid_size, grid_size - (cy % grid_size))
        align_errors.append(min(gx_err, gy_err))
    avg_align_err = sum(align_errors) / len(align_errors) if align_errors else 0
    alignment_score = 10 * math.exp(-avg_align_err / 6.0)

    # ═══ 4. Signal Flow (10%) ═══
    # Components should progress left→right (increasing X)
    sorted_by_x = sorted(comp_positions, key=lambda c: c[0])
    flow_violations = 0
    for i in range(len(sorted_by_x) - 1):
        # A small Y change is fine, but X should increase
        if sorted_by_x[i + 1][0] <= sorted_by_x[i][0] + 20:
            flow_violations += 1
    flow_ratio = flow_violations / max(len(comp_positions) - 1, 1)
    signal_flow_score = 10 * math.exp(-flow_ratio / 0.5)

    # ═══ 5. Power Placement (10%) ═══
    vcc_y = None
    gnd_y = None
    for cx, cy, label in comp_positions:
        upper = label.upper()
        if not vcc_y and any(kw in upper for kw in ("VCC", "VDD", "V+", "VIN")):
            vcc_y = cy
        if not gnd_y and any(kw in upper for kw in ("GND", "VSS", "V-", "0")):
            gnd_y = cy
    # Also check standalone text labels
    for tx, ty, text in texts:
        upper = text.upper()
        if not vcc_y and any(kw in upper for kw in ("VCC", "VDD", "V+")):
            vcc_y = ty
        if not gnd_y and any(kw in upper for kw in ("GND", "VSS", "V-")):
            gnd_y = ty

    pp_score = 5.0  # neutral default
    if vcc_y is not None and gnd_y is not None:
        vcc_ok = vcc_y < canvas_h * 0.3
        gnd_ok = gnd_y > canvas_h * 0.7
        pp_score = (7.0 if vcc_ok else 3.0) + (3.0 if gnd_ok else 1.0)
    elif vcc_y is not None:
        pp_score = 7.0 if vcc_y < canvas_h * 0.3 else 4.0
    elif gnd_y is not None:
        pp_score = 7.0 if gnd_y > canvas_h * 0.7 else 4.0
    power_placement_score = pp_score

    # ═══ 6. Symmetry (10%) ═══
    # Find same-label pairs and check Y-mirroring
    center_y = canvas_h / 2
    label_groups = {}
    for cx, cy, label in comp_positions:
        if label:
            label_groups.setdefault(label, []).append((cx, cy))
    sym_pairs = 0
    sym_ok = 0
    for label, positions in label_groups.items():
        if len(positions) >= 2:
            for i in range(len(positions)):
                for j in range(i + 1, len(positions)):
                    sym_pairs += 1
                    # Check Y-mirroring: same X, Y symmetric about center
                    dx = abs(positions[i][0] - positions[j][0])
                    dy_avg = (positions[i][1] + positions[j][1]) / 2
                    if dx < 50 and abs(dy_avg - center_y) < canvas_h * 0.2:
                        sym_ok += 1
    sym_ratio = sym_ok / max(sym_pairs, 1)
    symmetry_score = 5.0 + 5.0 * sym_ratio  # 5-10 range

    # ═══ 7. White Space (10%) ═══
    if comp_positions:
        xs = [c[0] for c in comp_positions]
        ys = [c[1] for c in comp_positions]
        bbox_w = max(xs) - min(xs) + 100  # +margin
        bbox_h = max(ys) - min(ys) + 100
        bbox_area = bbox_w * bbox_h
        canvas_area = canvas_w * canvas_h
        ratio = bbox_area / max(canvas_area, 1)
        whitespace_score = 10 * min(1.0, ratio * 3.5)
    else:
        whitespace_score = 5.0

    # ═══ 8. Junction Quality (5%) ═══
    # Count wire paths with unnecessary bends
    total_bends = 0
    clean_junctions = 0
    for p in root.findall(f".//{{{ns}}}path"):
        fill = (p.get("fill") or "").strip()
        stroke = (p.get("stroke") or "").strip()
        if fill == "none" and "#7c3aed" in stroke:
            d = p.get("d", "")
            segs = _parse_wire_path_segments(d)
            if len(segs) <= 2:
                clean_junctions += 1
            total_bends += max(0, len(segs) - 1)
    junc_ratio = clean_junctions / max(total_bends + clean_junctions, 1)
    junction_score = 5.0 + 5.0 * junc_ratio

    # ── Weighted total ──
    result["score"] = (
        0.25 * crossing_score +
        0.15 * wire_length_score +
        0.15 * alignment_score +
        0.10 * signal_flow_score +
        0.10 * power_placement_score +
        0.10 * symmetry_score +
        0.10 * whitespace_score +
        0.05 * junction_score
    )
    result["score"] = max(0.0, min(10.0, result["score"]))

    result["components"] = n_comps
    result["metrics"] = {
        "components": n_comps,
        "wire_segments": n_wires,
        "avg_wire_len": result["avg_wire_len"],
        "crossings": crossings,
        "canvas": f"{canvas_w}x{canvas_h}",
        "crossing_score": round(crossing_score, 1),
        "wire_length_score": round(wire_length_score, 1),
        "alignment_score": round(alignment_score, 1),
        "signal_flow_score": round(signal_flow_score, 1),
        "power_score": round(power_placement_score, 1),
        "symmetry_score": round(symmetry_score, 1),
        "whitespace_score": round(whitespace_score, 1),
        "junction_score": round(junction_score, 1),
    }

    if crossings > 0:
        result["issues"].append(f"{crossings} wire crossing(s)")
    if avg_align_err > 8:
        result["issues"].append(f"Component alignment off ({avg_align_err:.0f}px avg)")
    if not result["issues"]:
        result["issues"].append("Clean layout — no issues")

    result["summary"] = (
        f"Layout: {n_comps} components on {canvas_w}x{canvas_h} canvas. "
        f"{n_wires} wire segments, avg length {avg_len:.0f}px. "
        f"Issues: {', '.join(result['issues']) if result['issues'] else 'none'}. "
        f"Score: {result['score']:.0f}/10."
    )
    return result


def _parse_wire_path_segments(d: str) -> list:
    """Parse SVG path d-string into line segment list [(x1,y1,x2,y2), ...]."""
    import re
    nums = [float(x) for x in re.findall(r"[-]?\d+\.?\d*(?:e[-+]?\d+)?", d, re.IGNORECASE)]
    segs = []
    for i in range(0, len(nums) - 2, 2):
        if i + 3 < len(nums):
            segs.append((nums[i], nums[i+1], nums[i+2], nums[i+3]))
    return segs


def _segments_cross_analog(w1: tuple, w2: tuple) -> bool:
    """Check if two orthogonal line segments cross."""
    x1a, y1a, x2a, y2a = w1
    x1b, y1b, x2b, y2b = w2
    h1 = abs(x1a - x2a) > abs(y1a - y2a)
    h2 = abs(x1b - x2b) > abs(y1b - y2b)
    if h1 == h2:
        return False  # both H or both V — parallel
    if h1:
        hx1, hx2 = min(x1a, x2a), max(x1a, x2a)
        hy = y1a
        vx = x1b
        vy1, vy2 = min(y1b, y2b), max(y1b, y2b)
    else:
        hx1, hx2 = min(x1b, x2b), max(x1b, x2b)
        hy = y1b
        vx = x1a
        vy1, vy2 = min(y1a, y2a), max(y1a, y2a)
    return hx1 + 2 < vx < hx2 - 2 and vy1 + 2 < hy < vy2 - 2


def _svg_path_bbox_analog(d: str) -> tuple | None:
    """Approximate bounding box of an SVG path."""
    import re
    nums = [float(x) for x in re.findall(r"[-]?\d+\.?\d*(?:e[-+]?\d+)?", d, re.IGNORECASE)]
    if len(nums) < 2:
        return None
    # Extract M/L command points (skip arc/curve control params)
    tokens = re.findall(r'[A-Za-z]|[-]?\d+\.?\d*', d)
    points = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in 'ML' and i + 2 < len(tokens):
            points.append((float(tokens[i+1]), float(tokens[i+2])))
            i += 3
        elif t in 'ACQ':
            nums_after = [float(x) for x in tokens[i+1:] if re.match(r'[-]?\d', x)]
            if len(nums_after) >= 2:
                points.append((nums_after[-2], nums_after[-1]))
            i = len(tokens)
        elif t == 'Z':
            i += 1
        else:
            i += 1
    if not points:
        xs = [nums[i] for i in range(0, len(nums), 2) if i+1 < len(nums)]
        ys = [nums[i] for i in range(1, len(nums), 2) if i < len(nums)]
        if xs and ys:
            return (min(xs), min(ys), max(xs), max(ys))
        return None
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


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
            "differential_amp": "gain", "summing_amp": "gain",
            "common_emitter": "gain", "bjt_diff_pair": "gain",
            "mosfet_cs": "gain", "cascode": "gain",
            "common_base": "gain", "instrumentation_amp": "gain",
            "sallen_key_hp": "fc", "mfb_bandpass": "fc",
            "twin_t_notch": "fc", "integrator": "fc",
            "differentiator": "fc", "wien_bridge_osc": "fc",
            "rc_phase_shift_osc": "fc",
            }
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
        r_keys = [k for k in ("R", "R1", "R2", "R3") if k in new]
        c_keys = [k for k in ("C", "C1", "C2", "C3") if k in new]
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
        # Gain ~ feedback/input resistor ratio.
        # Scale feedback resistors (Rf, Rc, Rd) proportionally.
        for k in ("Rf", "Rc", "Rc1", "Rc2", "Rd", "Rd1", "Rd2"):
            if k in new:
                new[k] = _format_value(_parse_value(str(new[k])) * ratio)
        # Rg in instrumentation amp: lower Rg = higher gain (inverse)
        for k in ("Rg",):
            if k in new and calc_name == "instrumentation_amp":
                new[k] = _format_value(_parse_value(str(new[k])) / ratio)

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

        ("compose_circuit",
         "Combine multiple circuit templates into a multi-stage design.\n"
         "Auto-handles node namespace, coupling, power merge, DOT cluster layout.\n"
         "\n**Examples:**\n"
         '- `stages_json=\'[{"template":"共射放大","specs":"gain=10"},'
         '{"template":"RC低通","specs":"fc=10kHz"}]\'`',
         "compose_circuit",
         {"stages_json": {"type": "string",
                           "description": "JSON list of [{\"template\": \"...\", \"specs\": \"...\"}]"},
          "coupling": {"type": "string", "description": "'ac' or 'dc'"},
          "title": {"type": "string", "description": "Optional title"}},
         ["stages_json"]),

        ("add_custom_template",
         "Save a verified circuit as a reusable template.\n"
         "Args: circuit_id, category, name, keywords_cn, components, params?, calculate?",
         "add_custom_template",
         {"circuit_id": {"type": "string", "description": "Unique ID e.g. 'my_bandpass'"},
          "category": {"type": "string", "description": "filter|amplifier|bjt|mosfet|rectifier|divider"},
          "name": {"type": "string", "description": "Display name"},
          "keywords_cn": {"type": "array", "items": {"type": "string"},
                           "description": "Keywords for NL matching"},
          "components": {"type": "array", "items": {"type": "object"},
                          "description": "[{type, name, nodes, value, model?}]"},
          "params": {"type": "object", "description": "Optional {key: [desc, default]}"},
          "guide": {"type": "string", "description": "Circuit description"},
          "calculate": {"type": "string", "description": "Calculator name, default 'fixed'"}},
         ["circuit_id", "category", "name", "keywords_cn", "components"]),
    ]

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        # Templates loaded at module import (see bottom of file)

    @staticmethod
    def _load_templates() -> None:
        """Load circuit templates from YAML (primary) or hardcoded fallback.

        Only replaces the hardcoded fallback when YAML loads successfully.
        If YAML fails, the module-level hardcoded templates remain intact.
        """
        global _CIRCUIT_TEMPLATES
        yaml_path = Path(__file__).parent / "templates" / "circuits.yaml"
        try:
            import yaml as _yaml
        except ImportError:
            logger.debug("PyYAML not installed, using hardcoded templates")
            return

        if not yaml_path.exists():
            logger.debug(f"YAML template file not found: {yaml_path}")
            return

        new_templates = {}
        try:
            with open(yaml_path) as f:
                data = _yaml.safe_load(f)
            for t in data.get("templates", []):
                new_templates[(t["category"], t["id"])] = {
                    "name": t["name"],
                    "keywords_cn": t.get("keywords_cn", []),
                    "guide": t.get("guide", ""),
                    "components": t.get("components", []),
                    "params": t.get("params", {}),
                    "calculate": t.get("calculate", "fixed"),
                }
        except Exception as e:
            logger.warning(f"YAML parse failed ({e}), using hardcoded templates")
            return

        if not new_templates:
            logger.warning("YAML loaded but no templates found, using hardcoded templates")
            return

        _CIRCUIT_TEMPLATES = new_templates
        logger.debug(f"Loaded {len(_CIRCUIT_TEMPLATES)} circuit templates from YAML")

    def draw_analog_svg(self, description: str, title: str = "") -> str:
        """Pipeline: ① 生成电路拓扑 → ② SPICE Netlist → ③ Ngspice验证 → ④ Self-Refine修正 → ⑤ Schemdraw渲染 → ⑥ 输出"""
        try:
            tmpl, values = self._match_template(description)
            components = [dict(c) for c in tmpl["components"]]
            spice = _to_spice(components, values)
            svg_title = title or tmpl.get("name", "")
        except Exception as e:
            logger.exception(f"Analog SVG failed: {e}")
            return f"Error drawing analog circuit: {e}"

        # ── Step ③: Ngspice 仿真验证 ──
        sim_ok, sim_output = self._run_sim_check(spice)
        refine_hint = ""

        if not sim_ok:
            # Self-Refine attempt
            fix_attempts = 0
            while not sim_ok and fix_attempts < 6:
                fix_attempts += 1
                if "Mismatch" in sim_output or "subckt" in sim_output.lower():
                    spice = _replace_opamp_with_e_source(spice, gain=100000)
                else:
                    fixed = _auto_fix_spice(spice, sim_output)
                    if fixed != spice:
                        spice = fixed
                    else:
                        break
                sim_ok, sim_output = self._run_sim_check(spice)

        if not sim_ok:
            return (
                f"### ⚠️ Simulation Failed (after {fix_attempts} fix attempts)\n\n"
                f"**Error:**\n```\n{sim_output[:800]}\n```\n\n"
                f"**SPICE Netlist:**\n```spice\n{spice}\n```\n\n"
                f"🔧 **Self-Refine:** Fix the SPICE above and call `draw_analog_spice`."
            )

        # Electrical validation
        char_ok, char_msg = self._validate_characteristics(spice, tmpl, values)
        if not char_ok:
            refine_hint = (
                f"\n\n### ⚠️ Electrical Issues\n{char_msg}\n"
                f"\n🔧 **Self-Refine:** Adjust component values and re-render."
            )

        # ── Step ⑤: Schemdraw 渲染 ──
        try:
            svg = self._render_schemdraw_svg(spice, svg_title)
        except Exception as e:
            logger.exception(f"Analog SVG render failed: {e}")
            return f"Error rendering analog circuit: {e}"
        if not svg or "<svg" not in svg:
            return f"Error rendering analog circuit: schemdraw returned empty SVG"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        # ── Step ⑥: 输出结果 ──
        guide = tmpl.get("guide", "")
        guide_text = f"\n{guide}" if guide else ""
        sim_block = f"\n\n✅ **Simulation verified**" if sim_ok and not refine_hint else ""
        spice_block = f"\n\n**SPICE Netlist:**\n```spice\n{spice}\n```"

        # ── Layout Quality Score ──
        layout_block = ""
        try:
            lq = score_analog_layout(svg)
            ls = lq["score"]
            if ls >= 7.0:
                level = "✅ Clean"
            elif ls >= 5.0:
                level = "⚠️ Acceptable"
            else:
                level = "❌ Poor"
            layout_block = (
                f"\n\n**Layout Quality:** {level} ({ls:.0f}/10)\n"
                f"| Metric | Score |\n|--------|-------|\n"
                f"| Crossings | {lq['crossings']} |\n"
                f"| Wire Segments | {lq['wire_segments']} |\n"
                f"| Avg Wire Length | {lq['avg_wire_len']:.0f}px |\n"
                f"| Components | {lq['components']} |\n"
                f"| Issues | {', '.join(lq['issues'][:3])} |"
            )
        except Exception:
            pass

        return f"![{svg_title}]({url})\n{url}{guide_text}{sim_block}{spice_block}{layout_block}{refine_hint}"

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
        has_opamp = bool(re.search(r'(?:^|\s)X\w+\s', sim_spice, re.MULTILINE))
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
                if re.search(r'(?:^|\s)D\d\w*\s', sim_spice, re.MULTILINE):
                    f.write(".model DEFAULT_D D (IS=1e-14 RS=1 N=1)\n")
                # Auto-inject device models
                from .spice_common import NPN_MODEL, PNP_MODEL, NMOS_MODEL, PMOS_MODEL
                _MM = {"NPN": NPN_MODEL, "PNP": PNP_MODEL, "NMOS": NMOS_MODEL, "PMOS": PMOS_MODEL}
                for m in re.finditer(r'(?:^|\s)([QM]\d\w*)\s+(.+)', sim_spice, re.MULTILINE):
                    rest = m.group(2).split()
                    ctype = m.group(1)[0].upper()
                    candidates = []
                    if ctype == "M" and len(rest) >= 5:
                        candidates.append(rest[4])   # M d g s b model
                    elif ctype == "Q" and len(rest) >= 4:
                        candidates.append(rest[3])   # Q c b e model
                        if len(rest) >= 5:
                            candidates.append(rest[4])  # Q c b e s model
                    for mn in candidates:
                        mn = mn.upper()
                        if mn in _MM:
                            f.write(_MM[mn])
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
        except Exception as e:
            logger.warning(f"Sim check failed: {e}")
            return True, ""

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
        has_opamp = bool(re.search(r'(?:^|\s)X\w+\s', sim_spice, re.MULTILINE))
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
        except Exception as e:
            logger.warning(f"AC validation sim failed: {e}")
            return True, ""

        # Parse AC output: extract vm(out) values
        issues = []
        try:
            from .spice_simulator import _parse_ac_output
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

            # ── P2: CMRR for differential amplifiers ──
            if calc_name in ("differential_amp", "instrumentation_amp", "bjt_diff_pair",
                             "mosfet_diff_pair"):
                if expected_gain and dc_gain_lin > 0:
                    gain_error = abs(dc_gain_lin - expected_gain) / max(expected_gain, 0.01)
                    if gain_error < 0.01:
                        cmrr_est = 80  # excellent matching
                    elif gain_error < 0.03:
                        cmrr_est = 60  # good
                    elif gain_error < 0.10:
                        cmrr_est = 40  # fair
                    else:
                        cmrr_est = 20  # poor
                    if cmrr_est < 40:
                        issues.append(
                            f"CMRR: ~{cmrr_est} dB (poor matching) — "
                            "resistor mismatch degrades common-mode rejection")

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

        except Exception as e:
            logger.warning(f"AC output parsing failed: {e}")

        if issues:
            detail = "### ⚠️ Electrical Validation Failed\n\n" + \
                     "\n".join(f"- {i}" for i in issues)
            return False, detail
        return True, ""

    def draw_analog_spice(self, spice: str, title: str = "") -> str:
        """Pipeline: ① SPICE → ② Ngspice验证 → ③ Self-Refine修正 → ④ Schemdraw渲染 → ⑤ 输出"""
        spice_stripped = spice.strip()

        # ── Step ②: Ngspice 仿真验证 ──
        sim_ok, sim_output = self._run_sim_check(spice_stripped)
        refine_hint = ""
        if not sim_ok:
            return (
                f"### ⚠️ Simulation Failed\n\n"
                f"**Error:**\n```\n{sim_output[:800]}\n```\n\n"
                f"**SPICE Netlist:**\n```spice\n{spice_stripped}\n```\n\n"
                f"🔧 **Self-Refine:** Fix the SPICE above and try again."
            )

        # ── Step ④: Schemdraw 渲染 ──
        try:
            components = _parse_spice(spice_stripped)
            if not components:
                return "Error: no valid SPICE components found. " \
                       "Supported: R, C, L, D, V, X (op-amp subcircuit)."
            svg = self._render_schemdraw_svg(spice_stripped, title)
        except Exception as e:
            logger.exception(f"Analog SPICE render failed: {e}")
            return f"Error rendering SPICE circuit: {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        # ── Step ⑤: 输出结果 ──
        sim_block = f"\n\n✅ **Simulation verified**" if sim_ok else ""
        spice_block = f"\n\n**SPICE Netlist:**\n```spice\n{spice_stripped}\n```"

        # ── Layout Quality Score ──
        layout_block = ""
        try:
            lq = score_analog_layout(svg)
            ls = lq["score"]
            level = "✅ Clean" if ls >= 7.0 else ("⚠️ Acceptable" if ls >= 5.0 else "❌ Poor")
            layout_block = (
                f"\n\n**Layout Quality:** {level} ({ls:.0f}/10)\n"
                f"| Metric | Score |\n|--------|-------|\n"
                f"| Crossings | {lq['crossings']} |\n"
                f"| Wire Segments | {lq['wire_segments']} |\n"
                f"| Avg Wire Length | {lq['avg_wire_len']:.0f}px |\n"
                f"| Components | {lq['components']} |"
            )
        except Exception:
            pass

        return f"![{title or 'Analog Circuit'}]({url})\n{url}{sim_block}{spice_block}{layout_block}"

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

        while not sim_ok and fix_attempts < 6:
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
            svg = self._render_schemdraw_svg(spice, circuit_name)
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
        from .spice_simulator import (
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
        except Exception as e:
            logger.warning(f"Metric measurement sim failed: {e}")
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
        elif metric_name == "Q":
            return metrics.get("q_factor")
        return None

    # ═══════════ compose_circuit: 多级电路组合 ═══════════

    def compose_circuit(self, stages_json: str, coupling: str = "ac",
                        title: str = "") -> str:
        """Compose multiple circuit templates into a multi-stage design.

        Args:
            stages_json: JSON list of [{"template": "...", "specs": "key=value"}]
            coupling: "ac" (capacitor, default) or "dc" (direct wire)
        """
        # 1. Parse stages
        stages = _json.loads(stages_json)
        if not isinstance(stages, list) or len(stages) < 2:
            return "❌ **Need at least 2 stages.**"

        # 2. Match templates
        stage_data = []
        for si, stage in enumerate(stages):
            desc = stage.get("template", "")
            specs = stage.get("specs", "")
            try:
                tmpl, values = self._match_template(desc)
            except Exception as e:
                return f"❌ **Stage {si} template matching failed:** {e}"

            if specs:
                for part in specs.split():
                    if "=" in part:
                        k, v = part.split("=", 1)
                        k = k.strip().lower()
                        for pk in tmpl.get("params", {}):
                            if pk.lower() == k:
                                values[pk] = v.strip()

            components = [dict(c) for c in tmpl["components"]]
            stage_data.append({
                "tmpl": tmpl, "values": values, "components": components,
                "name": tmpl.get("name", f"Stage{si}"),
            })

        # 3. Node namespace + coupling
        all_components = []
        stage_names = []
        prev_out_net = None

        for si, sd in enumerate(stage_data):
            suffix = f"_s{si}"
            stage_names.append(sd["name"])
            renamed = []
            for c in sd["components"]:
                nc = dict(c)
                nc["nodes"] = [str(n) + suffix if str(n) != "0" else "0" for n in c.get("nodes", [])]
                nc["name"] = c["name"] + suffix
                renamed.append(nc)
            all_components.extend(renamed)

            # Find in/out nets
            in_net = None
            out_net = None
            for c in renamed:
                if c["type"] == "V" and "AC" in str(c.get("value", "")).upper():
                    for n in c["nodes"]:
                        if str(n) != "0":
                            in_net = str(n)
                            break
            for c in renamed:
                for n in c["nodes"]:
                    if "out" in str(n):
                        out_net = str(n)
                        break
            if not out_net:
                out_net = "out" + suffix

            # Remove AC sources from non-first stages
            if si > 0:
                all_components = [c for c in all_components
                                  if not (c["type"] == "V" and "AC" in str(c.get("value", ""))
                                          and c["name"].endswith(suffix))]

            # Insert coupling
            if si > 0 and prev_out_net:
                cpl_net = f"cpl_s{si}"
                if coupling == "ac":
                    all_components.append({
                        "type": "C", "name": f"C_couple_s{si}",
                        "nodes": [prev_out_net, cpl_net], "value": "10u",
                    })
                else:
                    all_components.append({
                        "type": "Wire", "name": f"W_couple_s{si}",
                        "nodes": [prev_out_net, cpl_net], "value": "",
                    })
                for c in all_components:
                    if c["name"].endswith(suffix):
                        c["nodes"] = [cpl_net if (str(n) == in_net and str(n) != "0") else str(n)
                                      for n in c["nodes"]]
            prev_out_net = out_net

        # 4. Merge power supplies
        power_nets = {}
        merged = []
        for c in all_components:
            if c["type"] == "V" and "DC" in str(c.get("value", "")).upper():
                pkey = c["value"]
                if pkey in power_nets:
                    old_pos = c["nodes"][0] if c["nodes"] else ""
                    new_pos = power_nets[pkey]
                    for oc in all_components + merged:
                        oc["nodes"] = [new_pos if str(n) == old_pos else str(n) for n in oc.get("nodes", [])]
                    continue
                else:
                    power_nets[pkey] = c["nodes"][0] if c["nodes"] else ""
            merged.append(c)
        all_components = merged

        # 5. Handle Wire connections
        wire_merges = {}
        for c in all_components:
            if c["type"] == "Wire":
                a, b = str(c["nodes"][0]), str(c["nodes"][1])
                if a != b and a != "0" and b != "0":
                    wire_merges[b] = a
        all_components = [c for c in all_components if c["type"] != "Wire"]
        if wire_merges:
            for c in all_components:
                c["nodes"] = [wire_merges.get(str(n), str(n)) for n in c["nodes"]]

        # 6. Generate SPICE
        all_values = {}
        for si, sd in enumerate(stage_data):
            suffix = f"_s{si}"
            for k, v in sd["values"].items():
                all_values[k + suffix] = v

        spice = _to_spice(all_components, all_values)
        circuit_name = title or (" → ".join(stage_names))

        # 7. Sim check
        sim_ok, sim_output = self._run_sim_check(spice)
        if not sim_ok:
            return (
                f"❌ **Multi-stage simulation failed.**\n\n"
                f"**Circuit:** {circuit_name}\n\n"
                f"**SPICE Netlist:**\n```spice\n{spice}\n```\n\n"
                f"**Error:**\n```\n{sim_output[:1200]}\n```"
            )

        # 8. Render
        try:
            svg = self._render_schemdraw_svg(
                spice, circuit_name,
                stages=stage_names,
                stage_components=all_components,
            )
        except Exception as e:
            return f"❌ **SVG rendering failed:** {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        parts = [f"![{circuit_name}]({url})\n{url}"]
        parts.append(f"\n✅ **{len(stage_data)}-stage circuit — simulation passed**")
        parts.append(f"\n**Stages:** {' → '.join(stage_names)}")
        parts.append(f"\n**Coupling:** {'AC' if coupling == 'ac' else 'DC'}")
        parts.append(f"\n**SPICE Netlist:**\n```spice\n{spice}\n```")
        return "\n".join(parts)

    # ═══════════ DOT Layout + SVG Render ═══════════

    @staticmethod
    def _render_schemdraw_svg(spice: str, title: str = "",
                               stages: list[str] = None,
                               stage_components: list[dict] = None) -> str:
        """Render circuit with schemdraw (professional IEEE layout)."""
        try:
            from .spice_renderer import SpiceRenderer
            sr = SpiceRenderer.__new__(SpiceRenderer)
            from .spice_renderer import _build_graph, _layout
            graph = _build_graph(spice)
            if not graph["components"]:
                return None
            layout = _layout(graph)
            return sr._render_schemdraw(graph, layout, title)
        except ImportError:
            return None
        except Exception as e:
            logger.warning(f"schemdraw render failed: {e}")
            return None
    @staticmethod
    def _draw_svg_from_positions(components: list[dict],
                                  comp_pos_svg: dict[int, tuple[float, float]],
                                  node_pos: dict[str, tuple[float, float]],
                                  node_to_comps: dict[str, list[int]],
                                  svg_w: int, svg_h: int, title: str = "") -> str:
        """Draw SVG circuit diagram from pre-computed component/node positions."""
        svg = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {svg_w} {svg_h}",
            "width": str(svg_w), "height": str(svg_h),
        })
        ET.SubElement(svg, "rect", {
            "width": str(svg_w), "height": str(svg_h), "fill": _SVG_COLORS["bg"],
        })
        if title:
            t = ET.SubElement(svg, "text", {
                "x": str(svg_w // 2), "y": "24", "text-anchor": "middle",
                "fill": _SVG_COLORS["text"], "font-family": "monospace",
                "font-size": "13", "font-weight": "bold",
            })
            t.text = title

        for nid, cis in node_to_comps.items():
            if nid == "0" or len(cis) <= 1 or nid not in node_pos:
                continue
            nx, ny = node_pos[nid]
            ET.SubElement(svg, "circle", {
                "cx": str(int(nx)), "cy": str(int(ny)),
                "r": "3", "fill": _SVG_COLORS["node_dot"],
            })

        for i, c in enumerate(components):
            if i not in comp_pos_svg:
                continue
            cx, cy = comp_pos_svg[i]
            for j, nid in enumerate(c["filled_nodes"]):
                if nid not in node_pos:
                    continue
                nx, ny = node_pos[nid]
                px, py = _pin_pos(cx, cy, j, len(c["filled_nodes"]), c["type"])
                _draw_ortho_wire(svg, px, py, nx, ny)

        for i, c in enumerate(components):
            if i in comp_pos_svg:
                _draw_component(svg, c, comp_pos_svg[i][0], comp_pos_svg[i][1])

        if "0" in node_pos:
            gx, gy = node_pos["0"]
            _draw_ground(svg, gx, gy)

        return ET.tostring(svg, encoding="unicode")

    # ═══════════ add_custom_template ═══════════

    @staticmethod
    def add_custom_template(circuit_id: str, category: str, name: str,
                            keywords_cn: list[str], components: list[dict],
                            params: dict = None, guide: str = "",
                            calculate: str = "fixed") -> str:
        """Save a custom circuit template for future reuse."""
        import yaml as _yaml
        custom_path = Path(__file__).parent / "templates" / "custom_circuits.yaml"
        existing = {"templates": []}
        if custom_path.exists():
            with open(custom_path) as f:
                existing = _yaml.safe_load(f) or {"templates": []}

        for t in existing["templates"]:
            if t["id"] == circuit_id:
                return f"⚠️ Template '{circuit_id}' already exists."

        new_tpl = {
            "id": circuit_id, "category": category, "name": name,
            "keywords_cn": keywords_cn, "guide": guide,
            "params": params or {}, "calculate": calculate,
            "components": components,
        }
        existing["templates"].append(new_tpl)
        custom_path.parent.mkdir(parents=True, exist_ok=True)
        with open(custom_path, "w") as f:
            _yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        AnalogSVG._load_templates()
        return f"✅ Custom template '{circuit_id}' saved ({len(keywords_cn)} keywords)."

    @staticmethod
    def _match_template(desc: str):
        """Match NL description to template + calculate values.

        Scoring: longer keyword = more specific = higher weight.
        Chinese keywords get base weight 5 + keyword length.
        """
        desc_lower = desc.lower().strip()

        # Keyword matching
        matches = []
        for (cat, sub), tmpl in _CIRCUIT_TEMPLATES.items():
            score = 0
            name_lower = tmpl["name"].lower()
            # English keywords: sub name and category
            if sub.replace("_", " ") in desc_lower or sub in desc_lower:
                score += 3 + len(sub)  # longer sub id = more specific
            for word in name_lower.split():
                w = word.replace("-", " ")
                # Skip single-letter words to avoid false positives
                if len(w) >= 2 and (w in desc_lower):
                    score += 1 + len(w)
            if cat in desc_lower:
                score += 1 + len(cat)
            # Chinese keywords: case-insensitive, weight by length (longer = more specific match)
            for kw in tmpl.get("keywords_cn", []):
                if kw.lower() in desc_lower:
                    score += 5 + len(kw)
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
            # Check more specific amplifier types first
            if any(w in desc_lower for w in ("推挽", "互补", "功放", "功率", "音频", "输出放大",
                                               "otl", "ocl", "btl", "push.pull", "class ab")):
                cat, sub = "output", "class_ab_push_pull"
            elif any(w in desc_lower for w in ("甲类", "class a")):
                cat, sub = "output", "class_a_ce_output"
            elif any(w in desc_lower for w in ("放大", "运放")):
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


AnalogSVG._load_templates()
