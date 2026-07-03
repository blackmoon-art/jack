"""Tests for digital circuit pipeline: Verilog, simulation, synthesis, SVG, layout."""

import pytest
import tempfile
from pathlib import Path


# ══════════════════════════════════════════════════════════
# Template matching
# ══════════════════════════════════════════════════════════

class TestTemplateMatching:
    def test_half_adder_match(self):
        from nano_agent.tools.digital.digital_circuit import (
            DigitalCircuit, _DIGITAL_TEMPLATES)
        tmpl = DigitalCircuit._match_digital_template("half adder")
        assert tmpl["name"] == "Half Adder"
        assert "module half_adder" in tmpl["verilog"]

    def test_full_adder_match(self):
        from nano_agent.tools.digital.digital_circuit import DigitalCircuit
        tmpl = DigitalCircuit._match_digital_template("full adder")
        assert tmpl["name"] == "Full Adder"

    def test_dff_match(self):
        from nano_agent.tools.digital.digital_circuit import DigitalCircuit
        tmpl = DigitalCircuit._match_digital_template("d flip-flop")
        assert tmpl["name"] == "D Flip-Flop"

    def test_counter_match_chinese(self):
        from nano_agent.tools.digital.digital_circuit import DigitalCircuit
        tmpl = DigitalCircuit._match_digital_template("4位计数器")
        assert tmpl["name"] == "4-Bit Binary Counter"

    def test_mux_match(self):
        from nano_agent.tools.digital.digital_circuit import DigitalCircuit
        tmpl = DigitalCircuit._match_digital_template("2-to-1 mux")
        assert "mux_2to1" in tmpl["verilog"]

    def test_unknown_fails(self):
        from nano_agent.tools.digital.digital_circuit import DigitalCircuit
        with pytest.raises(ValueError, match="No digital template"):
            DigitalCircuit._match_digital_template("quantum computer")


# ══════════════════════════════════════════════════════════
# DSL generation (yosys netlist → logic DSL)
# ══════════════════════════════════════════════════════════

class TestYosysToDSL:
    def test_half_adder_dsl(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _yosys_netlist_to_logic_dsl)
        cells = [
            {"type": "$_AND_", "name": "a1", "connections": {
                "A": ["a"], "B": ["b"], "Y": ["carry"]}},
            {"type": "$_XOR_", "name": "x1", "connections": {
                "A": ["a"], "B": ["b"], "Y": ["sum"]}},
        ]
        dsl = _yosys_netlist_to_logic_dsl(cells=cells)
        assert "AND(a,b) = carry" in dsl
        assert "XOR(a,b) = sum" in dsl

    def test_dff_dsl(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _yosys_netlist_to_logic_dsl)
        cells = [
            {"type": "$_DFFE_PP0P_", "name": "ff1", "connections": {
                "C": ["clk"], "D": ["d"], "E": ["en"], "R": ["rst"],
                "Q": ["q"]}},
        ]
        dsl = _yosys_netlist_to_logic_dsl(cells=cells)
        assert "DFF(clk,d,en,rst) = q" in dsl

    def test_not_gate_dsl(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _yosys_netlist_to_logic_dsl)
        cells = [
            {"type": "$_NOT_", "name": "n1", "connections": {
                "A": ["a"], "Y": ["not_a"]}},
        ]
        dsl = _yosys_netlist_to_logic_dsl(cells=cells)
        assert "NOT(a) = not_a" in dsl

    def test_multi_bit_port_names(self):
        from nano_agent.tools.digital.verilog_synthesizer import synthesize
        # Circuit with real gates so yosys produces gate netlist
        verilog = "module m(input [1:0] a, input [1:0] b, output [1:0] y); assign y = a & b; endmodule"
        result = synthesize(verilog, "m")
        assert result["success"]
        assert result["gate_count"] >= 2  # at least 2 AND gates for 2 bits


# ══════════════════════════════════════════════════════════
# Gate decomposition (AND/OR/NOT only)
# ══════════════════════════════════════════════════════════

class TestGateDecomposition:
    def test_nand_decompose(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _decompose_to_and_or_not)
        lines = ["NAND(a,b) = y"]
        result = _decompose_to_and_or_not(lines)
        # Should produce AND + NOT
        assert any("AND(a,b)" in l for l in result)
        assert any("NOT(" in l and "= y" in l for l in result)

    def test_nor_decompose(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _decompose_to_and_or_not)
        lines = ["NOR(a,b) = y"]
        result = _decompose_to_and_or_not(lines)
        assert any("OR(a,b)" in l for l in result)
        assert any("NOT(" in l and "= y" in l for l in result)

    def test_xor_decompose(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _decompose_to_and_or_not)
        lines = ["XOR(a,b) = y"]
        result = _decompose_to_and_or_not(lines)
        # XOR = OR(AND(a,NOT(b)), AND(NOT(a),b)) → 5 gates
        assert len(result) == 5
        gate_types = [l.split("(")[0] for l in result]
        assert "AND" in gate_types
        assert "OR" in gate_types
        assert "NOT" in gate_types

    def test_passthrough_and_or_not(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _decompose_to_and_or_not)
        lines = ["AND(a,b) = c", "OR(d,e) = f", "NOT(g) = h"]
        result = _decompose_to_and_or_not(lines)
        assert result == lines

    def test_mux_decompose(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _decompose_to_and_or_not)
        lines = ["MUX(a,b,s) = y"]
        result = _decompose_to_and_or_not(lines)
        # MUX = OR(AND(a,NOT(s)), AND(b,s)) → 4 gates
        assert len(result) == 4

    def test_dsl_with_decompose_flag(self):
        from nano_agent.tools.digital.verilog_synthesizer import (
            _yosys_netlist_to_logic_dsl)
        cells = [
            {"type": "$_NAND_", "name": "n1", "connections": {
                "A": ["a"], "B": ["b"], "Y": ["y"]}},
        ]
        dsl = _yosys_netlist_to_logic_dsl(cells=cells, decompose=True)
        assert "NAND" not in dsl
        assert "AND" in dsl
        assert "NOT" in dsl


# ══════════════════════════════════════════════════════════
# Auto testbench generation
# ══════════════════════════════════════════════════════════

class TestAutoTestbench:
    def test_simple_module(self):
        from nano_agent.tools.digital.verilog_compiler import auto_testbench
        verilog = "module half_adder(input a, b, output sum, carry); assign sum = a ^ b; assign carry = a & b; endmodule"
        tb = auto_testbench(verilog, num_vectors=4)
        assert "module tb;" in tb
        assert "half_adder uut" in tb
        assert "PASS: auto_testbench" in tb
        assert "a = " in tb
        assert "b = " in tb

    def test_bus_module(self):
        from nano_agent.tools.digital.verilog_compiler import auto_testbench
        verilog = "module adder(input [3:0] a, input [3:0] b, output [3:0] sum); assign sum = a + b; endmodule"
        tb = auto_testbench(verilog, num_vectors=4)
        assert "[3:0] a;" in tb
        assert "[3:0] b;" in tb
        assert "[3:0] sum;" in tb

    def test_compiles_and_runs(self):
        from nano_agent.tools.digital.verilog_compiler import (
            auto_testbench, compile_verilog, run_simulation, cleanup_vvp)
        verilog = "module and_gate(input a, b, output y); assign y = a & b; endmodule"
        tb = auto_testbench(verilog, num_vectors=4)
        comp = compile_verilog(verilog, tb)
        assert comp["success"], f"Compile failed: {comp['errors']}"
        if comp.get("vvp_path"):
            sim = run_simulation(comp["vvp_path"])
            cleanup_vvp(comp["vvp_path"])
            assert sim["success"]


# ══════════════════════════════════════════════════════════
# LogicSVG parser
# ══════════════════════════════════════════════════════════

class TestLogicSVGParser:
    def test_parse_half_adder(self):
        from nano_agent.tools.logic_svg import LogicSVG
        lsv = LogicSVG()
        gates, inputs, outputs = lsv._parse(
            "XOR(a,b) = sum\nAND(a,b) = carry")
        assert len(gates) == 2
        assert inputs == {"a", "b"}
        assert outputs == {"sum", "carry"}

    def test_parse_full_adder(self):
        from nano_agent.tools.logic_svg import LogicSVG
        lsv = LogicSVG()
        dsl = ("XOR(a,b) = g1\nXOR(g1,cin) = sum\n"
               "AND(a,b) = g2\nAND(g1,cin) = g3\nOR(g2,g3) = cout")
        gates, inputs, outputs = lsv._parse(dsl)
        assert len(gates) == 5
        assert inputs == {"a", "b", "cin"}
        assert outputs == {"sum", "cout"}

    def test_parse_dff_feedback(self):
        from nano_agent.tools.logic_svg import LogicSVG
        lsv = LogicSVG()
        # DFF output feeds back to NOT gate
        dsl = "DFF(clk,d,en,rst) = count[0]\nNOT(count[0]) = n9"
        gates, inputs, outputs = lsv._parse(dsl)
        assert len(gates) == 2
        # count[0] should be kept as output (DFF output port)
        assert "count[0]" in outputs, f"DFF output missing from outputs: {outputs}"

    def test_parse_bracket_outputs(self):
        from nano_agent.tools.logic_svg import LogicSVG
        lsv = LogicSVG()
        dsl = ("NOT(count[0]) = n9\nDFF(clk,n9,en,rst) = count[0]\n"
               "AND(count[0],count[1]) = n10\nDFF(clk,n10,en,rst) = count[1]")
        gates, inputs, outputs = lsv._parse(dsl)
        assert len(gates) == 4
        assert "count[0]" in outputs
        assert "count[1]" in outputs

    def test_empty_dsl(self):
        from nano_agent.tools.logic_svg import LogicSVG
        lsv = LogicSVG()
        gates, inputs, outputs = lsv._parse("")
        assert gates == []
        assert inputs == set()
        assert outputs == set()


# ══════════════════════════════════════════════════════════
# SVG layout analysis
# ══════════════════════════════════════════════════════════

class TestLayoutQuality:
    def test_clean_svg_score_10(self):
        from nano_agent.tools.logic_svg import LogicSVG
        # Minimal valid SVG with 1 gate, 0 crossings
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">
<rect width="400" height="200" fill="#1a1a2e"/>
<g><path d="M100,50 L100,100 L180,75 Z" fill="#2a2a4e" stroke="#7c3aed"/><text x="140" y="77">AND</text></g>
<rect x="36" y="66" width="48" height="28" rx="4" fill="#0f172a" stroke="#3b82f6"/><text x="60" y="82">a</text>
<rect x="36" y="146" width="48" height="28" rx="4" fill="#0f172a" stroke="#3b82f6"/><text x="60" y="162">b</text>
<rect x="316" y="66" width="48" height="28" rx="4" fill="#0f172a" stroke="#3b82f6"/><text x="340" y="82">y</text>
</svg>'''
        result = LogicSVG.score_layout_quality(svg)
        assert result["score"] >= 8, f"Expected clean score, got {result}"

    def test_counts_gate_types(self):
        from nano_agent.tools.logic_svg import LogicSVG
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="500" height="200">
<rect width="500" height="200" fill="#1a1a2e"/>
<g><path d="M100,50 L100,100 L180,75 Z" fill="#2a2a4e" stroke="#7c3aed"/><text x="140" y="77">AND</text></g>
<g><path d="M300,50 L300,100 L380,75 Z" fill="#2a2a4e" stroke="#7c3aed"/><text x="340" y="77">OR</text></g>
</svg>'''
        result = LogicSVG.score_layout_quality(svg)
        assert "AND" in result["metrics"]["gate_types"]
        assert "OR" in result["metrics"]["gate_types"]

    def test_has_summary(self):
        from nano_agent.tools.logic_svg import LogicSVG
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">
<rect width="400" height="200" fill="#1a1a2e"/>
</svg>'''
        result = LogicSVG.score_layout_quality(svg)
        assert "summary" in result
        assert "score" in result

    def test_invalid_svg_returns_default(self):
        from nano_agent.tools.logic_svg import LogicSVG
        result = LogicSVG.score_layout_quality("not valid svg")
        assert result["score"] == 10.0
        assert "Could not parse" in result["issues"][0]
