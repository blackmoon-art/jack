"""Analog circuit simulator — ngspice runner + output parser + waveform chart.

No TOOLS (no tool registration) — used internally by analog_pipeline.py.
"""

from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.analog_sim")


# ═══════════ SimulationResult ═══════════

@dataclass
class SimulationResult:
    """Structured result of an ngspice simulation run."""
    sim_type: str                # "ac", "tran", "dc", "op"
    success: bool
    raw_output: str = ""         # full ngspice stdout
    vectors: dict = field(default_factory=dict)
    # vectors keys: "frequency"|"time"|"v-sweep" + "V(out)", "V(out)_db", "V(out)_deg", ...
    error: str = ""
    metrics: dict = field(default_factory=dict)


# ═══════════ AnalogSimulator ═══════════

class AnalogSimulator:
    """Runs ngspice, parses output, computes metrics, generates waveform charts."""

    # Dark theme colors (matches analog_svg.py)
    COLORS = {
        "bg": "#1a1a2e", "fg": "#e0e0e0", "stroke": "#7c3aed",
        "grid": "#333", "line1": "#7c3aed", "line2": "#f59e0b",
    }

    def __init__(self, work_dir: str = "", charts_dir: str = "",
                 ngspice_path: str = "ngspice", timeout: int = 30):
        self.ngspice_path = ngspice_path
        self.timeout = timeout
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = Path(tempfile.gettempdir()) / "nano_agent_charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        if work_dir:
            self.work_dir = Path(work_dir)
        else:
            self.work_dir = self.charts_dir.parent

    # ── ngspice check ──

    def check_ngspice(self) -> tuple[bool, str]:
        """Check if ngspice is available. Returns (available, version_string)."""
        try:
            result = subprocess.run(
                [self.ngspice_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            version = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return True, version
        except FileNotFoundError:
            return False, f"ngspice not found at '{self.ngspice_path}'. Install: apt install ngspice / brew install ngspice"
        except Exception as e:
            return False, f"ngspice check failed: {e}"

    # ── Simulation execution ──

    def run_ngspice(self, netlist: str) -> tuple[str, bool, str]:
        """Run ngspice batch mode on a netlist. Returns (raw_output, success, error)."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        cir_file = self.charts_dir / f"sim_{ts}.cir"
        try:
            cir_file.write_text(netlist, encoding="utf-8")
        except OSError as e:
            return "", False, f"Cannot write netlist file: {e}"

        try:
            result = subprocess.run(
                [self.ngspice_path, "-b", str(cir_file)],
                capture_output=True, text=True,
                timeout=self.timeout,
                cwd=str(self.charts_dir),
            )
            raw = result.stdout
            if result.returncode != 0:
                # Collect error lines
                err_lines = result.stderr.split("\n") if result.stderr else []
                err_text = "\n".join(err_lines[-20:])
                return raw, False, f"ngspice exit {result.returncode}: {err_text}"
            return raw, True, ""
        except subprocess.TimeoutExpired:
            return "", False, f"Simulation timed out after {self.timeout}s"
        except Exception as e:
            return "", False, f"Simulation error: {e}"
        finally:
            # Clean up temp files (keep charts)
            for pat in ["sim_output.raw", "sim_output.raw", cir_file.name + "~"]:
                f = self.charts_dir / pat
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass

    # ── Output parsing ──

    def parse_raw_output(self, raw_output: str, sim_type: str) -> SimulationResult:
        """Parse ngspice raw output into structured vectors."""
        result = SimulationResult(sim_type=sim_type, success=False, raw_output=raw_output)

        # Try parsing raw file first
        raw_file = self.charts_dir / "sim_output.raw"
        if raw_file.exists():
            try:
                vectors = self._parse_raw_ascii(raw_file, sim_type)
                if vectors:
                    result.vectors = vectors
                    result.success = True
                    return result
            except Exception as e:
                logger.debug(f"Raw file parse failed: {e}")

        # Fallback: parse printed table from stdout
        try:
            vectors = self._parse_printed_output(raw_output, sim_type)
            if vectors:
                result.vectors = vectors
                result.success = True
                return result
        except Exception as e:
            logger.debug(f"Printed output parse failed: {e}")

        # Error detection
        error_kw = ["error", "fatal", "singular", "no dc path", "floating",
                     "convergence", "timestep too small", "unknown parameter"]
        err_lines = []
        for line in raw_output.split("\n"):
            line_lower = line.lower()
            if any(kw in line_lower for kw in error_kw):
                err_lines.append(line.strip())
        result.error = "\n".join(err_lines[:10])
        return result

    def _parse_raw_ascii(self, raw_file: Path, sim_type: str) -> dict | None:
        """Parse ngspice raw file in ASCII (nutmeg) format."""
        text = raw_file.read_text(encoding="utf-8", errors="replace")
        # Find "Variables:" section
        if "Variables:" not in text:
            return None
        # Parse variable count and names
        var_names = []
        var_section = text.split("Variables:")[1].split("Values:")[0]
        for line in var_section.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # Format: <index> <name> <type>
            # e.g. "0 frequency frequency"
            # e.g. "1 v(2) voltage"
            m = re.match(r'\d+\s+(\S+)', line)
            if m:
                var_names.append(m.group(1))

        if not var_names:
            return None

        # Parse "Values:" section
        if "Values:" not in text:
            return None
        data_text = text.split("Values:")[1]
        vectors: dict[str, list[float]] = {name: [] for name in var_names}

        for line in data_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("Binary:"):
                break
            # Skip the index line
            if re.match(r'^\d+\s', line):
                parts = line.split()
                # first part is index, rest are values
                vals = parts[1:]
                for i, val_str in enumerate(vals):
                    if i < len(var_names):
                        try:
                            vectors[var_names[i]].append(float(val_str))
                        except ValueError:
                            vectors[var_names[i]].append(0.0)

        # For AC: extract independent var as "frequency"
        if sim_type == "ac" and var_names:
            for name in var_names:
                if name.lower() in ("frequency", "freq"):
                    vectors["frequency"] = vectors.get(name, [])
                    break
        elif sim_type == "tran" and var_names:
            for name in var_names:
                if name.lower() in ("time", "t"):
                    vectors["time"] = vectors.get(name, [])
                    break

        # Check if we got useful data
        has_data = any(len(v) > 0 for v in vectors.values())
        return vectors if has_data else None

    def _parse_printed_output(self, raw_output: str, sim_type: str) -> dict | None:
        """Parse ngspice .print table output from stdout.

        ngspice prints tabular data like:
          Index   frequency   v(2)
          -----------------------------------
          0       1.000e+00   9.999e-01
          1       1.258e+00   9.996e-01
        """
        lines = raw_output.split("\n")
        vectors: dict[str, list[float]] = {}
        header_found = False
        columns: list[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Skip separator lines
            if line.startswith("---") or line.startswith("==="):
                continue

            # Look for header: "Index   frequency   v(2)" etc.
            if not header_found and ("Index" in line or "index" in line):
                parts = line.split()
                # Normalize: lowercase, strip parens
                columns = [p.lower().replace("(", "").replace(")", "") for p in parts[1:]]
                for col in columns:
                    vectors[col] = []
                header_found = True
                continue

            if header_found and columns:
                parts = line.split()
                if len(parts) >= len(columns) + 1:
                    # First part is index, rest are values
                    vals = parts[1:len(columns) + 1]
                    for i, val_str in enumerate(vals):
                        if i < len(columns):
                            try:
                                vectors[columns[i]].append(float(val_str))
                            except ValueError:
                                pass

        # Rename for uniform access
        if sim_type == "ac":
            for col in columns:
                if col in ("frequency", "freq"):
                    vectors["frequency"] = vectors.pop(col, [])
                    break
        elif sim_type == "tran":
            for col in columns:
                if col in ("time", "t"):
                    vectors["time"] = vectors.pop(col, [])
                    break

        has_data = any(len(v) > 0 for v in vectors.values())
        return vectors if has_data else None

    # ── Metrics computation ──

    def compute_metrics(self, vectors: dict, sim_type: str) -> dict:
        """Compute key metrics from simulation vectors."""
        metrics: dict = {}
        try:
            if sim_type == "ac":
                self._compute_ac_metrics(vectors, metrics)
            elif sim_type == "tran":
                self._compute_tran_metrics(vectors, metrics)
            elif sim_type == "dc":
                self._compute_dc_metrics(vectors, metrics)
        except Exception as e:
            logger.warning(f"Metrics computation failed: {e}")
            metrics["error"] = str(e)
        return metrics

    def _compute_ac_metrics(self, vectors: dict, metrics: dict):
        """AC analysis metrics: DC gain, -3dB bandwidth, phase margin."""
        # Find output voltage magnitude
        out_keys = [k for k in vectors if k not in ("frequency", "freq")
                    and "_deg" not in k and "_db" not in k and len(vectors[k]) > 0]
        if not out_keys:
            return
        out_key = out_keys[0]
        out_mag = vectors[out_key]

        dc_gain = out_mag[0] if out_mag else 0
        dc_gain_db = 20 * math.log10(dc_gain) if dc_gain > 0 else -100
        metrics["dc_gain"] = round(dc_gain_db, 2)

        # -3dB bandwidth
        threshold = dc_gain / math.sqrt(2)
        bw_freq = None
        freq_key = vectors.get("frequency", [])
        for i, mag in enumerate(out_mag):
            if mag <= threshold:
                bw_freq = freq_key[i] if i < len(freq_key) else None
                break
        if bw_freq is not None:
            metrics["bandwidth_hz"] = round(bw_freq, 1)

        # Phase margin: find unity-gain frequency, compute phase margin
        unity_idx = None
        for i, mag in enumerate(out_mag):
            if mag <= 1.0:
                unity_idx = i
                break
        if unity_idx is not None and unity_idx > 0:
            phase_key = None
            for k in vectors:
                if "_deg" in k:
                    phase_key = k
                    break
            if phase_key and unity_idx < len(vectors[phase_key]):
                phase = vectors[phase_key][unity_idx]
                metrics["phase_margin_deg"] = round(180 + phase, 1)

    def _compute_tran_metrics(self, vectors: dict, metrics: dict):
        """Transient analysis metrics: overshoot, rise time, settling time."""
        out_keys = [k for k in vectors if k not in ("time", "t") and len(vectors[k]) > 0]
        if not out_keys:
            return
        out_key = out_keys[0]
        out = vectors[out_key]
        time_key = vectors.get("time", [])

        if len(out) < 2 or len(time_key) < 2:
            return

        # Steady-state = last 20% of samples
        n = len(out)
        steady = sum(out[int(n * 0.8):]) / max(1, n - int(n * 0.8))
        if steady != 0:
            peak = max(out)
            overshoot = (peak - steady) / abs(steady) * 100
            metrics["overshoot_pct"] = round(max(0, overshoot), 1)

        # Rise time: 10% → 90%
        t10, t90 = None, None
        steady_val = steady if steady > 0 else max(out)
        for i, val in enumerate(out):
            if t10 is None and val >= steady_val * 0.1:
                t10 = time_key[i] if i < len(time_key) else i
            if t90 is None and val >= steady_val * 0.9:
                t90 = time_key[i] if i < len(time_key) else i
                break
        if t10 is not None and t90 is not None:
            metrics["rise_time"] = round(float(t90) - float(t10), 6)

    def _compute_dc_metrics(self, vectors: dict, metrics: dict):
        """DC sweep metrics: transfer slope in linear region."""
        out_keys = [k for k in vectors if k not in ("v-sweep",) and len(vectors[k]) > 0]
        if not out_keys or len(out_keys[0]) < 3:
            return
        out = vectors[out_keys[0]]
        # Simple slope over middle 60% of range
        n = len(out)
        i_start = int(n * 0.2)
        i_end = int(n * 0.8)
        if i_end - i_start >= 2:
            d_out = out[i_end] - out[i_start]
            metrics["transfer_slope"] = round(d_out / (i_end - i_start), 4)

    # ── Waveform chart generation ──

    def generate_waveform_chart(self, vectors: dict, sim_type: str,
                                 title: str = "") -> str:
        """Generate a waveform chart PNG and return markdown image link.

        Returns empty string if chart generation fails.
        """
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            return ""

        try:
            if sim_type == "ac":
                return self._chart_bode(vectors, title)
            elif sim_type == "tran":
                return self._chart_time_domain(vectors, title)
            elif sim_type == "dc":
                return self._chart_dc(vectors, title)
        except Exception as e:
            logger.warning(f"Chart generation failed: {e}")
        return ""

    def _chart_bode(self, vectors: dict, title: str) -> str:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        freq = vectors.get("frequency", [])
        if not freq:
            return ""

        # Find magnitude and phase vectors
        out_key = None
        phase_key = None
        for k in vectors:
            if k not in ("frequency",) and "_deg" not in k and k not in ("time",):
                out_key = k
            if "_deg" in k:
                phase_key = k

        if out_key is None or out_key not in vectors:
            return ""
        mag = vectors[out_key]
        mag_db = [20 * math.log10(abs(v)) if v > 0 else -100 for v in mag]

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True,
                                        facecolor=self.COLORS["bg"])
        fig.patch.set_facecolor(self.COLORS["bg"])

        # Magnitude
        ax1.semilogx(freq, mag_db, color=self.COLORS["line1"], linewidth=1.5)
        ax1.set_ylabel("Magnitude (dB)", color=self.COLORS["fg"])
        ax1.set_title(title or "Frequency Response", color=self.COLORS["fg"])
        ax1.grid(True, alpha=0.3, color=self.COLORS["grid"])
        ax1.set_facecolor(self.COLORS["bg"])
        ax1.tick_params(colors=self.COLORS["fg"])

        # Phase
        if phase_key and phase_key in vectors:
            phase = vectors[phase_key]
            ax2.semilogx(freq, phase, color=self.COLORS["line2"], linewidth=1.5)
        ax2.set_ylabel("Phase (deg)", color=self.COLORS["fg"])
        ax2.set_xlabel("Frequency (Hz)", color=self.COLORS["fg"])
        ax2.grid(True, alpha=0.3, color=self.COLORS["grid"])
        ax2.set_facecolor(self.COLORS["bg"])
        ax2.tick_params(colors=self.COLORS["fg"])

        plt.tight_layout()
        return self._save_fig(fig, "bode")

    def _chart_time_domain(self, vectors: dict, title: str) -> str:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        t = vectors.get("time", [])
        if not t:
            return ""

        fig, ax = plt.subplots(figsize=(8, 4), facecolor=self.COLORS["bg"])
        fig.patch.set_facecolor(self.COLORS["bg"])

        colors = [self.COLORS["line1"], self.COLORS["line2"], "#10b981", "#ef4444"]
        ci = 0
        for k, v in vectors.items():
            if k not in ("time",) and len(v) == len(t):
                ax.plot(t, v, color=colors[ci % len(colors)],
                        linewidth=1.5, label=k)
                ci += 1

        ax.set_xlabel("Time (s)", color=self.COLORS["fg"])
        ax.set_ylabel("Voltage (V)", color=self.COLORS["fg"])
        ax.set_title(title or "Transient Response", color=self.COLORS["fg"])
        ax.grid(True, alpha=0.3, color=self.COLORS["grid"])
        ax.set_facecolor(self.COLORS["bg"])
        ax.tick_params(colors=self.COLORS["fg"])
        if ci > 1:
            ax.legend(loc="best")

        plt.tight_layout()
        return self._save_fig(fig, "tran")

    def _chart_dc(self, vectors: dict, title: str) -> str:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        out_keys = [k for k in vectors if k not in ("v-sweep", "frequency", "time")
                    and len(vectors[k]) > 0]
        sweep_keys = [k for k in vectors if k in ("v-sweep",) and len(vectors[k]) > 0]
        if not out_keys:
            return ""

        fig, ax = plt.subplots(figsize=(8, 4), facecolor=self.COLORS["bg"])
        fig.patch.set_facecolor(self.COLORS["bg"])

        x = vectors[sweep_keys[0]] if sweep_keys else range(len(vectors[out_keys[0]]))
        for k in out_keys:
            v = vectors[k]
            ax.plot(list(x)[:len(v)], v, color=self.COLORS["line1"], linewidth=1.5)

        ax.set_xlabel("Sweep", color=self.COLORS["fg"])
        ax.set_ylabel("Output", color=self.COLORS["fg"])
        ax.set_title(title or "DC Sweep", color=self.COLORS["fg"])
        ax.grid(True, alpha=0.3, color=self.COLORS["grid"])
        ax.set_facecolor(self.COLORS["bg"])
        ax.tick_params(colors=self.COLORS["fg"])

        plt.tight_layout()
        return self._save_fig(fig, "dc")

    def _save_fig(self, fig, prefix: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fp = self.charts_dir / f"{prefix}_{ts}.png"
        fig.savefig(str(fp), dpi=100, bbox_inches="tight",
                    facecolor=self.COLORS["bg"])
        import matplotlib.pyplot as plt
        plt.close(fig)
        url = f"/charts/{fp.name}"
        return f"![{prefix.upper()} Chart]({url})"
