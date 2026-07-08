"""SPICE 仿真器 — ngspice 批量仿真 + 结果解析 + 指标提取。

Pipeline:
  SPICE Netlist → Netlist Prep → ngspice -b → Parse Output → Structured Metrics

三合一闭环:
  draw_analog_spice  → 画电路图
  simulate_spice     → 跑仿真 + 解析结果
  LLM 分析结果       → 修改 SPICE → 重新 simulate_spice → 循环优化
"""

import csv
import io
import logging
import math
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .spice_common import (
    GROUND_NAMES, OPAMP_SUBCKT, DIODE_MODEL,
    check_ngspice, check_subckt_support,
    replace_opamp_with_e_source,
    has_opamp, has_diode, extract_nodes,
)

logger = logging.getLogger("nano_agent.tools.spice_simulator")

# Backward compat aliases
_GROUND_NAMES = GROUND_NAMES
_OPAMP_SUBCKT = OPAMP_SUBCKT
_DIODE_MODEL = DIODE_MODEL
_check_ngspice = check_ngspice
_check_subckt_support = check_subckt_support
_replace_opamp_with_e_source = replace_opamp_with_e_source
_extract_nodes = extract_nodes

# ═══════════════════════════════════════════════════════════════

def _prep_netlist(spice_text: str, analysis: str = "") -> tuple[str, str]:
    """Prepare SPICE netlist for ngspice batch mode.

    Returns (prepared_netlist, detected_analysis_type).
    """
    text = spice_text.strip()

    # SPICE convention: first line is always a title. If it looks like a component
    # (starts with R/C/L/D/V/X/M/Q), prepend a title line to prevent it being swallowed.
    if text and text.split("\n")[0].strip()[0].upper() in "RCLDVXMQ":
        text = "Simulation\n" + text

    # Detect analysis type
    if not analysis:
        if re.search(r'\.ac\b', text, re.IGNORECASE):
            analysis = "ac"
        elif re.search(r'\.tran\b', text, re.IGNORECASE):
            analysis = "tran"
        elif re.search(r'\.op\b', text, re.IGNORECASE):
            analysis = "op"
        else:
            analysis = "op"  # default

    # Strip existing print commands (we'll add our own)
    text = re.sub(r'\.print\s+.*', '', text, flags=re.IGNORECASE)

    # Keep .ac/.tran/.op
    has_analysis_cmd = bool(re.search(
        r'\.(ac|tran|op)\b', text, re.IGNORECASE))

    # Inject opamp/diode models if needed
    # Match component lines specifically (X/D at line start, not in values like "DC 0")
    has_opamp = bool(re.search(r'(?:^|\s)X\w+\s', text, re.MULTILINE))
    has_diode = bool(re.search(r'(?:^|\s)D\d\w*\s', text, re.MULTILINE))

    if has_opamp:
        if _check_subckt_support():
            # ngspice supports .subckt — inject behavioral opamp model
            if ".subckt opamp" not in text.lower():
                text = _OPAMP_SUBCKT.strip() + "\n" + text
        else:
            # ngspice subcircuit broken (Homebrew Apple Silicon) — use inline E-source
            text = _replace_opamp_with_e_source(text)

    if has_diode and ".model" not in text.lower():
        text = _DIODE_MODEL.strip() + "\n" + text

    # Auto-inject BJT/MOSFET models — detect which models are actually used
    models_needed = set()
    for m in re.finditer(r'(?:^|\s)([QM]\d\w*)\s+(.+)', text, re.MULTILINE):
        rest = m.group(2).split()
        ctype = m.group(1)[0].upper()
        if ctype == "M" and len(rest) >= 5:
            models_needed.add(rest[4].upper())   # M d g s b model
        elif ctype == "Q" and len(rest) >= 4:
            models_needed.add(rest[3].upper())   # Q c b e model
            if len(rest) >= 5:
                models_needed.add(rest[4].upper())  # Q c b e s model
    from .spice_common import NPN_MODEL, PNP_MODEL, NMOS_MODEL, PMOS_MODEL
    _MODEL_MAP = {"NPN": NPN_MODEL, "PNP": PNP_MODEL,
                  "NMOS": NMOS_MODEL, "PMOS": PMOS_MODEL}
    for mn in sorted(models_needed):
        if mn in _MODEL_MAP and not re.search(
            r'\.model\s+' + re.escape(mn) + r'\b', text, re.IGNORECASE
        ):
            text = _MODEL_MAP[mn].strip() + "\n" + text

    # Get non-ground nodes for .print
    nodes = _extract_nodes(text)

    # Add analysis if missing
    if not has_analysis_cmd:
        if analysis == "ac":
            text += "\n.ac dec 50 1 1e6"
        elif analysis == "tran":
            text += "\n.tran 1u 1m"
        else:
            text += "\n.op"

    # Add .print commands
    if analysis == "ac" and nodes:
        node_exprs = " ".join(f"vm({n}) vp({n})" for n in nodes[:5])
        text += f"\n.print ac {node_exprs}"
    elif analysis == "tran" and nodes:
        node_exprs = " ".join(f"v({n})" for n in nodes[:5])
        text += f"\n.print tran {node_exprs}"
    # .op prints voltages automatically, no .print needed

    if not text.strip().endswith(".end"):
        text += "\n.end"

    return text, analysis


# ═══════════════════════════════════════════════════════════════
# 结果解析
# ═══════════════════════════════════════════════════════════════

def _parse_op_output(output: str) -> dict:
    """Parse ngspice DC operating point output.

    Returns {"nodes": {name: voltage}, "sources": {name: current},
             "warnings": [...], "raw_table": str}
    """
    result = {"nodes": {}, "sources": {}, "warnings": [], "raw_table": ""}

    # Parse node voltages
    in_voltage_section = False
    in_current_section = False
    for line in output.split("\n"):
        stripped = line.strip()

        if "Node" in stripped and "Voltage" in stripped:
            in_voltage_section = True
            in_current_section = False
            continue
        if "Source" in stripped and "Current" in stripped:
            in_voltage_section = False
            in_current_section = True
            continue
        if stripped.startswith("---") or not stripped:
            continue

        if in_voltage_section:
            # Match: V(1)  5.000000e+00
            m = re.match(r'V\((\S+)\)\s+([\d\.\+\-e]+)', stripped)
            if m:
                node_name = m.group(1)
                voltage = float(m.group(2))
                result["nodes"][node_name] = voltage

        if in_current_section:
            # Match: v1#branch  -1.66667e-03
            m = re.match(r'(\S+#?\S*)\s+([\d\.\+\-e]+)\s*$', stripped)
            if m:
                try:
                    current = float(m.group(2))
                    result["sources"][m.group(1)] = current
                except ValueError:
                    continue
            elif stripped and not stripped.startswith("---"):
                # Non-data line in current section → end of section
                if not re.fullmatch(r'[\d\.\+\-e\s]+', stripped):
                    in_current_section = False

    # Health checks
    for node, voltage in result["nodes"].items():
        if abs(voltage) < 1e-12:
            result["warnings"].append(f"Node {node} is at 0V — possible open circuit or missing connection")

    # Calculate total power from sources
    total_power = 0.0
    for src, current in result["sources"].items():
        # Find the source's positive node voltage
        src_base = src.replace("#branch", "")
        for node, voltage in result["nodes"].items():
            if node == src_base or f"v({node})" == src_base:
                total_power += abs(voltage * current)
                break
    if total_power > 0:
        result["total_power"] = total_power

    return result


def _parse_ngspice_table(output: str, x_col: str, col_filter) -> dict:
    """Common ngspice table parser — handles form-feed pagination and page merging.

    Args:
        output: raw ngspice stdout+stderr
        x_col: independent variable column name ('frequency' or 'time')
        col_filter: predicate(h) → bool for data column headers

    Returns: {x_values: {row_idx: x_val}, col_data: {col_name: {row_idx: val}},
              num_rows: N, error: str|None}
    """
    cleaned = output.replace("\f", "\n")
    x_values = {}
    col_data = {}
    current_cols = []
    in_table = False
    max_row_idx = -1

    for line in cleaned.split("\n"):
        stripped = line.strip()

        if "Index" in stripped and x_col in stripped.lower():
            in_table = True
            parts = stripped.split()
            current_cols = [h for h in parts[1:] if col_filter(h)]
            continue

        if stripped.startswith("---") or not stripped:
            if not stripped and in_table:
                in_table = False
            continue

        if in_table:
            parts = stripped.split()
            if len(parts) < 2:
                continue
            try:
                row = [float(p) for p in parts]
            except ValueError:
                continue
            if len(row) < 2:
                continue

            row_idx = int(row[0])
            x_values[row_idx] = row[1]
            max_row_idx = max(max_row_idx, row_idx)

            for ci in range(2, len(row)):
                col_name = current_cols[ci - 2] if ci - 2 < len(current_cols) else f"col_{ci}"
                if col_name == x_col:
                    continue
                col_data.setdefault(col_name, {})[row_idx] = row[ci]

    return {"x_values": x_values, "col_data": col_data,
            "num_rows": max_row_idx + 1 if max_row_idx >= 0 else 0,
            "error": None if max_row_idx >= 0 else f"No {x_col} data rows found"}


def _parse_ac_output(output: str) -> dict:
    """Parse ngspice AC analysis output via shared table parser."""
    r = _parse_ngspice_table(output, "frequency",
                             lambda h: h.startswith(("vm(", "vdb(", "vp(")))
    if r["error"]:
        return {"frequencies": [], "data": {}, "num_points": 0,
                "freq_range": (0, 0), "error": r["error"]}

    n = r["num_rows"]
    result = {"num_points": n, "frequencies": [r["x_values"].get(i, 0.0) for i in range(n)],
              "data": {}, "freq_range": (0, 0)}
    if result["frequencies"]:
        result["freq_range"] = (result["frequencies"][0], result["frequencies"][-1])

    for col_name, idx_vals in r["col_data"].items():
        vals = [idx_vals.get(i, 0.0) for i in range(n)]
        if col_name.startswith("vm("):
            db_name = col_name.replace("vm(", "vdb(", 1)
            result["data"][db_name] = [
                20.0 * math.log10(abs(v)) if abs(v) > 1e-15 else -200.0 for v in vals
            ]
        else:
            result["data"][col_name] = vals
    return result


def _parse_tran_output(output: str) -> dict:
    """Parse ngspice transient analysis output via shared table parser."""
    r = _parse_ngspice_table(output, "time", lambda h: h.startswith("v("))
    if r["error"]:
        return {"time": [], "signals": {}, "num_points": 0, "error": r["error"]}

    n = r["num_rows"]
    result = {"num_points": n, "time": [r["x_values"].get(i, 0.0) for i in range(n)],
              "signals": {}}
    for name, idx_vals in r["col_data"].items():
        result["signals"][name] = [idx_vals.get(i, 0.0) for i in range(n)]
    return result


# ═══════════════════════════════════════════════════════════════
# 指标计算
# ═══════════════════════════════════════════════════════════════

def _compute_filter_q(db_data: list[float], freqs: list[float],
                      ref_gain: float, filter_type: str) -> float | None:
    """Estimate filter Q factor from AC sweep data.

    Band-pass: Q = f0 / BW_3dB (exact)
    Low-/high-pass with peaking: Q ≈ 10^(peaking_dB/20) * 0.6 (empirical)
    No peaking: Q ≈ 0.5 (overdamped, typical of unity-gain Sallen-Key)

    Returns None if insufficient data.
    """
    if not db_data or not freqs or len(db_data) < 3:
        return None

    max_gain = max(db_data)
    max_idx = db_data.index(max_gain)
    peaking_db = max_gain - ref_gain

    if filter_type == "band-pass":
        # Exact: Q = f0 / BW_3dB
        target = max_gain - 3.0
        f_low, f_high = None, None
        # Search left of peak: gain decreases as index decreases
        for i in range(max_idx - 1, -1, -1):
            if db_data[i] <= target and db_data[i + 1] >= target:
                frac = (target - db_data[i + 1]) / (db_data[i] - db_data[i + 1]) if abs(db_data[i] - db_data[i + 1]) > 1e-12 else 0
                f_low = freqs[i + 1] + frac * (freqs[i] - freqs[i + 1])
                break
        # Search right of peak: gain decreases as index increases
        for i in range(max_idx, len(db_data) - 1):
            if db_data[i] >= target > db_data[i + 1]:
                frac = (target - db_data[i]) / (db_data[i + 1] - db_data[i]) if abs(db_data[i + 1] - db_data[i]) > 1e-12 else 0
                f_high = freqs[i] + frac * (freqs[i + 1] - freqs[i])
                break
        if f_low and f_high and f_high > f_low > 0:
            return round(freqs[max_idx] / (f_high - f_low), 2)

    # Low-pass / high-pass: estimate from peaking
    if filter_type in ("low-pass", "high-pass"):
        if peaking_db > 0.5:
            # Q > 0.707: estimate from peaking height
            # Standard 2nd-order LP: |H|max = Q / sqrt(1 - 1/(4Q²))
            # Approximate inverse: Q ≈ 10^(peaking_dB/20) * 0.6
            q_est = 10 ** (peaking_db / 20.0) * 0.6
            return round(min(q_est, 15.0), 2)
        else:
            # No peaking → Q ≤ 0.707 (Butterworth) or lower (Bessel)
            # Without further data, report ~0.5 (unity-gain Sallen-Key default)
            return 0.50

    return None


def _compute_ac_metrics(ac_data: dict) -> dict:
    """Compute key metrics from AC analysis data.

    Returns dict with: dc_gain, cutoff_freq, phase_at_cutoff, max_gain,
                       roll_off_db_dec, filter_type, warnings
    """
    metrics = {"warnings": []}

    freqs = ac_data.get("frequencies", [])
    if not freqs:
        return metrics

    # Find the dB column with the most attenuation (skip flat 0dB source outputs)
    db_cols = [c for c in ac_data.get("data", {}) if c.startswith("vdb(")]
    phase_cols = [c for c in ac_data.get("data", {}) if c.startswith("vp(")]
    metrics["phase_cols"] = phase_cols  # store for downstream phase margin calc

    if not db_cols:
        metrics["warnings"].append("No dB data found")
        return metrics

    # Pick the best dB column: prefer one with meaningful signal (not source/dc nodes)
    # Skip columns at -200dB (DC-only nodes, effectively 0V) and flat 0dB sources
    best_db_col = db_cols[0]
    best_score = -999
    for col in db_cols:
        vals = ac_data["data"].get(col, [])
        if not vals:
            continue
        vmin, vmax = min(vals), max(vals)
        # Skip DC-only nodes (stuck at -200dB) and pure source nodes (flat 0dB)
        if vmin < -190 or (abs(vmax - vmin) < 0.001 and abs(vmax) < 0.001):
            continue
        # Prefer the column with the most interesting signal:
        # - If frequency-dependent (filter): pick the one with most dynamic range
        # - If flat (amplifier): pick the one closest to 0dB (highest gain, likely output)
        dynamic_range = abs(vmax - vmin)
        if dynamic_range > 0.5:
            score = dynamic_range  # clear frequency shaping
        else:
            # Flat response: prefer highest gain (least negative dB)
            score = max(vmax, -190)  # clamp -200 sentinel
        if score > best_score:
            best_score = score
            best_db_col = col

    db_data = ac_data["data"][best_db_col]
    # Match phase column to the chosen dB column
    node_num = best_db_col.replace("vdb(", "").rstrip(")")
    matching_phase = f"vp({node_num})"
    phase_data = ac_data["data"].get(matching_phase, []) if phase_cols else []
    # Store all phase columns as dict for downstream phase margin computation (line ~520)
    metrics["phase_data"] = {col: ac_data["data"].get(col, []) for col in phase_cols}

    if not db_data:
        return metrics

    # DC gain: gain at lowest frequency
    metrics["dc_gain_db"] = db_data[0]
    metrics["dc_gain_freq"] = freqs[0]

    # Max gain
    max_idx = max(range(len(db_data)), key=lambda i: db_data[i])
    metrics["max_gain_db"] = db_data[max_idx]
    metrics["max_gain_freq"] = freqs[max_idx]

    # -3dB cutoff: find where gain drops 3dB below the reference (use max gain as ref)
    ref_gain = db_data[max_idx]  # use max gain as reference
    cutoff_idx = None
    for i, db in enumerate(db_data):
        if db < ref_gain - 3.0:
            cutoff_idx = i
            break

    if cutoff_idx is not None:
        if cutoff_idx > 0:
            # Linear interpolation
            f1, f2 = freqs[cutoff_idx - 1], freqs[cutoff_idx]
            g1, g2 = db_data[cutoff_idx - 1], db_data[cutoff_idx]
            target = ref_gain - 3.0
            if abs(g2 - g1) > 1e-12:
                cutoff_freq = f1 + (f2 - f1) * (target - g1) / (g2 - g1)
            else:
                cutoff_freq = freqs[cutoff_idx]
            metrics["cutoff_freq"] = cutoff_freq

            # Phase at cutoff
            if phase_data and cutoff_idx < len(phase_data):
                if cutoff_idx > 0:
                    p1 = phase_data[cutoff_idx - 1]
                    p2 = phase_data[cutoff_idx]
                    frac = (target - g1) / (g2 - g1) if abs(g2 - g1) > 1e-12 else 0
                    metrics["phase_at_cutoff"] = p1 + frac * (p2 - p1)
                else:
                    metrics["phase_at_cutoff"] = phase_data[cutoff_idx]
        else:
            metrics["cutoff_freq"] = freqs[cutoff_idx]
    else:
        metrics["warnings"].append("Cutoff frequency not found in measured range")

    # Roll-off: slope after cutoff
    if cutoff_idx is not None and cutoff_idx + 1 < len(freqs):
        # Find a decade after cutoff
        decade_freq = metrics.get("cutoff_freq", freqs[cutoff_idx]) * 10
        dec_idx = None
        for i in range(cutoff_idx, len(freqs)):
            if freqs[i] >= decade_freq:
                dec_idx = i
                break
        if dec_idx is not None and dec_idx > cutoff_idx:
            db_decade = db_data[dec_idx] - db_data[cutoff_idx]
            metrics["roll_off_db_per_decade"] = db_decade
        elif cutoff_idx + 5 < len(freqs):
            # Approximate from available data
            db_change = db_data[cutoff_idx + min(5, len(db_data) - cutoff_idx - 1)] - db_data[cutoff_idx]
            freq_ratio = freqs[cutoff_idx + min(5, len(freqs) - cutoff_idx - 1)] / freqs[cutoff_idx]
            metrics["roll_off_db_per_decade"] = db_change / math.log10(freq_ratio)

    # Filter type identification
    # Check band-pass BEFORE high-pass: band-pass filters have low DC gain
    # but a mid-frequency peak, which would be misidentified as high-pass
    # if the high-pass check (dc_gain < -20) runs first.
    if metrics.get("dc_gain_db", -999) > -1 and metrics.get("cutoff_freq"):
        metrics["filter_type"] = "low-pass"
    elif metrics.get("max_gain_db", -999) > metrics.get("dc_gain_db", -999) + 3:
        metrics["filter_type"] = "band-pass"
    elif metrics.get("dc_gain_db", 0) < -20 and metrics.get("cutoff_freq"):
        metrics["filter_type"] = "high-pass"
    else:
        metrics["filter_type"] = "unknown"

    # Q factor (quality factor) for 2nd-order filters
    q_val = _compute_filter_q(db_data, freqs, ref_gain,
                               metrics.get("filter_type", "unknown"))
    if q_val is not None:
        metrics["q_factor"] = q_val

    # Health checks
    if metrics.get("dc_gain_db", -999) < -60:
        metrics["warnings"].append("Very low DC gain — check circuit connections")

    if metrics.get("filter_type") == "low-pass" and metrics.get("cutoff_freq", 1e9) > 1e6:
        metrics["warnings"].append("Cutoff frequency very high (>1MHz) — check component values")

    # Q-specific health checks
    if q_val is not None and q_val > 3.0:
        metrics["warnings"].append(f"Q factor very high ({q_val:.1f}) — strong peaking, may oscillate")
    elif q_val is not None and q_val < 0.3:
        metrics["warnings"].append(f"Q factor very low ({q_val:.2f}) — filter is overdamped, poor selectivity")

    # ── P0: Gain-Bandwidth Product (GBW) for amplifiers ──
    dc_gain = metrics.get("dc_gain_db", -999)
    cutoff = metrics.get("cutoff_freq")
    if dc_gain > -190 and cutoff is not None:
        dc_gain_lin = 10 ** (dc_gain / 20.0)
        gbw = dc_gain_lin * cutoff
        metrics["gbw"] = gbw
        # Health check: amplifier with very low GBW
        if metrics.get("filter_type") == "unknown" and gbw < 100:
            metrics["warnings"].append(f"GBW very low ({gbw:.1f} Hz) — amplifier bandwidth may be insufficient")

    # ── P1: Phase margin (at unity-gain crossover, not -3dB cutoff) ──
    # Phase margin = 180° + phase(f_unity_gain) where gain(f_unity_gain) ≈ 0dB
    phase_cols = [c for c in metrics.get("phase_cols", [])]
    if phase_cols and freqs and db_data:
        phase_data = metrics.get("phase_data", {})
        best_col = phase_cols[0]
        best_phase = phase_data.get(best_col, [])
        if best_phase and len(best_phase) == len(freqs):
            # Find unity-gain crossover: gain crosses 0dB (from above to below)
            unity_idx = None
            for i in range(len(db_data) - 1):
                if db_data[i] >= 0 and db_data[i + 1] < 0:
                    # Linear interpolation for more accurate crossing
                    frac = (0 - db_data[i]) / (db_data[i + 1] - db_data[i]) if db_data[i + 1] != db_data[i] else 0
                    unity_phase = best_phase[i] + frac * (best_phase[i + 1] - best_phase[i])
                    phase_margin = 180.0 + unity_phase
                    # Normalize to [0, 180]
                    while phase_margin > 180:
                        phase_margin -= 360
                    while phase_margin < 0:
                        phase_margin += 360
                    metrics["phase_margin_deg"] = round(phase_margin, 1)
                    if phase_margin < 30:
                        metrics["warnings"].append(
                            f"Phase margin only {phase_margin:.0f}° — may ring or oscillate")
                    elif phase_margin < 45:
                        metrics["warnings"].append(
                            f"Phase margin {phase_margin:.0f}° (<45°) — marginal stability")
                    break
            # Fallback: if gain never reaches 0dB (e.g. passive filter), use -3dB point
            if "phase_margin_deg" not in metrics:
                phase_at_cut = metrics.get("phase_at_cutoff")
                if phase_at_cut is not None:
                    phase_margin = 180.0 + phase_at_cut
                    while phase_margin > 180:
                        phase_margin -= 360
                    while phase_margin < 0:
                        phase_margin += 360
                    metrics["phase_margin_deg"] = round(phase_margin, 1)

    return metrics


def _compute_tran_metrics(tran_data: dict) -> dict:
    """Compute key metrics from transient analysis data.

    Returns dict with: rise_time, settling_time, overshoot_pct,
                       final_value, max_value, min_value, warnings
    """
    metrics = {"warnings": []}

    signals = tran_data.get("signals", {})
    times = tran_data.get("time", [])

    if not signals or not times:
        return metrics

    # Analyze the first non-constant signal
    for sig_name, values in signals.items():
        if len(values) < 10:
            continue
        max_v = max(values)
        min_v = min(values)
        if max_v - min_v < 1e-9:
            continue  # constant signal

        metrics["signal"] = sig_name
        metrics["max_value"] = max_v
        metrics["min_value"] = min_v

        # Final value (average of last 10%)
        last_n = max(1, len(values) // 10)
        final_value = sum(values[-last_n:]) / last_n
        metrics["final_value"] = final_value

        # Rise time: 10% → 90%
        v10 = min_v + 0.1 * (max_v - min_v)
        v90 = min_v + 0.9 * (max_v - min_v)
        t10, t90 = None, None
        for i, v in enumerate(values):
            if t10 is None and v >= v10:
                # Linear interpolate
                if i > 0:
                    frac = (v10 - values[i - 1]) / (v - values[i - 1]) if abs(v - values[i - 1]) > 1e-12 else 0
                    t10 = times[i - 1] + frac * (times[i] - times[i - 1])
                else:
                    t10 = times[i]
            if t90 is None and v >= v90:
                if i > 0:
                    frac = (v90 - values[i - 1]) / (v - values[i - 1]) if abs(v - values[i - 1]) > 1e-12 else 0
                    t90 = times[i - 1] + frac * (times[i] - times[i - 1])
                else:
                    t90 = times[i]
            if t10 is not None and t90 is not None:
                break
        if t10 is not None and t90 is not None:
            metrics["rise_time"] = t90 - t10

        # Overshoot
        if abs(final_value) > 1e-9:
            overshoot = (max_v - final_value) / abs(final_value) * 100
            metrics["overshoot_pct"] = overshoot
            if overshoot > 50:
                metrics["warnings"].append(f"High overshoot ({overshoot:.1f}%) — consider damping")

        # Settling time: last time outside 5% band
        settle_band = 0.05 * abs(final_value)
        settling_time = 0
        for i in range(len(values) - 1, -1, -1):
            if abs(values[i] - final_value) > settle_band:
                settling_time = times[i] if i < len(times) else times[-1]
                break
        metrics["settling_time"] = settling_time
        if settling_time > 0.9 * times[-1]:
            metrics["warnings"].append("Signal has not fully settled")

        # Ripple: standard deviation in steady state
        steady_vals = values[-last_n:]
        if len(steady_vals) > 1:
            mean_v = sum(steady_vals) / len(steady_vals)
            ripple = math.sqrt(sum((v - mean_v) ** 2 for v in steady_vals) / len(steady_vals))
            metrics["ripple_rms"] = ripple
            if abs(final_value) > 1e-9 and ripple / abs(final_value) > 0.1:
                metrics["warnings"].append(f"High ripple ({ripple:.4f}V RMS)")

        # ── P3: Slew rate = max |dV/dt| during rising/falling edge ──
        if len(values) >= 3 and len(times) >= 3:
            max_slew = 0.0
            for i in range(1, len(values)):
                dt = times[i] - times[i - 1]
                if dt > 1e-15:
                    dv_dt = abs(values[i] - values[i - 1]) / dt
                    if dv_dt > max_slew:
                        max_slew = dv_dt
            metrics["slew_rate"] = max_slew
            if max_slew < 1e-3:
                metrics["warnings"].append(f"Slew rate very low ({max_slew:.2e} V/s)")

        break  # Only analyze first non-constant signal

    return metrics


# ═══════════════════════════════════════════════════════════════
# CSV 导出
# ═══════════════════════════════════════════════════════════════

def _export_csv(analysis: str, ac_data: dict, tran_data: dict, charts_dir: Path) -> str:
    """Export simulation data to CSV. Returns URL path."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    csv_path = charts_dir / f"sim_{ts}.csv"

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        if analysis == "ac" and ac_data.get("frequencies"):
            cols = ["Frequency(Hz)"] + list(ac_data.get("data", {}).keys())
            writer.writerow(cols)
            freqs = ac_data["frequencies"]
            for i in range(len(freqs)):
                row = [freqs[i]]
                for col_data in ac_data.get("data", {}).values():
                    row.append(col_data[i] if i < len(col_data) else "")
                writer.writerow(row)
        elif analysis == "tran" and tran_data.get("time"):
            cols = ["Time(s)"] + list(tran_data.get("signals", {}).keys())
            writer.writerow(cols)
            times = tran_data["time"]
            for i in range(len(times)):
                row = [times[i]]
                for sig_data in tran_data.get("signals", {}).values():
                    row.append(sig_data[i] if i < len(sig_data) else "")
                writer.writerow(row)

    return f"/charts/{csv_path.name}"


# ═══════════════════════════════════════════════════════════════
# 格式化输出
# ═══════════════════════════════════════════════════════════════

def _format_result(analysis: str, success: bool, error_msg: str,
                   op_parsed: dict, ac_parsed: dict, tran_parsed: dict,
                   ac_metrics: dict, tran_metrics: dict,
                   csv_url: str, output_preview: str) -> str:
    """Format simulation results into LLM-friendly markdown."""

    parts = []

    # Status header
    analysis_label = {"op": "DC Operating Point", "ac": "AC Frequency Sweep",
                      "tran": "Transient Analysis"}.get(analysis, analysis.upper())

    if success:
        num_points = (ac_parsed.get("num_points", 0) or tran_parsed.get("num_points", 0)
                      or len(op_parsed.get("nodes", {})))
        parts.append(f"## ✅ Simulation Complete — {analysis_label}")
        if analysis == "ac" and ac_parsed.get("freq_range"):
            f_min, f_max = ac_parsed["freq_range"]
            parts.append(f"**Data points:** {num_points} ({f_min:.1f} Hz → {f_max:.1f} Hz)")
        elif analysis == "tran" and tran_parsed.get("time"):
            t_min, t_max = tran_parsed["time"][0], tran_parsed["time"][-1]
            parts.append(f"**Data points:** {num_points} ({t_min:.2e}s → {t_max:.2e}s)")
        parts.append("")
    else:
        parts.append(f"## ❌ Simulation Failed — {analysis_label}")
        parts.append(f"**Error:** {error_msg}")
        parts.append("")
        parts.append("### ngspice Output")
        parts.append(f"```\n{output_preview[:1500]}\n```")
        parts.append("")
        parts.append("---")
        parts.append("🔧 **Recovery:** Fix the SPICE errors above, then call "
                     "`simulate_spice` again with the corrected netlist. "
                     "After the simulation passes, use `draw_analog_spice` "
                     "to re-render the updated circuit diagram.")
        return "\n".join(parts)

    # DC Operating Point results
    if analysis == "op" and op_parsed.get("nodes"):
        parts.append("### DC Operating Point")
        parts.append("")
        parts.append("| Node | Voltage |")
        parts.append("|------|---------|")
        for node, v in sorted(op_parsed["nodes"].items()):
            parts.append(f"| {node} | {v:.4f} V |")
        if op_parsed.get("sources"):
            parts.append("")
            parts.append("| Source | Current |")
            parts.append("|--------|---------|")
            for src, i in sorted(op_parsed["sources"].items()):
                parts.append(f"| {src} | {i * 1000:.4f} mA |")
        if op_parsed.get("total_power", 0) > 0:
            parts.append(f"\n**Total Power:** {op_parsed['total_power'] * 1000:.3f} mW")
        parts.append("")

    # AC metrics
    if analysis == "ac":
        m = ac_metrics
        parts.append("### Key Metrics")
        parts.append("")
        parts.append("| Metric | Value |")
        parts.append("|--------|-------|")
        if m.get("dc_gain_db") is not None:
            parts.append(f"| DC Gain | {m['dc_gain_db']:.4f} dB @ {m['dc_gain_freq']:.1f} Hz |")
        if m.get("cutoff_freq") is not None:
            parts.append(f"| -3 dB Cutoff | {m['cutoff_freq']:.1f} Hz |")
        if m.get("phase_at_cutoff") is not None:
            parts.append(f"| Phase at Cutoff | {m['phase_at_cutoff']:.1f}° |")
        if m.get("max_gain_db") is not None:
            parts.append(f"| Max Gain | {m['max_gain_db']:.4f} dB @ {m['max_gain_freq']:.1f} Hz |")
        if m.get("roll_off_db_per_decade") is not None:
            parts.append(f"| Roll-off | {m['roll_off_db_per_decade']:.1f} dB/decade |")
        if m.get("q_factor") is not None:
            parts.append(f"| Q Factor | **{m['q_factor']:.2f}** |")
        if m.get("gbw") is not None:
            gbw = m["gbw"]
            if gbw >= 1e6:
                parts.append(f"| GBW (Gain×BW) | **{gbw/1e6:.2f} MHz** |")
            elif gbw >= 1e3:
                parts.append(f"| GBW (Gain×BW) | **{gbw/1e3:.2f} kHz** |")
            else:
                parts.append(f"| GBW (Gain×BW) | **{gbw:.1f} Hz** |")
        if m.get("phase_margin_deg") is not None:
            pm = m["phase_margin_deg"]
            parts.append(f"| Phase Margin | **{pm:.0f}°** |")
        if m.get("filter_type"):
            parts.append(f"| Filter Type | **{m['filter_type']}** |")
        parts.append("")

        # Data preview
        if ac_parsed.get("frequencies"):
            parts.append("### Data Preview")
            parts.append("")
            freqs = ac_parsed["frequencies"]
            data = ac_parsed.get("data", {})
            cols = list(data.keys())
            if cols:
                # Header
                header = "| Freq (Hz) |"
                sep = "|-----------|"
                for c in cols[:3]:
                    label = re.sub(r'v[md]b\((.+)\)', r'Gain dB (\1)', c)
                    label = re.sub(r'vp\((.+)\)', r'Phase° (\1)', label)
                    header += f" {label} |"
                    sep += "-----------|"
                parts.append(header)
                parts.append(sep)
                # Rows (show 15 points logarithmic spread)
                n = len(freqs)
                indices = list(range(0, n, max(1, n // 15)))[:15]
                if n - 1 not in indices:
                    indices.append(n - 1)
                for i in indices:
                    row = f"| {freqs[i]:.2f} |"
                    for c in cols[:3]:
                        if i < len(data[c]):
                            row += f" {data[c][i]:.4f} |"
                    parts.append(row)
                parts.append("")
                parts.append(f"*Showing {len(indices)} of {n} data points*")
                parts.append("")

    # Transient metrics
    if analysis == "tran":
        m = tran_metrics
        parts.append("### Key Metrics")
        parts.append("")
        parts.append("| Metric | Value |")
        parts.append("|--------|-------|")
        if m.get("signal"):
            parts.append(f"| Analyzed Signal | {m['signal']} |")
        if m.get("final_value") is not None:
            parts.append(f"| Final Value | {m['final_value']:.4f} V |")
        if m.get("rise_time") is not None:
            parts.append(f"| Rise Time (10%→90%) | {m['rise_time'] * 1e6:.2f} µs |")
        if m.get("overshoot_pct") is not None:
            parts.append(f"| Overshoot | {m['overshoot_pct']:.1f}% |")
        if m.get("settling_time") is not None:
            parts.append(f"| Settling Time (±5%) | {m['settling_time'] * 1e6:.2f} µs |")
        if m.get("ripple_rms") is not None:
            parts.append(f"| Ripple (RMS) | {m['ripple_rms'] * 1000:.3f} mV |")
        if m.get("slew_rate") is not None:
            sr = m["slew_rate"]
            if sr >= 1e6:
                parts.append(f"| Slew Rate | **{sr/1e6:.1f} V/µs** |")
            else:
                parts.append(f"| Slew Rate | **{sr/1e3:.3f} V/ms** |")
        parts.append("")

        # Data preview
        if tran_parsed.get("time"):
            parts.append("### Data Preview")
            parts.append("")
            times = tran_parsed["time"]
            signals = tran_parsed.get("signals", {})
            sig_names = list(signals.keys())
            if sig_names:
                header = "| Time (s) |"
                sep = "|-----------|"
                for s in sig_names[:3]:
                    header += f" {s} |"
                    sep += "-----------|"
                parts.append(header)
                parts.append(sep)
                n = len(times)
                indices = list(range(0, n, max(1, n // 15)))[:15]
                if n - 1 not in indices:
                    indices.append(n - 1)
                for i in indices:
                    row = f"| {times[i]:.4e} |"
                    for s in sig_names[:3]:
                        if i < len(signals[s]):
                            row += f" {signals[s][i]:.4f} |"
                    parts.append(row)
                parts.append("")
                parts.append(f"*Showing {len(indices)} of {n} data points*")
                parts.append("")

    # Health check
    all_warnings = (op_parsed.get("warnings", []) + ac_metrics.get("warnings", [])
                    + tran_metrics.get("warnings", []))
    if all_warnings:
        parts.append("### ⚠️ Health Check")
        for w in all_warnings:
            parts.append(f"- ⚠️ {w}")
    else:
        parts.append("### ✅ Health Check")
        parts.append("All checks passed — no issues detected.")
    parts.append("")

    # CSV download
    if csv_url:
        parts.append(f"📥 **Raw data:** [{csv_url}]({csv_url})")
        parts.append("")

    # LLM optimization hint
    parts.append("---")
    parts.append("🔄 **Iterative optimization:** If the metrics don't meet your specs:\n"
                 "1. Modify the SPICE netlist (adjust component values)\n"
                 "2. Call `draw_analog_spice` to re-render the circuit\n"
                 "3. Call `simulate_spice` again to verify\n"
                 "4. Repeat until performance targets are met")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# SpiceSimulator 类
# ═══════════════════════════════════════════════════════════════

class SpiceSimulator:
    TOOLS = [
        ("simulate_spice",
         "Run ngspice simulation on a SPICE netlist and return analysis results. "
         "Extracts key metrics (cutoff frequency, gain, rise time, etc.) that "
         "the LLM can reason about for circuit optimization.\n"
         "\n"
         "**Analysis types:**\n"
         "- `.op` — DC operating point (node voltages, currents, power)\n"
         "- `.ac` — AC frequency sweep (gain vs freq, -3dB cutoff, phase, roll-off)\n"
         "- `.tran` — Transient analysis (rise time, overshoot, settling time, ripple)\n"
         "\n"
         "**Auto-features:**\n"
         "- Auto-detects analysis type from netlist (or specify explicitly)\n"
         "- Auto-injects `.print` commands and opamp/diode models\n"
         "- Returns structured metrics + data table + CSV download link\n"
         "\n"
         "**Circuit optimization workflow:**\n"
         "1. `draw_analog_spice` — visualize the circuit\n"
         "2. `simulate_spice` — get performance metrics\n"
         "3. Analyze results → modify SPICE → simulate again → iterate",
         "simulate_spice",
         {"spice": {"type": "string",
                     "description":
                     "SPICE netlist to simulate. Include .ac/.tran/.op analysis command, "
                     "or specify analysis type explicitly."},
          "analysis": {"type": "string",
                        "description": "Analysis type: 'op', 'ac', or 'tran'. "
                        "Auto-detected from netlist if not specified."}},
         ["spice"]),
    ]

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def simulate_spice(self, spice: str, analysis: str = "") -> str:
        """Run ngspice simulation and return structured analysis results."""
        if not _check_ngspice():
            return ("❌ **ngspice is not installed.**\n"
                    "Install: `brew install ngspice` (macOS) or "
                    "`apt install ngspice` (Linux)\n\n"
                    "You can still use `draw_analog_spice` to draw the circuit.")

        # 1. Prepare netlist
        try:
            prepared, detected_analysis = _prep_netlist(spice, analysis)
        except Exception as e:
            return f"❌ **Error preparing netlist:** {e}"

        # 2. Write temp file and run ngspice (unique name avoids concurrency races)
        import tempfile as _tempfile
        with _tempfile.NamedTemporaryFile(
            mode="w", suffix=".cir", delete=False, dir=self.charts_dir
        ) as _tf:
            _tf.write(prepared)
            cir_path = Path(_tf.name)

        try:
            result = subprocess.run(
                ["ngspice", "-b", str(cir_path)],
                capture_output=True, text=True, timeout=30,
                cwd=str(self.charts_dir),
            )
        except subprocess.TimeoutExpired:
            self._cleanup(cir_path)
            return "❌ **Simulation timed out (>30s).** The circuit may be too complex or oscillating."
        except FileNotFoundError:
            self._cleanup(cir_path)
            return "❌ **ngspice not found.** Install: `brew install ngspice`"

        # Strip form feeds (safety; set nopage should prevent them)
        output = (result.stderr + result.stdout).replace("\f", "\n")
        success = result.returncode == 0

        # 3. Check for fatal errors
        fatal_pattern = re.search(
            r'(Error on line|FATAL|parse error|unknown component|too few nodes)',
            output, re.IGNORECASE)
        if fatal_pattern:
            error_lines = []
            for line in output.split("\n"):
                upper = line.upper()
                if ("ERROR" in upper or "FATAL" in upper or "parse error" in line.lower()
                        or "unknown" in line.lower()):
                    if "no errors" not in line.lower():
                        error_lines.append(line.strip()[:200])
            if error_lines:
                error_msg = error_lines[0]
                self._cleanup(cir_path)
                return _format_result(detected_analysis, False, error_msg,
                                    {}, {}, {}, {}, {}, "", output[:1000])

        # 4. Parse results from stdout
        op_parsed = {}
        ac_parsed = {}
        tran_parsed = {}
        ac_metrics = {}
        tran_metrics = {}

        if detected_analysis == "op":
            op_parsed = _parse_op_output(output)
        elif detected_analysis == "ac":
            ac_parsed = _parse_ac_output(output)
            if not ac_parsed.get("error"):
                ac_metrics = _compute_ac_metrics(ac_parsed)
        elif detected_analysis == "tran":
            tran_parsed = _parse_tran_output(output)
            if not tran_parsed.get("error"):
                tran_metrics = _compute_tran_metrics(tran_parsed)

        # 5. Export CSV
        csv_url = _export_csv(detected_analysis, ac_parsed, tran_parsed, self.charts_dir)

        # 6. Cleanup
        self._cleanup(cir_path)

        # 7. Format output
        return _format_result(
            detected_analysis, True, "",
            op_parsed, ac_parsed, tran_parsed, ac_metrics, tran_metrics,
            csv_url, output)

    def _cleanup(self, cir_path: Path):
        """Cleanup temp simulation files. ngspice creates output files named
        after the input .cir file (e.g. tmpXXXXX.raw, tmpXXXXX.log)."""
        # Always unlink the input .cir file
        if cir_path.exists():
            try:
                cir_path.unlink(missing_ok=True)
            except Exception:
                pass
        # Unlink ngspice output files derived from the same stem
        stem = cir_path.stem
        parent = cir_path.parent
        for ext in (".raw", ".log", ".out"):
            p = parent / (stem + ext)
            if p.exists():
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
