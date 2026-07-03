"""Digital circuit end-to-end pipeline: NL→Verilog→compile→simulate→synthesize→SVG.

Three entry points:
  design_digital  — full closed-loop: NL→Verilog→compile→sim→synth→render
  simulate_verilog — Verilog + testbench → iverilog compile → vvp simulate
  synthesize_gates — Verilog → yosys → gate netlist + stats

Architecture mirrors analog_svg.py / spice_simulator.py.
"""

import json as _json
import logging
import re
from datetime import datetime
from pathlib import Path

from .verilog_compiler import compile_verilog, run_simulation, cleanup_vvp
from .verilog_synthesizer import synthesize, _yosys_netlist_to_logic_dsl

logger = logging.getLogger("nano_agent.tools.digital_circuit")

# ═══════════ Digital Circuit Templates ═══════════

_DIGITAL_TEMPLATES = {
    ("combinational", "half_adder"): {
        "name": "Half Adder",
        "keywords_cn": ["半加器", "half adder", "半加"],
        "guide": "A half adder adds two 1-bit numbers. Outputs Sum and Carry.",
        "verilog": (
            "module half_adder(\n"
            "  input  a, b,\n"
            "  output sum, carry\n"
            ");\n"
            "  assign sum   = a ^ b;\n"
            "  assign carry = a & b;\n"
            "endmodule\n"
        ),
        "testbench": (
            "module tb;\n"
            "  reg a, b;\n"
            "  wire sum, carry;\n"
            "  half_adder uut(.a(a), .b(b), .sum(sum), .carry(carry));\n"
            "  initial begin\n"
            "    $display(\"a b | sum carry\");\n"
            '    $display("------+----------");\n'
            "    a=0; b=0; #10 $display(\"%b %b |  %b    %b\", a,b,sum,carry);\n"
            "    a=0; b=1; #10 $display(\"%b %b |  %b    %b\", a,b,sum,carry);\n"
            "    a=1; b=0; #10 $display(\"%b %b |  %b    %b\", a,b,sum,carry);\n"
            "    a=1; b=1; #10 $display(\"%b %b |  %b    %b\", a,b,sum,carry);\n"
            '    $display("PASS: half_adder");\n'
            "    $finish;\n"
            "  end\n"
            "endmodule\n"
        ),
        "params": {},
    },
    ("combinational", "full_adder"): {
        "name": "Full Adder",
        "keywords_cn": ["全加器", "full adder", "全加"],
        "guide": "A full adder adds three 1-bit numbers (a, b, cin). Outputs Sum and Cout.",
        "verilog": (
            "module full_adder(\n"
            "  input  a, b, cin,\n"
            "  output sum, cout\n"
            ");\n"
            "  assign sum  = a ^ b ^ cin;\n"
            "  assign cout = (a & b) | (a & cin) | (b & cin);\n"
            "endmodule\n"
        ),
        "testbench": (
            "module tb;\n"
            "  reg a, b, cin;\n"
            "  wire sum, cout;\n"
            "  full_adder uut(.a(a), .b(b), .cin(cin), .sum(sum), .cout(cout));\n"
            "  initial begin\n"
            "    a=0; b=0; cin=0; #10;\n"
            "    a=0; b=1; cin=0; #10;\n"
            "    a=1; b=0; cin=0; #10;\n"
            "    a=1; b=1; cin=0; #10;\n"
            "    a=0; b=0; cin=1; #10;\n"
            "    a=0; b=1; cin=1; #10;\n"
            "    a=1; b=0; cin=1; #10;\n"
            "    a=1; b=1; cin=1; #10;\n"
            '    $display("PASS: full_adder");\n'
            "    $finish;\n"
            "  end\n"
            "endmodule\n"
        ),
        "params": {},
    },
    ("combinational", "mux_2to1"): {
        "name": "2-to-1 Multiplexer",
        "keywords_cn": ["多路选择器", "mux", "选择器", "2选1", "multiplexer"],
        "guide": "A 2-to-1 multiplexer selects between two inputs based on sel.",
        "verilog": (
            "module mux_2to1(\n"
            "  input  a, b, sel,\n"
            "  output y\n"
            ");\n"
            "  assign y = sel ? b : a;\n"
            "endmodule\n"
        ),
        "testbench": (
            "module tb;\n"
            "  reg a, b, sel;\n"
            "  wire y;\n"
            "  mux_2to1 uut(.a(a), .b(b), .sel(sel), .y(y));\n"
            "  initial begin\n"
            "    a=0; b=1; sel=0; #10;\n"
            "    sel=1; #10;\n"
            "    a=1; b=0; sel=0; #10;\n"
            "    sel=1; #10;\n"
            '    $display("PASS: mux_2to1");\n'
            "    $finish;\n"
            "  end\n"
            "endmodule\n"
        ),
        "params": {},
    },
    ("sequential", "dff"): {
        "name": "D Flip-Flop",
        "keywords_cn": ["D触发器", "dff", "d flip-flop", "触发器", "寄存器"],
        "guide": "A positive-edge-triggered D flip-flop.",
        "verilog": (
            "module dff(\n"
            "  input  d, clk,\n"
            "  output reg q\n"
            ");\n"
            "  always @(posedge clk)\n"
            "    q <= d;\n"
            "endmodule\n"
        ),
        "testbench": (
            "module tb;\n"
            "  reg d, clk;\n"
            "  wire q;\n"
            "  dff uut(.d(d), .clk(clk), .q(q));\n"
            "  always #5 clk = ~clk;\n"
            "  initial begin\n"
            "    clk=0; d=0; #12;\n"
            "    d=1; #10;\n"
            "    d=0; #10;\n"
            "    d=1; #10;\n"
            '    $display("PASS: dff");\n'
            "    $finish;\n"
            "  end\n"
            "endmodule\n"
        ),
        "params": {},
    },
    ("sequential", "counter_4bit"): {
        "name": "4-Bit Binary Counter",
        "keywords_cn": ["计数器", "counter", "4位计数器", "二进制计数器", "4bit counter"],
        "guide": "A 4-bit synchronous binary counter with enable and reset.",
        "verilog": (
            "module counter_4bit(\n"
            "  input  clk, rst, en,\n"
            "  output reg [3:0] count\n"
            ");\n"
            "  always @(posedge clk or posedge rst)\n"
            "    if (rst)\n"
            "      count <= 0;\n"
            "    else if (en)\n"
            "      count <= count + 1;\n"
            "endmodule\n"
        ),
        "testbench": (
            "module tb;\n"
            "  reg clk, rst, en;\n"
            "  wire [3:0] count;\n"
            "  counter_4bit uut(.clk(clk), .rst(rst), .en(en), .count(count));\n"
            "  always #5 clk = ~clk;\n"
            "  initial begin\n"
            "    clk=0; rst=1; en=0; #15;\n"
            "    rst=0; en=1;\n"
            "    repeat(20) #10 $display(\"count=%d\", count);\n"
            '    $display("PASS: counter_4bit");\n'
            "    $finish;\n"
            "  end\n"
            "endmodule\n"
        ),
        "params": {},
    },
}


class DigitalCircuit:
    TOOLS = [
        ("design_digital",
         "Design a digital circuit end-to-end: NL→Verilog→compile→simulate→"
         "synthesize→render.\n"
         "\n"
         "**What it does automatically:**\n"
         "1. Matches description to a digital circuit template (or LLM writes Verilog)\n"
         "2. Compiles with iverilog\n"
         "3. Runs vvp simulation — checks assertions (PASS/FAIL)\n"
         "4. Synthesizes to gate-level netlist with yosys\n"
         "5. Renders gate-level schematic as SVG\n"
         "\n"
         "**Supported templates:**\n"
         "- Combinational: half_adder, full_adder, mux_2to1\n"
         "- Sequential: dff, counter_4bit\n"
         "\n"
         "**Examples:**\n"
         "- `design_digital('half adder')`\n"
         "- `design_digital('4-bit counter')`",
         "design_digital",
         {"description": {"type": "string",
                           "description":
                           "Circuit description. Examples: 'half adder', 'full adder', "
                           "'2-to-1 mux', 'D flip-flop', '4-bit counter'"},
          "title": {"type": "string", "description": "Optional title"}},
         ["description"]),

        ("simulate_verilog",
         "Compile and simulate a Verilog module using iverilog + vvp.\n"
         "\n"
         "**Args:**\n"
         "- `verilog`: Verilog module source code\n"
         "- `testbench`: Testbench source code (optional, auto-generated if empty)\n"
         "\n"
         "**Returns:** compilation status, simulation output, assertion results.\n"
         "\n"
         "**Testbench convention:** Use `$display(\"PASS: msg\");` for passing assertions, "
         "`$display(\"FAIL: msg\");` for failures.",
         "simulate_verilog",
         {"verilog": {"type": "string",
                       "description":
                       "Verilog module source code. Include module + testbench, "
                       "or pass testbench separately."},
          "testbench": {"type": "string",
                         "description":
                         "Optional testbench. If empty, auto-generates a simple one."}},
         ["verilog"]),

        ("synthesize_gates",
         "Synthesize Verilog to gate-level netlist using yosys.\n"
         "\n"
         "**Args:**\n"
         "- `verilog`: Verilog source code\n"
         "- `top_module`: Top module name (auto-detected if empty)\n"
         "\n"
         "**Returns:** gate count, cell types, gate-level netlist.",
         "synthesize_gates",
         {"verilog": {"type": "string",
                       "description": "Verilog module source code"},
          "top_module": {"type": "string",
                          "description": "Top module name (auto-detected from source)"}},
         ["verilog"]),
    ]

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir = work_dir

    # ═══════════ design_digital: full closed-loop ═══════════

    def design_digital(self, description: str, title: str = "") -> str:
        """Full pipeline: match template → compile → simulate → synthesize → render."""
        # 1. Template matching
        try:
            tmpl = self._match_digital_template(description)
        except Exception as e:
            return f"❌ **Template matching failed:** {e}"

        circuit_name = title or tmpl.get("name", description)
        verilog = tmpl.get("verilog", "")
        testbench = tmpl.get("testbench", "")
        guide = tmpl.get("guide", "")

        parts = []
        parts.append(f"## 🔧 Digital Circuit: {circuit_name}")
        if guide:
            parts.append(f"*{guide}*")
        parts.append("")

        # 2. Compile
        parts.append("### Stage 1: Compilation (iverilog)")
        comp = compile_verilog(verilog, testbench)
        if not comp["success"]:
            parts.append("❌ **Compilation Failed**")
            for e in comp["errors"][:5]:
                parts.append(f"- {e[:200]}")
            for w in comp["warnings"][:3]:
                parts.append(f"- ⚠️ {w[:150]}")
            parts.append("")
            parts.append("**Verilog Source:**")
            parts.append(f"```verilog\n{verilog}\n```")
            return "\n".join(parts)

        parts.append("✅ Compilation passed")
        for w in comp["warnings"][:3]:
            parts.append(f"- ⚠️ {w[:150]}")
        parts.append("")

        # 3. Simulate
        parts.append("### Stage 2: Simulation (vvp)")
        has_sim = False
        if comp.get("vvp_path"):
            sim = run_simulation(comp["vvp_path"])
            has_sim = True
            if sim["success"]:
                parts.append(f"✅ Simulation passed "
                            f"({sim['assertions_passed']} assertions ok)")
            else:
                parts.append(f"❌ Simulation failed "
                            f"({sim['assertions_failed']} assertions failed)")
            for line in sim["output_lines"][:20]:
                if line.strip():
                    parts.append(f"  {line.strip()[:120]}")
            for e in sim["errors"][:3]:
                parts.append(f"- ❌ {e[:200]}")
            cleanup_vvp(comp["vvp_path"])
        parts.append("")

        # 4. Synthesize
        parts.append("### Stage 3: Synthesis (yosys)")
        top = self._extract_top(verilog)
        synth = synthesize(verilog, top)
        if synth["success"]:
            parts.append(f"✅ Synthesis complete — **{synth['gate_count']} gates**")
            if synth["cell_types"]:
                gate_list = ", ".join(f"{k}:{v}" for k, v in
                                     sorted(synth["cell_types"].items())[:8])
                parts.append(f"  Cell types: {gate_list}")
        else:
            parts.append("❌ Synthesis failed")
            for e in synth["errors"][:3]:
                parts.append(f"- {e[:200]}")
        parts.append("")

        # 5. Render gate-level SVG
        parts.append("### Stage 4: Gate-Level Schematic")
        if synth["success"] and synth["gate_netlist"]:
            try:
                dsl = _yosys_netlist_to_logic_dsl(synth["gate_netlist"])
                from ..logic_svg import LogicSVG
                lsv = LogicSVG(str(self.charts_dir.parent.parent),
                               str(self.charts_dir))
                svg_result = lsv.draw_logic(dsl, circuit_name)
                # Extract URL from draw_logic output
                url_match = re.search(r'(/charts/\S+\.svg)', svg_result)
                if url_match:
                    url = url_match.group(1)
                    parts.append(f"![{circuit_name}]({url})")
                    parts.append(url)
                else:
                    parts.append("*(Gate schematic rendered)*")
            except Exception as e:
                logger.warning(f"Gate SVG render failed: {e}")
                parts.append(f"*(Schematic unavailable: {e})*")
        parts.append("")

        # 6. Source code
        parts.append("### Verilog Source")
        parts.append(f"```verilog\n{verilog}\n```")
        if testbench:
            parts.append(f"```verilog\n{testbench}\n```")
        parts.append("")

        # 7. Hint
        parts.append("---")
        parts.append("🔄 **Iterative design:** If the circuit doesn't meet requirements:\n"
                     "1. Modify the Verilog source\n"
                     "2. Call `simulate_verilog` to verify\n"
                     "3. Call `synthesize_gates` to check gate count\n"
                     "4. Repeat until satisfied")

        return "\n".join(parts)

    # ═══════════ simulate_verilog ═══════════

    def simulate_verilog(self, verilog: str, testbench: str = "") -> str:
        """Compile and simulate Verilog. Returns structured results."""
        parts = []

        # Extract module name
        top = self._extract_top(verilog)
        parts.append(f"## Verilog Simulation: `{top}`")
        parts.append("")

        # Compile
        parts.append("### Compilation")
        comp = compile_verilog(verilog, testbench)
        if not comp["success"]:
            parts.append("❌ **Compilation Failed**")
            for e in comp["errors"][:5]:
                parts.append(f"- {e[:250]}")
            for w in comp["warnings"][:3]:
                parts.append(f"- ⚠️ {w[:150]}")
            parts.append("")
            parts.append("🔧 Fix the errors above and try again.")
            return "\n".join(parts)

        parts.append("✅ Compilation passed")
        for w in comp["warnings"][:3]:
            parts.append(f"- ⚠️ {w[:150]}")
        parts.append("")

        # Simulate
        parts.append("### Simulation Results")
        if not comp.get("vvp_path"):
            parts.append("⚠️ No simulation output (no testbench provided)")
            return "\n".join(parts)

        sim = run_simulation(comp["vvp_path"])
        cleanup_vvp(comp["vvp_path"])

        if sim["success"]:
            parts.append(f"✅ **Simulation Passed** "
                        f"({sim['assertions_passed']} assertions ok)")
        else:
            parts.append(f"❌ **Simulation Failed** "
                        f"({sim['assertions_failed']} assertions failed)")

        parts.append("")
        parts.append("| Metric | Value |")
        parts.append("|--------|-------|")
        parts.append(f"| Compilation | ✅ Passed |")
        status = "✅ Passed" if sim["success"] else "❌ Failed"
        parts.append(f"| Simulation | {status} |")
        parts.append(f"| Assertions Passed | {sim['assertions_passed']} |")
        parts.append(f"| Assertions Failed | {sim['assertions_failed']} |")
        parts.append("")

        if sim["output_lines"]:
            parts.append("### Output")
            parts.append("```")
            for line in sim["output_lines"][:30]:
                parts.append(line.strip()[:150])
            parts.append("```")

        if sim["errors"]:
            parts.append("### Errors")
            for e in sim["errors"][:5]:
                parts.append(f"- {e[:200]}")

        parts.append("")
        parts.append("---")
        parts.append("💡 **Next:** Call `synthesize_gates` to see the gate-level implementation.")

        return "\n".join(parts)

    # ═══════════ synthesize_gates ═══════════

    def synthesize_gates(self, verilog: str, top_module: str = "") -> str:
        """Synthesize Verilog to gate netlist + stats."""
        parts = []

        top = top_module or self._extract_top(verilog)
        parts.append(f"## yosys Synthesis: `{top}`")
        parts.append("")

        synth = synthesize(verilog, top)

        if not synth["success"]:
            parts.append("❌ **Synthesis Failed**")
            for e in synth["errors"][:5]:
                parts.append(f"- {e[:250]}")
            for w in synth["warnings"][:3]:
                parts.append(f"- ⚠️ {w[:150]}")
            return "\n".join(parts)

        parts.append("✅ **Synthesis Complete**")
        parts.append("")
        parts.append("| Metric | Value |")
        parts.append("|--------|-------|")
        parts.append(f"| Gate Count | **{synth['gate_count']}** |")
        for ct, count in sorted(synth["cell_types"].items()):
            parts.append(f"| {ct} | {count} |")
        parts.append("")

        if synth["gate_netlist"]:
            parts.append("### Gate-Level Netlist")
            netlist_preview = "\n".join(
                synth["gate_netlist"].strip().split("\n")[:40])
            parts.append(f"```verilog\n{netlist_preview}\n```")
            parts.append("")

        if synth["warnings"]:
            parts.append("### ⚠️ Warnings")
            for w in synth["warnings"][:5]:
                parts.append(f"- {w[:200]}")
        else:
            parts.append("### ✅ No warnings")

        return "\n".join(parts)

    # ═══════════ Templates ═══════════

    @staticmethod
    def _match_digital_template(desc: str) -> dict:
        """Match NL description to digital circuit template."""
        desc_lower = desc.lower().strip()
        matches = []
        for (cat, sub_id), tmpl in _DIGITAL_TEMPLATES.items():
            score = 0
            for word in tmpl["name"].lower().split():
                if word in desc_lower:
                    score += 1
            if sub_id.replace("_", " ") in desc_lower:
                score += 3
            for kw in tmpl.get("keywords_cn", []):
                if kw.lower() in desc_lower:
                    score += 5
            if score > 0:
                matches.append((score, cat, sub_id))
        if not matches:
            raise ValueError(
                f"No digital template matched '{desc}'. "
                f"Available: half_adder, full_adder, mux_2to1, dff, counter_4bit")
        matches.sort(reverse=True)
        cat, sub_id = matches[0][1], matches[0][2]
        return dict(_DIGITAL_TEMPLATES[(cat, sub_id)])

    @staticmethod
    def _extract_top(verilog: str) -> str:
        """Extract top module name."""
        m = re.search(r"module\s+(\w+)", verilog, re.IGNORECASE)
        return m.group(1) if m else "top"
