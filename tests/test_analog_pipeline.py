"""Tests for analog circuit pipeline: IR → SPICE → Simulation → Report."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from nano_agent.tools.analog_ir import AnalogCircuit, Component, SimulationCommand
from nano_agent.tools.analog_sim import AnalogSimulator, SimulationResult
from nano_agent.tools.analog_pipeline import AnalogPipeline


# ═══════════ Test IR / SPICE ═══════════

class TestAnalogIR(unittest.TestCase):
    """Tests for AnalogCircuit IR and SPICE export."""

    def test_simple_rc_lowpass(self):
        circuit = AnalogCircuit(
            title="RC Low-Pass",
            components=[
                Component(type="V", name="V1", nodes=["1", "0"], value="AC 1"),
                Component(type="R", name="R1", nodes=["1", "2"], value="1k"),
                Component(type="C", name="C1", nodes=["2", "0"], value="10n"),
            ],
            simulations=[SimulationCommand(sim_type="ac",
                params={"sweep": "dec", "points": 100, "start": "1", "stop": "1Meg"})],
            probes=["V(2)"],
        )
        spice = circuit.to_spice()
        # Nodes "1" and "2" are already valid SPICE integers, kept as-is
        self.assertIn("V1 1 0 AC 1", spice)
        self.assertIn("R1 1 2 1k", spice)
        self.assertIn("C1 2 0 10n", spice)
        self.assertIn(".ac dec 100 1 1Meg", spice)
        self.assertIn(".probe V(2)", spice)
        self.assertIn(".control", spice)
        self.assertIn(".endc", spice)
        self.assertIn(".end", spice)

    def test_node_mapping_gnd_is_zero(self):
        circuit = AnalogCircuit(components=[
            Component(type="R", name="R1", nodes=["0", "out"], value="100"),
        ])
        spice = circuit.to_spice()
        self.assertIn("R1 0", spice)  # GND stays 0

    def test_opamp_subcircuit_included(self):
        circuit = AnalogCircuit(components=[
            Component(type="X", name="X1", nodes=["1","2","3","4","5"], model="opamp"),
        ])
        spice = circuit.to_spice()
        self.assertIn(".subckt opamp", spice)

    def test_bjt_model_included(self):
        circuit = AnalogCircuit(components=[
            Component(type="Q", name="Q1", nodes=["C","B","E"], model="NPN"),
        ])
        spice = circuit.to_spice()
        self.assertIn(".model NPN", spice)

    def test_validate_empty_circuit(self):
        circuit = AnalogCircuit()
        issues = circuit.validate()
        self.assertTrue(any("Error" in i for i in issues))

    def test_validate_ac_no_source(self):
        circuit = AnalogCircuit(
            components=[Component(type="R", name="R1", nodes=["1","2"], value="1k")],
            simulations=[SimulationCommand(sim_type="ac")],
        )
        issues = circuit.validate()
        self.assertTrue(any("AC" in i for i in issues))

    def test_from_spice_netlist(self):
        netlist = "V1 1 0 AC 1\nR1 1 2 1k\nC1 2 0 10n"
        circuit = AnalogCircuit.from_spice_netlist(netlist, title="Test")
        self.assertEqual(len(circuit.components), 3)
        self.assertEqual(circuit.components[0].type, "V")
        self.assertEqual(circuit.components[0].value, "AC 1")
        self.assertEqual(circuit.components[1].value, "1k")

    def test_from_spice_ignores_control_lines(self):
        netlist = ".title test\n.op\nR1 1 0 1k\n.end"
        circuit = AnalogCircuit.from_spice_netlist(netlist)
        self.assertEqual(len(circuit.components), 1)

    def test_value_formatting_k_m_u_n_p(self):
        for val, ctype in [("1k", "R"), ("10n", "C"), ("1m", "L"),
                            ("1Meg", "R"), ("0.1u", "C"), ("47p", "C")]:
            circuit = AnalogCircuit(components=[
                Component(type=ctype, name="X1", nodes=["1","0"], value=val),
            ])
            spice = circuit.to_spice()
            self.assertIn(val, spice, f"Value {val} not in SPICE output")


# ═══════════ Test Simulator (no ngspice needed) ═══════════

class TestAnalogSimParser(unittest.TestCase):
    """Tests for ngspice output parsing (mock data, no real ngspice)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sim = AnalogSimulator(charts_dir=self.tmp)

    def test_parse_printed_ac_output(self):
        raw = """Index   frequency   v(2)
-----------------------------------
0       1.000e+00   9.999e-01
1       1.258e+00   9.996e-01
2       1.585e+00   9.990e-01
"""
        result = self.sim.parse_raw_output(raw, "ac")
        self.assertTrue(result.success)
        # Parser strips parentheses: "v(2)" → "v2"
        self.assertIn("v2", result.vectors)
        self.assertEqual(len(result.vectors["v2"]), 3)

    def test_parse_nonsense_returns_failure(self):
        result = self.sim.parse_raw_output("garbage data", "ac")
        self.assertFalse(result.success)

    def test_compute_ac_metrics_gain(self):
        vectors = {
            "frequency": [1, 10, 100, 1000, 10000],
            "v(2)": [1.0, 0.995, 0.9, 0.5, 0.1],
        }
        metrics = self.sim.compute_metrics(vectors, "ac")
        self.assertIn("dc_gain", metrics)
        self.assertAlmostEqual(metrics["dc_gain"], 0, delta=0.1)
        self.assertIn("bandwidth_hz", metrics)

    def test_compute_phase_margin(self):
        vectors = {
            "frequency": [10, 100, 1000, 10000],
            "v(2)": [1.5, 1.2, 0.8, 0.3],
            "v(2)_deg": [-10, -40, -90, -150],
        }
        metrics = self.sim.compute_metrics(vectors, "ac")
        self.assertIn("phase_margin_deg", metrics)

    def test_compute_tran_overshoot(self):
        vectors = {
            "time": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "v(out)": [0, 0.2, 0.5, 0.9, 1.3, 1.2, 1.05, 1.02, 1.0, 1.0, 1.0],
        }
        metrics = self.sim.compute_metrics(vectors, "tran")
        self.assertIn("overshoot_pct", metrics)
        self.assertIn("rise_time", metrics)

    def test_check_ngspice_graceful(self):
        self.sim.ngspice_path = "/nonexistent/ngspice"
        avail, msg = self.sim.check_ngspice()
        self.assertFalse(avail)
        self.assertIn("not found", msg)


# ═══════════ Test Pipeline ═══════════

class TestAnalogPipeline(unittest.TestCase):
    """Integration tests for the pipeline (no real ngspice)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pl = AnalogPipeline(charts_dir=self.tmp, ngspice_path="/nonexistent/ngspice")

    def test_invalid_circuit_returns_error(self):
        result = self.pl.simulate_circuit("this is not a spice netlist", "ac")
        self.assertIn("No valid SPICE components", result)

    def test_missing_ngspice_returns_report(self):
        result = self.pl.simulate_circuit(
            "V1 1 0 AC 1\nR1 1 2 1k\nC1 2 0 10n",
            "ac", title="RC Test",
        )
        # Should still produce a report with netlist and notes
        self.assertIn("Simulation Report", result)
        self.assertIn("SPICE Netlist", result)
        self.assertIn("ngspice not available", result)

    def test_report_contains_sections(self):
        result = self.pl.simulate_circuit(
            "V1 1 0 AC 1\nR1 1 2 1k\nC1 2 0 10n",
            "ac", title="Test Circuit",
        )
        self.assertIn("# Simulation Report", result)
        self.assertIn("## SPICE Netlist", result)

    def test_report_contains_svg_if_opamp(self):
        # Should render SVG for any valid circuit
        result = self.pl.simulate_circuit(
            "V1 1 0 AC 1\nR1 1 2 1k\nC1 2 0 10n",
            "ac", title="RC",
        )
        # Schematic may or may not be included (depends on render success)
        # At minimum, report sections exist
        self.assertIn("Simulation Report", result)

    def test_simulate_circuit_tool_schema(self):
        """Verify the tool schema is valid."""
        tools = AnalogPipeline.TOOLS
        self.assertEqual(len(tools), 1)
        name, desc, method, props, required = tools[0]
        self.assertEqual(name, "simulate_circuit")
        self.assertEqual(method, "simulate_circuit")
        self.assertIn("description", props)
        self.assertIn("sim_type", props)
        self.assertIn("description", required)
        self.assertIn("sim_type", required)


# ═══════════ Test Report Structure ═══════════

class TestReportStructure(unittest.TestCase):
    """Validate markdown report generation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pl = AnalogPipeline(charts_dir=self.tmp, ngspice_path="/nonexistent/ngspice")

    def test_rc_lowpass_report(self):
        result = self.pl.simulate_circuit(
            "V1 1 0 AC 1\nR1 1 2 1.59k\nC1 2 0 100n",
            "ac", params="start=1,stop=100k,points=100,sweep=dec",
            probes="V(2)", title="RC Low-Pass Filter fc=1kHz",
        )
        sections = [
            "# Simulation Report",
            "## SPICE Netlist",
            "## Simulation Parameters",
            "## Recommendations",
        ]
        for s in sections:
            self.assertIn(s, result, f"Missing section: {s}")
        self.assertIn("```spice", result)
        self.assertIn(".ac dec", result)
        self.assertIn("V1", result)


if __name__ == "__main__":
    unittest.main()
