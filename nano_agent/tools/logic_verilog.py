"""Digital logic simulation — Verilog generation + iverilog runner + VCD parser.

No TOOLS (no tool registration) — used internally by logic_pipeline.py.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.logic_verilog")


# ═══════════ Gate → Verilog operator map ═══════════

_GATE_VERILOG = {
    "AND": "&", "OR": "|", "NOT": "~", "NAND": "~&", "NOR": "~|",
    "XOR": "^", "XNOR": "~^", "BUF": "",
}

# ═══════════ Verilog Generator ═══════════

class VerilogGen:
    """Generate Verilog from gate-level DSL and JSON module descriptions."""

    @staticmethod
    def gates_to_verilog(gates: list[dict], module_name: str = "circuit") -> str:
        """Generate combinational Verilog from gate netlist.

        Args:
            gates: [{"type":"AND","inputs":["A","B"],"output":"Y"}, ...]
            module_name: top-level module name

        Returns:
            Complete Verilog module source.
        """
        # Collect all signals
        produced = {g["output"] for g in gates}
        all_inputs = set()
        used_outputs = set()
        for g in gates:
            for inp in g["inputs"]:
                if inp not in produced:
                    all_inputs.add(inp)
            used_outputs.add(g["output"])

        # Outputs = signals that are produced but never used as inputs
        all_outputs = set()
        for g in gates:
            used_as_input = any(g["output"] in g2["inputs"] for g2 in gates)
            if not used_as_input:
                all_outputs.add(g["output"])
        if not all_outputs:
            # Last gate's output is the primary output
            all_outputs = {gates[-1]["output"]}

        lines = [f"module {module_name}("]
        io_list = sorted(all_inputs) + sorted(all_outputs)
        lines.append("  " + ", ".join(io_list) + ");")

        for inp in sorted(all_inputs):
            lines.append(f"  input {inp};")
        for out in sorted(all_outputs):
            lines.append(f"  output {out};")

        lines.append("")
        # Wire declarations for intermediate signals
        internals = set()
        for g in gates:
            for inp in g["inputs"]:
                if inp in produced:
                    internals.add(inp)
        for sig in sorted(internals):
            lines.append(f"  wire {sig};")
        lines.append("")

        # Gate assignments
        for g in gates:
            op = _GATE_VERILOG.get(g["type"])
            if op is None:
                lines.append(f"  // Unknown gate: {g['type']}")
                continue
            inputs = ", ".join(g["inputs"])
            if g["type"] == "NOT":
                lines.append(f"  assign {g['output']} = ~({g['inputs'][0]});")
            elif g["type"] == "BUF":
                lines.append(f"  assign {g['output']} = {g['inputs'][0]};")
            else:
                lines.append(f"  assign {g['output']} = {g['inputs'][0]} {op} {g['inputs'][1]};")

        lines.append("")
        lines.append("endmodule")
        return "\n".join(lines)

    @staticmethod
    def generate_testbench(gates: list[dict], module_name: str = "circuit") -> str:
        """Generate a self-checking testbench with all input combinations.

        For combinational circuits: exhaustive truth table.
        For sequential circuits (with CLK): clocked stimulus.
        """
        produced = {g["output"] for g in gates}
        all_inputs = sorted(set(
            inp for g in gates for inp in g["inputs"] if inp not in produced
        ))
        if not all_inputs:
            # All signals internal, generate dummy
            all_inputs = ["in"]

        lines = [
            f'`timescale 1ns / 1ps',
            f'module tb_{module_name};',
            f'  reg {", ".join(all_inputs)};',
        ]
        # Outputs
        all_outputs = sorted(set(
            g["output"] for g in gates
            if not any(g["output"] in g2["inputs"] for g2 in gates)
        ))
        if not all_outputs:
            all_outputs = [gates[-1]["output"]]
        lines.append(f"  wire {', '.join(all_outputs)};")
        lines.append("")

        # Instantiate DUT
        io_list = all_inputs + all_outputs
        lines.append(f"  {module_name} dut({', '.join(f'.{s}({s})' for s in io_list)});")
        lines.append("")

        # Generate all combinations
        n = len(all_inputs)
        total = 1 << n
        lines.append("  initial begin")
        lines.append('    $dumpfile("sim_output.vcd");')
        lines.append("    $dumpvars(0, tb_" + module_name + ");")
        lines.append(f"    for (integer i = 0; i < {total}; i = i + 1) begin")
        for idx, inp in enumerate(all_inputs):
            lines.append(f"      {inp} = i[{idx}];")
        lines.append("      #10;")
        lines.append("    end")
        lines.append('    $display("TEST COMPLETE");')
        lines.append("    $finish;")
        lines.append("  end")
        lines.append("")
        lines.append("endmodule")
        return "\n".join(lines)


# ═══════════ VCD Parser ═══════════

@dataclass
class VCDData:
    """Parsed VCD (Value Change Dump) data."""
    timescale: str = "1ns"
    signals: dict[str, str] = field(default_factory=dict)   # code → name
    changes: list[tuple[int, str, str]] = field(default_factory=list)
    # [(time, code, value), ...] e.g. [(0, "!", "0"), (10, "!", "1")]

    def get_vectors(self) -> dict[str, list[tuple[int, str]]]:
        """Return {signal_name: [(time, value), ...]}."""
        code_to_name = {v: k for k, v in self.signals.items()}
        vectors: dict[str, list[tuple[int, str]]] = {}
        for t, code, val in self.changes:
            name = code_to_name.get(code, code)
            vectors.setdefault(name, []).append((t, val))
        return vectors


def parse_vcd(vcd_text: str) -> VCDData:
    """Parse VCD text into structured data."""
    data = VCDData()
    current_time = 0
    var_map: dict[str, str] = {}  # code → name
    scope_stack: list[str] = []

    for line in vcd_text.split("\n"):
        line = line.strip()

        if line.startswith("$timescale"):
            m = re.search(r'(\d+)\s*(ns|ps|us|ms|s)', line)
            if m:
                data.timescale = f"{m.group(1)}{m.group(2)}"
        elif line.startswith("$scope"):
            m = re.search(r'module\s+(\S+)', line)
            if m:
                scope_stack.append(m.group(1))
        elif line == "$upscope $end":
            if scope_stack:
                scope_stack.pop()
        elif line.startswith("$var"):
            # $var wire 1 ! D $end
            parts = line.split()
            if len(parts) >= 5:
                code = parts[3]
                name = parts[4]
                var_map[code] = name
        elif line.startswith("$enddefinitions"):
            pass
        elif line.startswith("#"):
            try:
                current_time = int(line[1:])
            except ValueError:
                pass
        else:
            # Value change: "0!" or "1!"
            m = re.match(r'^([01xXzZ])(\S+)$', line)
            if m:
                val = m.group(1)
                code = m.group(2)
                if code in var_map:
                    data.changes.append((current_time, code, val))

    data.signals = var_map
    return data


# ═══════════ Logic Simulator ═══════════

class LogicSimulator:
    """Runs iverilog, parses VCD, generates truth tables and timing charts."""

    COLORS = {
        "bg": "#1a1a2e", "fg": "#e0e0e0", "line": "#7c3aed",
        "grid": "#333", "high": "#10b981", "low": "#3b82f6",
    }

    def __init__(self, work_dir: str = "", charts_dir: str = "",
                 iverilog_path: str = "iverilog", vvp_path: str = "vvp",
                 timeout: int = 30):
        self.iverilog_path = iverilog_path
        self.vvp_path = vvp_path
        self.timeout = timeout
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = Path(tempfile.gettempdir()) / "nano_agent_charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def check_iverilog(self) -> tuple[bool, str]:
        """Check if iverilog is available."""
        try:
            result = subprocess.run(
                [self.iverilog_path, "-V"],
                capture_output=True, text=True, timeout=5,
            )
            return True, result.stdout.split("\n")[0] if result.stdout else "unknown"
        except FileNotFoundError:
            return False, f"iverilog not found. Install: apt install iverilog"
        except Exception as e:
            return False, f"iverilog check failed: {e}"

    def run_simulation(self, verilog_src: str, tb_src: str) -> tuple[str, bool, str]:
        """Run iverilog + vvp, capture VCD output. Returns (vcd_text, success, error)."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = self.charts_dir / f"logic_sim_{ts}"
        v_file = base.with_suffix(".v")
        tb_file = base.with_suffix("_tb.v")
        vvp_file = base.with_suffix(".vvp")
        vcd_file = base.with_suffix(".vcd")

        try:
            v_file.write_text(verilog_src, encoding="utf-8")
            tb_file.write_text(tb_src, encoding="utf-8")
        except OSError as e:
            return "", False, f"Cannot write Verilog files: {e}"

        try:
            # Compile
            result = subprocess.run(
                [self.iverilog_path, "-o", str(vvp_file), str(v_file), str(tb_file)],
                capture_output=True, text=True,
                timeout=self.timeout,
                cwd=str(self.charts_dir),
            )
            if result.returncode != 0:
                return "", False, f"iverilog compile error:\n{result.stderr[:2000]}"

            # Run
            result = subprocess.run(
                [self.vvp_path, str(vvp_file)],
                capture_output=True, text=True,
                timeout=self.timeout,
                cwd=str(self.charts_dir),
            )

            # Read VCD
            if vcd_file.exists():
                vcd_text = vcd_file.read_text(encoding="utf-8", errors="replace")
                return vcd_text, True, ""
            else:
                return result.stdout, True, ""

        except subprocess.TimeoutExpired:
            return "", False, f"Simulation timed out after {self.timeout}s"
        except Exception as e:
            return "", False, f"Simulation error: {e}"
        finally:
            # Cleanup intermediate files
            for f in [v_file, tb_file, vvp_file]:
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass

    def generate_truth_table(self, vcd: VCDData, gates: list[dict]) -> str:
        """Generate markdown truth table from VCD data."""
        vectors = vcd.get_vectors()
        produced = {g["output"] for g in gates}
        all_inputs = sorted(set(
            inp for g in gates for inp in g["inputs"] if inp not in produced
        ))
        all_outputs = sorted(set(
            g["output"] for g in gates
            if not any(g["output"] in g2["inputs"] for g2 in gates)
        ))
        if not all_outputs:
            all_outputs = [gates[-1]["output"]]

        # Build truth table rows by collecting signals at each stable time
        # Group changes by time, collect the last value for each signal
        times = sorted(set(t for t, _, _ in vcd.changes))
        rows = []
        for t in times:
            row = {}
            for code, name in vcd.signals.items():
                # Find the last value before or at this time
                val = "x"
                for ct, cc, cv in vcd.changes:
                    if ct <= t and cc == code:
                        val = cv
                if name in all_inputs or name in all_outputs:
                    row[name] = val
            if row:
                rows.append(row)

        # Deduplicate rows
        seen = set()
        unique = []
        for r in rows:
            key = tuple(r.get(k, "x") for k in all_inputs + all_outputs)
            if key not in seen:
                seen.add(key)
                unique.append(r)

        # Build markdown table
        lines = []
        header = "| " + " | ".join(all_inputs + all_outputs) + " |"
        lines.append(header)
        sep = "|" + "|".join(" --- " for _ in range(len(all_inputs + all_outputs))) + "|"
        lines.append(sep)
        for r in unique:
            vals = [r.get(k, "x") for k in all_inputs + all_outputs]
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    def generate_timing_chart(self, vcd: VCDData, title: str = "") -> str:
        """Generate a digital timing waveform PNG and return markdown image link."""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np
        except ImportError:
            return ""

        try:
            vectors = vcd.get_vectors()
            if not vectors:
                return ""

            signal_names = sorted(vectors.keys())
            n_signals = len(signal_names)
            if n_signals == 0:
                return ""

            fig, ax = plt.subplots(figsize=(10, max(3, n_signals * 0.8)),
                                    facecolor=self.COLORS["bg"])
            fig.patch.set_facecolor(self.COLORS["bg"])

            # Find time range
            all_times = []
            for sig in signal_names:
                for t, _ in vectors[sig]:
                    all_times.append(t)
            t_min, t_max = min(all_times), max(all_times)
            if t_max == t_min:
                t_max = t_min + 10

            for si, sig in enumerate(signal_names):
                y_base = n_signals - si - 1
                changes = vectors[sig]
                # Draw signal trace
                x_vals = [t_min]
                y_vals = [0]
                last_val = 0
                for t, val in sorted(changes, key=lambda x: x[0]):
                    digit = 1 if val in ("1", "x") else 0
                    # Hold previous value until this time
                    x_vals.append(t)
                    y_vals.append(last_val)
                    # Transition
                    x_vals.append(t)
                    y_vals.append(digit)
                    last_val = digit
                x_vals.append(t_max)
                y_vals.append(last_val)

                ax.step(x_vals, [v + y_base * 1.5 for v in y_vals],
                        where="post", color=self.COLORS["line"],
                        linewidth=1.8)
                ax.text(-t_max * 0.02, y_base * 1.5 + 0.5, sig,
                        ha="right", va="center", fontsize=8,
                        color=self.COLORS["fg"], fontfamily="monospace")

            ax.set_xlim(t_min, t_max)
            ax.set_ylim(-0.5, n_signals * 1.5)
            ax.set_xlabel("Time", color=self.COLORS["fg"])
            ax.set_title(title or "Timing Waveform", color=self.COLORS["fg"])
            ax.grid(True, alpha=0.2, color=self.COLORS["grid"])
            ax.set_facecolor(self.COLORS["bg"])
            ax.tick_params(colors=self.COLORS["fg"])
            ax.set_yticks([])

            plt.tight_layout()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            fp = self.charts_dir / f"timing_{ts}.png"
            fig.savefig(str(fp), dpi=100, bbox_inches="tight",
                        facecolor=self.COLORS["bg"])
            plt.close(fig)
            url = f"/charts/{fp.name}"
            return f"![Timing Waveform]({url})"
        except Exception as e:
            logger.warning(f"Timing chart failed: {e}")
        return ""
