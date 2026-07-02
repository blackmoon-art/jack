"""Analog circuit pipeline — NL → SPICE → Simulation → Waveform → Report.

Single tool: simulate_circuit
Internally chains: analog_ir → analog_sim → analog_svg → chart generation
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .observation import Observation

logger = logging.getLogger("nano_agent.tools.analog_pipeline")


class AnalogPipeline:
    TOOLS = [
        ("simulate_circuit",
         "Design, simulate, and analyze analog circuits. Runs ngspice simulation "
         "and generates a full report with schematic, waveforms, and metrics.\n"
         "\n"
         "**SPICE format, one component per line:**\n"
         "`R1 1 2 1k` — resistor between node 1 and 2\n"
         "`C1 2 0 10n` — capacitor, node 0 = GND\n"
         "`V1 1 0 AC 1` — AC voltage source\n"
         "`X1 in+ in- out vcc 0 opamp` — op-amp\n"
         "\n"
         "**Examples:**\n"
         "- RC low-pass: `V1 1 0 AC 1\\nR1 1 2 1k\\nC1 2 0 10n`\n"
         "- Sallen-Key: `V1 1 0 AC 1\\nR1 1 2 10k\\nR2 2 3 10k\\n"
         "C1 2 4 10n\\nC2 3 0 10n\\nX1 3 4 4 5 0 opamp`\n"
         "- Inverting amp: `V1 1 0 AC 1\\nR1 1 2 1k\\nRf 2 3 10k\\n"
         "X1 2 0 3 4 0 opamp`",
         "simulate_circuit",
         {"description": {"type": "string",
                          "description":
                          "SPICE netlist. One component per line. "
                          "R/C/L/D/V/I/Q/X for resistor/capacitor/inductor/diode/source/transistor/opamp. "
                          "Node 0 = GND. "
                          "Example: 'V1 1 0 AC 1\\nR1 1 2 1k\\nC1 2 0 10n' for RC low-pass"},
          "sim_type": {"type": "string",
                       "enum": ["ac", "tran", "dc", "op"],
                       "description":
                       "Simulation type. ac=frequency sweep, tran=time domain, dc=DC sweep, op=operating point"},
          "params": {"type": "string",
                     "description":
                     "Simulation parameters. Format: 'key=value,key=value'\n"
                     ".ac: 'start=1,stop=1Meg,points=100,sweep=dec'\n"
                     ".tran: 'tstep=1u,tstop=1m'\n"
                     ".dc: 'source=V1,start=0,stop=5,step=0.1'\n"
                     ".op: leave empty"},
          "probes": {"type": "string",
                     "description":
                     "Comma-separated output variables to probe. "
                     "Example: 'V(2),V(3)'. Default: auto-detect from circuit"},
          "title": {"type": "string", "description": "Optional circuit title"}},
         ["description", "sim_type"]),
    ]

    def __init__(self, work_dir: str = "", charts_dir: str = "",
                 ngspice_path: str = "ngspice", sim_timeout: int = 30):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir = work_dir or str(self.charts_dir.parent)
        self.ngspice_path = ngspice_path
        self.sim_timeout = sim_timeout

    # ── Tool entry point ──

    def simulate_circuit(self, description: str, sim_type: str,
                         params: str = "", probes: str = "",
                         title: str = "") -> str:
        """Full pipeline: SPICE → ngspice simulation → SVG schematic → waveform → metrics → report.

        Returns markdown report string, or error Observation text.
        """
        try:
            return self._run_pipeline(description, sim_type, params, probes, title)
        except Exception as e:
            logger.exception(f"Pipeline failed: {e}")
            return f"Error: Circuit simulation failed — {e}"

    def _run_pipeline(self, description: str, sim_type: str,
                      params_str: str, probes_str: str,
                      title: str) -> str:
        """Internal pipeline orchestration."""

        # ── Stage 1: Parse SPICE → IR ──
        from .analog_ir import AnalogCircuit, SimulationCommand
        circuit = AnalogCircuit.from_spice_netlist(description, title=title)
        if not circuit.components:
            return "Error: No valid SPICE components found. Use format: R1 1 2 1k"

        # ── Stage 2: Build simulation command ──
        sim_params = self._parse_params(params_str, sim_type)
        probes_list = [p.strip() for p in probes_str.split(",") if p.strip()] if probes_str else []
        if not probes_list:
            probes_list = self._auto_probes(circuit, sim_type)
        sim_cmd = SimulationCommand(sim_type=sim_type, params=sim_params)
        circuit.simulations = [sim_cmd]
        circuit.probes = probes_list

        # Validate
        warnings = circuit.validate()
        if any(w.startswith("Error:") for w in warnings):
            return "\n".join(warnings)

        # ── Stage 3: IR → SPICE netlist ──
        netlist = circuit.to_spice()

        # ── Stage 4: Render schematic SVG ──
        svg_link = self._render_schematic(description, title)

        # ── Stage 5-6: Run ngspice + parse ──
        from .analog_sim import AnalogSimulator
        sim = AnalogSimulator(
            work_dir=self.work_dir, charts_dir=str(self.charts_dir),
            ngspice_path=self.ngspice_path, timeout=self.sim_timeout,
        )

        available, _ = sim.check_ngspice()
        sim_success = False
        sim_result = None
        metrics = {}
        waveform_link = ""

        if available:
            raw, sim_success, sim_error = sim.run_ngspice(netlist)
            if sim_success:
                sim_result = sim.parse_raw_output(raw, sim_type)
                if sim_result.success and sim_result.vectors:
                    metrics = sim.compute_metrics(sim_result.vectors, sim_type)
                    waveform_link = sim.generate_waveform_chart(
                        sim_result.vectors, sim_type, title
                    )
            else:
                sim_result = None
                if sim_error:
                    warnings.append(f"Simulation error: {sim_error[:500]}")
        else:
            warnings.append(
                "ngspice not available. Schematic and netlist are shown, "
                "but simulation skipped. Install ngspice for full analysis."
            )

        # ── Stage 7: Build report ──
        return self._build_report(
            title=title or circuit.title or "Circuit Analysis",
            netlist=netlist,
            svg_link=svg_link,
            waveform_link=waveform_link,
            sim_type=sim_type,
            sim_params=sim_params,
            sim_success=sim_success,
            metrics=metrics,
            warnings=warnings,
            sim_available=available,
        )

    # ── Internal helpers ──

    @staticmethod
    def _parse_params(params_str: str, sim_type: str) -> dict:
        """Parse 'key=value,key=value' into dict."""
        params = {}
        if params_str:
            for pair in params_str.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k.strip()] = v.strip()
        # Defaults
        if sim_type == "ac":
            params.setdefault("start", "1")
            params.setdefault("stop", "1Meg")
            params.setdefault("points", 100)
            params.setdefault("sweep", "dec")
        elif sim_type == "tran":
            params.setdefault("tstep", "1u")
            params.setdefault("tstop", "1m")
        elif sim_type == "dc":
            params.setdefault("source", "V1")
            params.setdefault("start", "0")
            params.setdefault("stop", "5")
            params.setdefault("step", "0.1")
        return params

    @staticmethod
    def _auto_probes(circuit, sim_type: str) -> list[str]:
        """Auto-detect probe nodes from circuit components."""
        probes = []
        # Find output nodes (nodes that only connect to one component's terminal)
        node_refs: dict[str, int] = {}
        for c in circuit.components:
            for n in c.nodes:
                node_refs[str(n)] = node_refs.get(str(n), 0) + 1
        for n, refs in node_refs.items():
            if refs == 1 and n != "0":
                probes.append(f"V({n})")
        if not probes:
            # Fallback: probe all non-GND nodes
            all_nodes = set()
            for c in circuit.components:
                for n in c.nodes:
                    if str(n) != "0":
                        all_nodes.add(str(n))
            probes = [f"V({n})" for n in sorted(all_nodes)[:3]]
        return probes[:5]  # Max 5 probes

    def _render_schematic(self, description: str, title: str) -> str:
        """Render schematic SVG using analog_svg.py."""
        try:
            from .analog_svg import AnalogSVG
            a = AnalogSVG(charts_dir=str(self.charts_dir))
            # Call _render directly for raw SVG, write ourselves
            svg = a._render(description, title)
            if svg.startswith("Error"):
                return ""
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            fp = self.charts_dir / f"analog_{ts}.svg"
            fp.write_text(svg, encoding="utf-8")
            url = f"/charts/{fp.name}"
            return f"![{title or 'Schematic'}]({url})"
        except Exception as e:
            logger.warning(f"Schematic render failed: {e}")
            return ""

    def _build_report(self, title: str, netlist: str, svg_link: str,
                      waveform_link: str, sim_type: str,
                      sim_params: dict, sim_success: bool,
                      metrics: dict, warnings: list,
                      sim_available: bool) -> str:
        """Build markdown simulation report."""
        lines = [f"# Simulation Report: {title}", ""]

        # Schematic
        if svg_link:
            lines.append("## Schematic")
            lines.append(svg_link)
            lines.append("")

        # Netlist
        lines.append("## SPICE Netlist")
        lines.append("```spice")
        lines.append(netlist)
        lines.append("```")
        lines.append("")

        # Simulation parameters
        lines.append("## Simulation Parameters")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        lines.append(f"| Type | {sim_type} |")
        for k, v in sim_params.items():
            lines.append(f"| {k} | {v} |")
        lines.append(f"| Status | {'✅ Success' if sim_success else '⚠️ Skipped'} |")
        if not sim_available:
            lines.append(f"| Note | ngspice not installed. Install with: `apt install ngspice` |")
        lines.append("")

        # Waveform
        if waveform_link:
            lines.append("## Waveforms")
            lines.append(waveform_link)
            lines.append("")

        # Metrics
        if metrics:
            lines.append("## Key Metrics")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            label_map = {
                "dc_gain": "DC Gain (dB)",
                "bandwidth_hz": "-3dB Bandwidth (Hz)",
                "phase_margin_deg": "Phase Margin (deg)",
                "overshoot_pct": "Overshoot (%)",
                "rise_time": "Rise Time (s)",
                "transfer_slope": "Transfer Slope",
            }
            for k, v in metrics.items():
                if k in label_map:
                    lines.append(f"| {label_map[k]} | {v} |")
            lines.append("")

        # Analysis
        if metrics:
            lines.append("## Analysis")
            observations = self._generate_observations(metrics, sim_type)
            for obs in observations:
                lines.append(f"- {obs}")
            lines.append("")

        # Recommendations
        lines.append("## Recommendations")
        recs = self._generate_recommendations(metrics, sim_type)
        for r in recs:
            lines.append(f"- {r}")
        lines.append("")

        # Warnings
        if warnings:
            lines.append("## Notes")
            for w in warnings:
                lines.append(f"- ⚠️ {w}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _generate_observations(metrics: dict, sim_type: str) -> list[str]:
        """Auto-generate analysis observations from metrics."""
        obs = []
        if sim_type == "ac":
            if "dc_gain" in metrics:
                db = metrics["dc_gain"]
                obs.append(
                    f"DC gain is {db} dB. "
                    + ("The circuit provides amplification." if db > 3
                       else "The circuit is passive or near unity-gain."))
            if "bandwidth_hz" in metrics:
                bw = metrics["bandwidth_hz"]
                obs.append(f"-3dB bandwidth is approximately {bw:.0f} Hz.")
            if "phase_margin_deg" in metrics:
                pm = metrics["phase_margin_deg"]
                if pm < 45:
                    obs.append(f"Low phase margin ({pm}°): potential stability concern. Consider compensation.")
                elif pm < 60:
                    obs.append(f"Moderate phase margin ({pm}°): acceptable but could be improved.")
                else:
                    obs.append(f"Good phase margin ({pm}°): circuit is stable.")
        elif sim_type == "tran":
            if "overshoot_pct" in metrics:
                pct = metrics["overshoot_pct"]
                obs.append(f"Overshoot is {pct:.1f}%.")
            if "rise_time" in metrics:
                obs.append(f"Rise time is {metrics['rise_time']:.2e}s.")
        elif sim_type == "dc":
            if "transfer_slope" in metrics:
                obs.append(f"Transfer slope: {metrics['transfer_slope']:.4f} V/V.")
        return obs or ["Simulation completed. Review waveforms for details."]

    @staticmethod
    def _generate_recommendations(metrics: dict, sim_type: str) -> list[str]:
        """Auto-generate recommendations from metrics."""
        recs = []
        if sim_type == "ac":
            if "phase_margin_deg" in metrics and metrics["phase_margin_deg"] < 45:
                recs.append("Add compensation capacitor or increase feedback resistance for better stability.")
            if "bandwidth_hz" in metrics:
                recs.append(f"To adjust bandwidth, modify RC values proportionally (double R = half BW).")
        elif sim_type == "tran":
            if "overshoot_pct" in metrics and metrics["overshoot_pct"] > 10:
                recs.append("Reduce overshoot by increasing damping (add series resistance or reduce gain).")
        return recs or ["Circuit appears to function as expected. Further optimization can be done based on design requirements."]
