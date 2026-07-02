"""Digital logic pipeline — DSL → Verilog → Simulation → Truth Table → Report.

Single tool: simulate_logic
Internally chains: logic_svg (render) → logic_verilog (sim) → report
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.logic_pipeline")


_GATE_GUIDE = (
    "**Gate DSL** (one per line):\n"
    "`XOR(A, B) = Sum\nAND(A, B) = Carry`\n"
    "Supported gates: AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF\n"
    "\n"
    "**JSON modules** (one per line):\n"
    '`{"id":"reg0","type":"REGISTER","clk":"clk","d":"din","q":"dout"}`\n'
    "Module types: REGISTER, DFF, COUNTER, MUX, ALU, RAM, FIFO, ADDER, "
    "MULTIPLIER, COMPARATOR, SHIFTER, TRISTATE, CLOCK_GATE, etc.\n"
    "\n"
    "**Examples:**\n"
    "- Half-adder: `XOR(A, B) = Sum\\nAND(A, B) = Carry`\n"
    "- Full-adder: `XOR(A, B) = S1\\nXOR(S1, Cin) = Sum\\n"
    "AND(A, B) = C1\\nAND(S1, Cin) = C2\\nOR(C1, C2) = Cout`"
)


class LogicPipeline:
    TOOLS = [
        ("simulate_logic",
         "Design, simulate and analyze digital logic circuits. "
         "Generates schematic SVG, truth table, and timing waveform.\n"
         "\n" + _GATE_GUIDE,
         "simulate_logic",
         {"description": {"type": "string",
                          "description":
                          "Gate DSL or JSON modules. "
                          "Gates: XOR(A,B)=Sum. "
                          "JSON: {'id':'u1','type':'REGISTER','clk':'clk','d':'in','q':'out'}. "
                          "Supports mixed gate+module format."},
          "title": {"type": "string", "description": "Optional circuit title"}},
         ["description"]),
    ]

    def __init__(self, work_dir: str = "", charts_dir: str = "",
                 iverilog_path: str = "iverilog", vvp_path: str = "vvp",
                 sim_timeout: int = 30):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir = work_dir or str(self.charts_dir.parent)
        self.iverilog_path = iverilog_path
        self.vvp_path = vvp_path
        self.sim_timeout = sim_timeout

    # ── Tool entry point ──

    def simulate_logic(self, description: str, title: str = "") -> str:
        """Full pipeline: DSL → Verilog → iverilog → truth table → SVG → report."""
        try:
            return self._run_pipeline(description, title)
        except Exception as e:
            logger.exception(f"Logic pipeline failed: {e}")
            return f"Error: Logic simulation failed — {e}"

    def _run_pipeline(self, description: str, title: str) -> str:
        from .logic_svg import LogicSVG
        from .logic_verilog import VerilogGen, LogicSimulator, parse_vcd

        # ── Stage 1: Parse DSL ──
        gates, modules = LogicSVG._parse_mixed(description)
        if not gates and not modules:
            return "Error: No valid gates or modules found. Use gate DSL (e.g. XOR(A,B)=Sum) or JSON modules."

        # ── Stage 2: Render schematic SVG ──
        svg_link = self._render_logic_svg(gates, modules, title)

        # ── Stage 3: Generate Verilog ──
        warnings = []
        verilog_src = ""
        tb_src = ""
        sim_available = False
        truth_table = ""
        waveform_link = ""

        if gates:
            verilog_src = VerilogGen.gates_to_verilog(gates)
            tb_src = VerilogGen.generate_testbench(gates)

            # ── Stage 4-6: Run iverilog + parse VCD ──
            sim = LogicSimulator(
                work_dir=self.work_dir, charts_dir=str(self.charts_dir),
                iverilog_path=self.iverilog_path, vvp_path=self.vvp_path,
                timeout=self.sim_timeout,
            )
            sim_available, _ = sim.check_iverilog()

            if sim_available:
                vcd_text, sim_success, sim_error = sim.run_simulation(verilog_src, tb_src)
                if sim_success and vcd_text:
                    vcd = parse_vcd(vcd_text)
                    if vcd.changes:
                        truth_table = sim.generate_truth_table(vcd, gates)
                        waveform_link = sim.generate_timing_chart(vcd, title)
                if sim_error:
                    warnings.append(sim_error)
            else:
                warnings.append(
                    "iverilog not available. Schematic shown but simulation skipped. "
                    "Install: apt install iverilog / brew install icarus-verilog"
                )
        else:
            warnings.append("Modules-only circuit: simulation requires gate-level DSL.")

        # ── Stage 7: Build report ──
        return self._build_report(
            title=title or "Digital Logic Analysis",
            description=description,
            svg_link=svg_link,
            verilog_src=verilog_src,
            truth_table=truth_table,
            waveform_link=waveform_link,
            gates=gates,
            modules=modules,
            sim_available=sim_available,
            warnings=warnings,
        )

    # ── Internal helpers ──

    def _render_logic_svg(self, gates: list, modules: list, title: str) -> str:
        """Render schematic SVG using logic_svg.py."""
        try:
            from .logic_svg import LogicSVG
            a = LogicSVG(charts_dir=str(self.charts_dir))
            svg = a._render_mixed(gates, modules, title)
            if svg.startswith("Error"):
                return ""
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            fp = self.charts_dir / f"logic_{ts}.svg"
            fp.write_text(svg, encoding="utf-8")
            url = f"/charts/{fp.name}"
            return f"![{title or 'Schematic'}]({url})"
        except Exception as e:
            logger.warning(f"Logic SVG render failed: {e}")
            return ""

    def _build_report(self, title: str, description: str, svg_link: str,
                      verilog_src: str, truth_table: str, waveform_link: str,
                      gates: list, modules: list, sim_available: bool,
                      warnings: list) -> str:
        """Build markdown report."""
        lines = [f"# Digital Logic Report: {title}", ""]

        # Schematic
        if svg_link:
            lines.append("## Schematic")
            lines.append(svg_link)
            lines.append("")

        # Circuit description
        lines.append("## Circuit Description")
        lines.append(f"- Gates: {len(gates)}")
        lines.append(f"- Modules: {len(modules)}")
        lines.append("")

        # DSL listing
        lines.append("## DSL Source")
        lines.append("```")
        lines.append(description)
        lines.append("```")
        lines.append("")

        # Verilog
        if verilog_src:
            lines.append("## Generated Verilog")
            lines.append("```verilog")
            lines.append(verilog_src)
            lines.append("```")
            lines.append("")

        # Simulation status
        lines.append("## Simulation")
        if sim_available:
            lines.append("| Status | ✅ Success |")
        else:
            lines.append("| Status | ⚠️ Skipped (iverilog not installed) |")
        lines.append("")

        # Truth table
        if truth_table:
            lines.append("## Truth Table")
            lines.append(truth_table)
            lines.append("")

        # Waveform
        if waveform_link:
            lines.append("## Timing Waveform")
            lines.append(waveform_link)
            lines.append("")

        # Warnings
        if warnings:
            lines.append("## Notes")
            for w in warnings:
                lines.append(f"- ⚠️ {w}")
            lines.append("")

        return "\n".join(lines)
