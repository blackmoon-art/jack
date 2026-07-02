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

logger = logging.getLogger("nano_agent.tools.spice_simulator")

# ═══════════════════════════════════════════════════════════════
# 共享模型定义
# ═══════════════════════════════════════════════════════════════

_OPAMP_SUBCKT = """
.subckt opamp in_p in_n out vcc vss
* Simple behavioral opamp: gain=100k, single-pole at 10Hz
G1 0 n1 in_p in_n 1
R1 n1 0 100k
C1 n1 0 1.59e-4
E1 out 0 n1 0 1
Rout out 0 75
.ends opamp
"""

_DIODE_MODEL = """
.model DEFAULT_D D (IS=1e-14 RS=1 N=1)
"""

_GROUND_NAMES = {"0", "gnd", "GND"}


def _check_ngspice() -> bool:
    """Check if ngspice is available on PATH."""
    return shutil.which("ngspice") is not None


# ═══════════════════════════════════════════════════════════════
# Netlist 准备
# ═══════════════════════════════════════════════════════════════

def _extract_nodes(spice_text: str) -> list[str]:
    """Extract unique non-ground node names from SPICE netlist."""
    nodes = set()
    for line in spice_text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith(("*", "#", ".")):
            continue
        tokens = line.split()
        if len(tokens) < 3:
            continue
        ctype = tokens[0][0].upper()
        if ctype == "R":
            nodes.update(tokens[1:3])
        elif ctype in ("C", "L", "D"):
            nodes.update(tokens[1:3])
        elif ctype == "V":
            nodes.update(tokens[1:3])
        elif ctype == "X":
            nodes.update(tokens[1:-1])
    return sorted(n for n in nodes if n not in _GROUND_NAMES)


def _prep_netlist(spice_text: str, analysis: str = "") -> tuple[str, str]:
    """Prepare SPICE netlist for ngspice batch mode.

    Returns (prepared_netlist, detected_analysis_type).
    """
    text = spice_text.strip()

    # SPICE convention: first line is always a title. If it looks like a component
    # (starts with R/C/L/D/V/X), prepend a title line to prevent it being swallowed.
    if text and text.split("\n")[0].strip()[0].upper() in "RCLDVX":
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
    has_opamp = bool(re.search(r'\bX\w+\b', text))
    has_diode = bool(re.search(r'\bD\w+\b', text))
    if has_opamp and ".subckt opamp" not in text.lower():
        text = _OPAMP_SUBCKT.strip() + "\n" + text
    if has_diode and ".model" not in text.lower():
        text = _DIODE_MODEL.strip() + "\n" + text

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
        node_exprs = " ".join(f"vdb({n}) vp({n})" for n in nodes[:5])
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
                if not re.match(r'[\d\.\+\-e\s]+', stripped):
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


def _parse_ac_output(output: str) -> dict:
    """Parse ngspice AC analysis output.

    Handles form-feed-paginated output where wide tables are split across
    multiple pages. Strips \\f, skips repeated header/index lines, and
    collects all data rows.

    Returns {"frequencies": [...], "data": {col_name: [...]},
             "num_points": N, "freq_range": (min, max)}
    """
    result = {"frequencies": [], "data": {}, "num_points": 0, "freq_range": (0, 0)}

    # Strip form feeds and normalize whitespace
    cleaned = output.replace("\f", "\n")

    lines = cleaned.split("\n")
    headers = []
    data_cols_seen = {}  # col_name → list of (row_idx, value) for multi-page merge
    freq_values = {}     # row_idx → frequency
    current_cols = []    # columns in current page group
    in_table = False
    max_row_idx = -1

    for line in lines:
        stripped = line.strip()

        # Detect table header: "Index   frequency   vdb(N)   vp(N) ..."
        if "Index" in stripped and "frequency" in stripped.lower():
            in_table = True
            parts = stripped.split()
            current_cols = []
            for j in range(1, len(parts)):
                h = parts[j]
                if h == "frequency":
                    continue  # skip independent variable column
                if h.startswith("vdb(") or h.startswith("vp("):
                    current_cols.append(h)
            continue

        # Skip separator lines
        if stripped.startswith("---"):
            continue

        # Empty line after data rows → end of current page's table
        if not stripped:
            if in_table:
                in_table = False
            continue

        # Parse data row while in table
        if in_table:
            parts = stripped.split()
            if len(parts) < 2:
                continue
            try:
                row = [float(p) for p in parts]
                if len(row) < 2:
                    continue
            except ValueError:
                continue

            row_idx = int(row[0])
            freq = row[1]
            max_row_idx = max(max_row_idx, row_idx)
            freq_values[row_idx] = freq

            # Parse data columns: start at position 2
            for ci in range(2, len(row)):
                col_name = current_cols[ci - 2] if ci - 2 < len(current_cols) else f"col_{ci}"
                if col_name == "frequency":
                    continue
                if col_name not in data_cols_seen:
                    data_cols_seen[col_name] = {}
                data_cols_seen[col_name][row_idx] = row[ci]

    if max_row_idx < 0:
        result["error"] = "No AC data rows found"
        return result

    num_rows = max_row_idx + 1
    result["num_points"] = num_rows

    # Build ordered frequency list
    result["frequencies"] = [freq_values.get(i, 0.0) for i in range(num_rows)]
    if result["frequencies"]:
        result["freq_range"] = (result["frequencies"][0], result["frequencies"][-1])

    # Build ordered data columns
    for col_name, idx_vals in data_cols_seen.items():
        result["data"][col_name] = [idx_vals.get(i, 0.0) for i in range(num_rows)]

    return result


def _parse_tran_output(output: str) -> dict:
    """Parse ngspice transient analysis output.

    Handles form-feed-paginated output same as _parse_ac_output.

    Returns {"time": [...], "signals": {name: [...]}, "num_points": N}
    """
    result = {"time": [], "signals": {}, "num_points": 0}

    cleaned = output.replace("\f", "\n")
    lines = cleaned.split("\n")
    signal_data = {}     # sig_name → {row_idx: value}
    time_values = {}     # row_idx → time
    current_cols = []
    in_table = False
    max_row_idx = -1

    for line in lines:
        stripped = line.strip()

        if "Index" in stripped and "time" in stripped.lower():
            in_table = True
            parts = stripped.split()
            current_cols = []
            for j in range(1, len(parts)):
                h = parts[j]
                if h == "time":
                    continue  # skip independent variable column
                if h.startswith("v("):
                    current_cols.append(h)
            continue

        if stripped.startswith("---"):
            continue

        if not stripped:
            if in_table:
                in_table = False
            continue

        if in_table:
            parts = stripped.split()
            if len(parts) < 2:
                continue
            try:
                row = [float(p) for p in parts]
                if len(row) < 2:
                    continue
            except ValueError:
                continue

            row_idx = int(row[0])
            time_val = row[1]
            max_row_idx = max(max_row_idx, row_idx)
            time_values[row_idx] = time_val

            for ci in range(2, len(row)):
                name = current_cols[ci - 2] if ci - 2 < len(current_cols) else f"sig_{ci}"
                if name == "time":
                    continue
                if name not in signal_data:
                    signal_data[name] = {}
                signal_data[name][row_idx] = row[ci]

    if max_row_idx < 0:
        result["error"] = "No transient data rows found"
        return result

    num_rows = max_row_idx + 1
    result["num_points"] = num_rows
    result["time"] = [time_values.get(i, 0.0) for i in range(num_rows)]

    for name, idx_vals in signal_data.items():
        result["signals"][name] = [idx_vals.get(i, 0.0) for i in range(num_rows)]

    return result


# ═══════════════════════════════════════════════════════════════
# 指标计算
# ═══════════════════════════════════════════════════════════════

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

    if not db_cols:
        metrics["warnings"].append("No dB data found")
        return metrics

    # Pick the best dB column: prefer one that shows attenuation (not flat 0dB source)
    best_db_col = db_cols[0]
    for col in db_cols:
        vals = ac_data["data"].get(col, [])
        if vals and min(vals) < -0.01:
            best_db_col = col
            break

    db_data = ac_data["data"][best_db_col]
    # Match phase column to the chosen dB column
    node_num = best_db_col.replace("vdb(", "").rstrip(")")
    matching_phase = f"vp({node_num})"
    phase_data = ac_data["data"].get(matching_phase, []) if phase_cols else []

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
    ref_gain = db_data[0]  # use DC gain as reference
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
    if metrics.get("dc_gain_db", -999) > -1 and metrics.get("cutoff_freq"):
        metrics["filter_type"] = "low-pass"
    elif metrics.get("dc_gain_db", 0) < -20 and metrics.get("cutoff_freq"):
        metrics["filter_type"] = "high-pass"
    elif metrics.get("max_gain_db", -999) > metrics.get("dc_gain_db", -999) + 3:
        metrics["filter_type"] = "band-pass"
    else:
        metrics["filter_type"] = "unknown"

    # Health checks
    if metrics.get("dc_gain_db", -999) < -60:
        metrics["warnings"].append("Very low DC gain — check circuit connections")

    if metrics.get("filter_type") == "low-pass" and metrics.get("cutoff_freq", 1e9) > 1e6:
        metrics["warnings"].append("Cutoff frequency very high (>1MHz) — check component values")

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

        break  # Only analyze first non-constant signal

    return metrics


# ═══════════════════════════════════════════════════════════════
# CSV 导出
# ═══════════════════════════════════════════════════════════════

def _export_csv(analysis: str, ac_data: dict, tran_data: dict, charts_dir: Path) -> str:
    """Export simulation data to CSV. Returns URL path."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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
                    label = re.sub(r'vdb\((.+)\)', r'Gain dB (\1)', c)
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
    parts.append("💡 **Optimization loop ready:** Review the metrics above. "
                 "If performance doesn't meet specs, modify the SPICE netlist and "
                 "call `simulate_spice` again to verify improvements. "
                 "Use `draw_analog_spice` to visualize the circuit.")

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

        # 2. Write temp file and run ngspice
        cir_path = self.charts_dir / "_simulate.cir"
        cir_path.write_text(prepared)

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
        """Cleanup temp simulation files."""
        for pat in ("_simulate.cir", "_simulate.out", "_simulate.raw",
                     "_simulate.log"):
            p = cir_path.parent / pat if cir_path.name != pat else cir_path
            if p.exists():
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
