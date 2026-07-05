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
        "guide": "A 4-bit ripple counter. Each DFF toggles on the previous stage's output.",
        "verilog": (
            "module counter_4bit(\n"
            "  input  clk,\n"
            "  output q0, q1, q2, q3\n"
            ");\n"
            "  wire n0, n1, n2, n3;\n"
            "  assign n0 = ~q0;\n"
            "  assign n1 = ~q1;\n"
            "  assign n2 = ~q2;\n"
            "  assign n3 = ~q3;\n"
            "  dff u0(.clk(clk), .d(n0), .q(q0));\n"
            "  dff u1(.clk(q0),  .d(n1), .q(q1));\n"
            "  dff u2(.clk(q1),  .d(n2), .q(q2));\n"
            "  dff u3(.clk(q2),  .d(n3), .q(q3));\n"
            "endmodule\n"
            "\n"
            "module dff(\n"
            "  input  clk, d,\n"
            "  output reg q\n"
            ");\n"
            "  initial q = 0;\n"
            "  always @(posedge clk)\n"
            "    q <= d;\n"
            "endmodule\n"
        ),
        "testbench": (
            "module tb;\n"
            "  reg clk;\n"
            "  wire q0, q1, q2, q3;\n"
            "  counter_4bit uut(.clk(clk), .q0(q0), .q1(q1), .q2(q2), .q3(q3));\n"
            "  always #5 clk = ~clk;\n"
            "  initial begin\n"
            "    clk=0;\n"
            "    repeat(32) #10 $display(\"q=%b%b%b%b\", q3,q2,q1,q0);\n"
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

    def __init__(self, work_dir: str = "", charts_dir: str = "", llm=None):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir = work_dir
        self.llm = llm  # optional LLM for Verilog generation

    # ═══════════ design_digital: full closed-loop ═══════════

    def design_digital(self, description: str, title: str = "",
                        verilog: str = "", testbench: str = "",
                        strict_layout: bool = False,
                        force_llm: bool = False) -> str:
        """Gated closed-loop pipeline with checkpoints at each stage.

        Gates:
          Stage 2 (Sim):  LLM checks PASS/FAIL → retry Verilog on FAIL
          Stage 3 (Synth): gate_count check → retry Verilog if unreasonable
          Stage 4 (Layout): layout score check → retry with alt algorithm if <5

        If verilog is provided, skips template matching.
        """
        MAX_LAYOUT_RETRIES = 3

        # ── Guard: cap counter width to prevent simulation hang ──
        import re as _re
        _cm = _re.search(r'(\d+)\s*-?\s*bit\s*(?:binary\s*)?counter', description.lower())
        if not _cm:
            _cm = _re.search(r'(\d+)\s*位\s*(?:计数|counter)', description.lower())
        if _cm and int(_cm.group(1)) > 16:
            return (f"❌ **Counter width {_cm.group(1)}-bit exceeds maximum 16-bit.**\n"
                    f"  Please choose a smaller width to avoid simulation timeout.")

        # ═══ Stage 1: Verilog generation ═══
        if verilog.strip():
            tmpl = {"name": title or "Custom Circuit", "verilog": verilog,
                    "testbench": testbench, "guide": "User-provided Verilog"}
        elif force_llm and self.llm:
            # Meta retry: skip template, force LLM redesign
            llm_v = self._llm_generate_verilog(description)
            if llm_v:
                from .verilog_compiler import auto_testbench
                tmpl = {"name": title or description, "verilog": llm_v,
                        "testbench": auto_testbench(llm_v),
                        "guide": f"LLM redesign: {description}"}
            else:
                return "❌ **LLM redesign failed**"
        else:
            try:
                tmpl = self._match_digital_template(description)
            except Exception:
                # Template not found — try LLM free generation
                if self.llm:
                    llm_verilog = self._llm_generate_verilog(description)
                    if llm_verilog:
                        from .verilog_compiler import auto_testbench
                        auto_tb = auto_testbench(llm_verilog)
                        tmpl = {"name": title or description,
                                "verilog": llm_verilog,
                                "testbench": auto_tb,
                                "guide": f"LLM-generated: {description}"}
                    else:
                        return (f"❌ **LLM generation failed** for '{description}'.\n"
                                f"Available templates: half_adder, full_adder, mux_2to1, dff, counter_4bit")
                else:
                    return (f"❌ **Template matching failed** — no template for '{description}'.\n"
                            f"Available: half_adder, full_adder, mux_2to1, dff, counter_4bit\n"
                            f"💡 Pass raw Verilog via `verilog=` parameter.")

        circuit_name = title or tmpl.get("name", description)
        verilog = tmpl.get("verilog", "")
        testbench = tmpl.get("testbench", "")
        guide = tmpl.get("guide", "")

        parts = []
        parts.append(f"## 🔧 Digital Circuit: {circuit_name}")
        if guide:
            parts.append(f"*{guide}*")
        parts.append("")

        # ═══ Stage 2: Compile + Simulate (LLM gate) ═══
        parts.append("### Stage 1: Compilation (iverilog)")
        comp = compile_verilog(verilog, testbench)
        if not comp["success"]:
            parts.append("❌ **Compilation Failed**")
            for e in comp["errors"][:5]:
                parts.append(f"- {e[:200]}")
            parts.append("")
            parts.append("### Stage 2: Simulation — ⛔ SKIPPED (compile failed)")
            parts.append("### Stage 3: Synthesis — ⛔ SKIPPED")
            parts.append("### Stage 4: Schematic — ⛔ SKIPPED")
            parts.append("")
            parts.append("**Verilog Source (fix errors and retry):**")
            parts.append(f"```verilog\n{verilog}\n```")
            return "\n".join(parts)

        parts.append("✅ Compilation passed")
        for w in comp["warnings"][:3]:
            parts.append(f"- ⚠️ {w[:150]}")
        parts.append("")

        # ── Simulate ──
        parts.append("### Stage 2: Simulation (vvp)")
        parts.append("**🔍 LLM Gate: Check simulation PASS/FAIL**")
        has_sim = False
        sim_passed = False
        if comp.get("vvp_path"):
            sim = run_simulation(comp["vvp_path"])
            has_sim = True
            sim_passed = sim["success"]
            if sim_passed:
                parts.append(f"✅ Simulation PASSED "
                            f"({sim['assertions_passed']} assertions ok)")
            else:
                parts.append(f"❌ Simulation FAILED "
                            f"({sim['assertions_failed']} assertions failed)")
            for line in sim["output_lines"][:20]:
                if line.strip():
                    parts.append(f"  {line.strip()[:120]}")
            for e in sim["errors"][:3]:
                parts.append(f"- ❌ {e[:200]}")
            cleanup_vvp(comp["vvp_path"])

        if has_sim and not sim_passed:
            parts.append("")
            parts.append("⛔ **Gate: Simulation FAILED → Return to Stage 1**")
            parts.append("Fix the Verilog to pass all assertions, then retry.")
            parts.append("")
            parts.append("**Verilog Source:**")
            parts.append(f"```verilog\n{verilog}\n```")
            parts.append("**Testbench:**")
            parts.append(f"```verilog\n{testbench}\n```")
            return "\n".join(parts)
        parts.append("")

        # ═══ Stage 3: Synthesis (gate count gate) ═══
        parts.append("### Stage 3: Synthesis (yosys)")
        parts.append("**🔍 Gate Check: Gate count reasonable?**")
        top = self._extract_top(verilog)
        synth = synthesize(verilog, top)
        synth_passed = synth["success"]
        gate_count = synth["gate_count"] if synth_passed else 0

        if not synth_passed:
            parts.append("❌ Synthesis failed")
            for e in synth["errors"][:3]:
                parts.append(f"- {e[:200]}")
            parts.append("")
            parts.append("⛔ **Gate: Synthesis FAILED → Return to Stage 1**")
            parts.append(f"```verilog\n{verilog}\n```")
            return "\n".join(parts)

        if gate_count > 500:
            parts.append(f"⚠️ Synthesis complete — **{gate_count} gates** (too many!)")
            parts.append("⛔ **Gate: Gate count >500 → Return to Stage 1 (simplify circuit)**")
            parts.append(f"```verilog\n{verilog}\n```")
            return "\n".join(parts)
        elif gate_count == 0:
            parts.append("❌ Synthesis produced 0 gates")
            parts.append("⛔ **Gate: Zero gates → Return to Stage 1**")
            return "\n".join(parts)
        else:
            parts.append(f"✅ Synthesis complete — **{gate_count} gates** (reasonable)")
        if synth["cell_types"]:
            gate_list = ", ".join(f"{k}:{v}" for k, v in
                                 sorted(synth["cell_types"].items())[:8])
            parts.append(f"  Cell types: {gate_list}")
        parts.append("")

        # ═══ Stage 4: SVG Render + Layout (layout score gate) ═══
        layout_threshold = 7.0  # post-fix scoring: crossings+overlaps = 60% of score
        parts.append("### Stage 4: Gate-Level Schematic + Layout Analysis")
        parts.append(f"**🔍 Gate Check: Layout score ≥ {layout_threshold:.0f}? (max {MAX_LAYOUT_RETRIES} retries)**")

        layout_quality = {"score": 0.0, "crossings": 0, "overlaps": 0,
                          "issues": ["No layout rendered"], "details": []}
        svg_url = ""
        dsl = _yosys_netlist_to_logic_dsl(
            gate_netlist=synth.get("gate_netlist", ""),
            cells=synth.get("cells", []))

        from ..logic_svg import LogicSVG

        # Try different layouts if score is too low
        best_layout = None
        best_svg_url = ""

        for layout_attempt in range(MAX_LAYOUT_RETRIES):
            try:
                lsv = LogicSVG(str(self.charts_dir.parent.parent),
                               str(self.charts_dir))
                svg_result = lsv.draw_logic(dsl, circuit_name,
                                           seed=layout_attempt * 7 + 1)
                m = re.search(r'(/charts/\S+\.svg)', svg_result)
                if m:
                    svg_url = m.group(1)
                    svg_path = self.charts_dir / Path(svg_url).name
                    if svg_path.exists():
                        lq = LogicSVG.score_layout_quality(
                            svg_path.read_text(encoding="utf-8"))
                        lq_score = lq.get("score", 10.0)
                        if best_layout is None or lq_score > best_layout.get("score", 0):
                            best_layout = lq
                            best_svg_url = svg_url
                        if lq_score >= layout_threshold:
                            layout_quality = lq
                            parts.append(f"  ✅ attempt {layout_attempt+1}: score={lq_score:.0f}/10 — acceptable")
                            break
                        parts.append(f"  🔄 attempt {layout_attempt+1}: score={lq_score:.0f}/10 — retry...")
            except Exception as e:
                logger.warning(f"Layout render failed (attempt {layout_attempt+1}): {e}")

        svg_url = best_svg_url or svg_url
        layout_quality = best_layout or layout_quality
        lq_score = layout_quality.get("score", 10.0)

        if svg_url:
            parts.append(f"![{circuit_name}]({svg_url})")
            parts.append(svg_url)
        else:
            parts.append("*(Gate schematic rendered)*")

        # Score-based gate: fixed scoring (60% crossings+overlaps) means
        # score accurately reflects layout quality. Threshold at 7.0.
        final_overlaps = layout_quality.get("overlaps", 0)
        final_crossings = layout_quality.get("crossings", 0)

        if lq_score >= layout_threshold:
            parts.append(f"✅ Layout quality: {lq_score:.1f}/10 (clean)")
        elif lq_score >= max(5.0, layout_threshold - 3):
            parts.append(f"⚠️ Layout quality: {lq_score:.0f}/10 (acceptable)")
            if final_crossings:
                parts.append(f"  - {final_crossings} wire crossing(s)")
            if final_overlaps:
                parts.append(f"  - {final_overlaps} wire/gate overlap(s)")
        else:
            parts.append(f"⛔ **Gate: Layout score {lq_score:.1f}/10 < {layout_threshold:.0f} — {final_crossings} crossings, {final_overlaps} overlaps → Return to Stage 1 (redesign circuit)**")
            return "\n".join(parts)
        parts.append("")

        # ═══ Stage 5: Summary ═══
        parts.append("### 📊 Metrics Summary")
        parts.append("| Metric | Value |")
        parts.append("|--------|-------|")
        parts.append(f"| Compilation | ✅ Passed |")
        sim_status = "✅ Passed" if sim_passed else "❌ Failed"
        parts.append(f"| Simulation | {sim_status} |")
        parts.append(f"| Assertions Passed | {sim.get('assertions_passed', 0) if has_sim else 0} |")
        parts.append(f"| Assertions Failed | {sim.get('assertions_failed', 0) if has_sim else 0} |")
        parts.append(f"| Synthesis | ✅ Complete |")
        parts.append(f"| Gate Count | **{gate_count}** |")
        if synth.get("cell_types"):
            for ct, count in sorted(synth["cell_types"].items())[:8]:
                parts.append(f"| {ct} | {count} |")
        lq_label = "✅ Clean" if lq_score >= layout_threshold else ("⚠️ Fair" if lq_score >= max(5.0, layout_threshold - 3) else "❌ Poor")
        parts.append(f"| Layout Quality | {lq_label} ({lq_score:.0f}/10) |")
        if layout_quality.get("crossings", 0) > 0:
            parts.append(f"| Wire Crossings | {layout_quality['crossings']} |")
        if layout_quality.get("overlaps", 0) > 0:
            parts.append(f"| Wire/Gate Overlaps | {layout_quality['overlaps']} |")
        if layout_quality and layout_quality.get("summary"):
            parts.append("")
            parts.append(layout_quality["summary"])
        parts.append("")

        # Source code
        parts.append("### Verilog Source")
        parts.append(f"```verilog\n{verilog}\n```")
        if testbench:
            parts.append(f"```verilog\n{testbench}\n```")
        parts.append("")
        parts.append("---")
        parts.append("🔄 **Iterative design:** If the circuit doesn't meet requirements:\n"
                     "1. Modify the Verilog source\n"
                     "2. Call `simulate_verilog` to verify\n"
                     "3. Call `synthesize_gates` to check gate count\n"
                     "4. Repeat until satisfied")

        return "\n".join(parts)

    def _llm_generate_verilog(self, description: str) -> str:
        """Use LLM to generate Verilog code for a circuit description."""
        try:
            messages = [{"role": "user", "content": (
                "Generate synthesizable Verilog for this circuit. "
                "Return ONLY Verilog, no explanation.\n\n"
                f"Circuit: {description}\n\n"
                "CRITICAL RULES for clean synthesis:\n"
                "- Use structural style: small sub-modules + wire connections\n"
                "- For counters: use T-flip-flop chain, NOT 'count <= count + 1'\n"
                "- For adders: use full-adder instances, NOT 'a + b'\n"
                "- Keep modules tiny: max 3-4 gates each\n"
                "- Use 1-bit signals: NO multi-bit buses [N:0]\n"
                "- Instantiate and connect with wires\n\n"
                "Example for 2-bit counter:\n"
                "module tff(input clk,rst,t,output reg q);\n"
                "  always @(posedge clk) if(t) q<=~q;\n"
                "endmodule\n"
                "module counter(input clk,rst,output [1:0] q);\n"
                "  wire t1=q[0];\n"
                "  tff u0(.clk(clk),.rst(rst),.t(1'b1),.q(q[0]));\n"
                "  tff u1(.clk(clk),.rst(rst),.t(t1),.q(q[1]));\n"
                "endmodule\n\n"
                "Reply with ONLY Verilog code starting with `module`."
            )}]
            resp = self.llm.chat(messages=messages, tools=[], system="You are a digital circuit designer. Output ONLY Verilog code.")
            text = resp.get("text", "") if isinstance(resp, dict) else str(resp)
            # Extract module...endmodule
            import re
            m = re.search(r'(module\s+.+?endmodule)', text, re.DOTALL | re.IGNORECASE)
            return m.group(1) if m else ""
        except Exception as e:
            logger.warning(f"LLM Verilog generation failed: {e}")
            return ""

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
        """Match NL description to digital circuit template.

        For counter requests with explicit bit-width (e.g. '8-bit counter'),
        generates the appropriate ripple counter dynamically.
        """
        import re as _re
        desc_lower = desc.lower().strip()

        # Detect N-bit counter: "8-bit counter", "8bit counter", "8 bit counter"
        counter_match = _re.search(r'(\d+)\s*-?\s*bit\s*(?:binary\s*)?counter', desc_lower)
        if not counter_match:
            counter_match = _re.search(r'(\d+)\s*bit\s*(?:binary\s*)?counter', desc_lower)
        if not counter_match:
            counter_match = _re.search(r'counter.*?(\d+)\s*-?\s*bit', desc_lower)
        if not counter_match:
            # Chinese: "8位计数器"
            counter_match = _re.search(r'(\d+)\s*位\s*(?:二进制\s*)?(?:计数|counter)', desc_lower)

        if counter_match:
            n_bits = int(counter_match.group(1))
            if n_bits > 16:
                raise ValueError(
                    f"Counter width {n_bits}-bit exceeds maximum 16-bit. "
                    f"Please choose a smaller width.")
            if n_bits != 4:  # use dynamic generation for non-4-bit counters
                return DigitalCircuit._generate_ripple_counter(n_bits)

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
    def _generate_ripple_counter(n_bits: int) -> dict:
        """Generate N-bit ripple counter Verilog + testbench dynamically."""
        bit_ids = [f"q{i}" for i in range(n_bits)]
        not_ids = [f"n{i}" for i in range(n_bits)]

        verilog_lines = [
            f"module counter_{n_bits}bit(",
            "  input  clk,",
            f"  output {', '.join(bit_ids)}",
            ");",
        ]
        verilog_lines.append(f"  wire {', '.join(not_ids)};")
        for i in range(n_bits):
            verilog_lines.append(f"  assign n{i} = ~q{i};")
        verilog_lines.append(
            f"  dff u0(.clk(clk), .d(n0), .q(q0));")
        for i in range(1, n_bits):
            verilog_lines.append(
                f"  dff u{i}(.clk(q{i-1}), .d(n{i}), .q(q{i}));")
        verilog_lines.append("endmodule")
        verilog_lines.append("")
        verilog_lines.append("module dff(")
        verilog_lines.append("  input  clk, d,")
        verilog_lines.append("  output reg q")
        verilog_lines.append(");")
        verilog_lines.append("  initial q = 0;")
        verilog_lines.append("  always @(posedge clk)")
        verilog_lines.append("    q <= d;")
        verilog_lines.append("endmodule")

        tb_lines = [
            "module tb;",
            "  reg clk;",
            f"  wire {', '.join(bit_ids)};",
            f"  counter_{n_bits}bit uut(.clk(clk), "
            f"{', '.join(f'.q{i}(q{i})' for i in range(n_bits))});",
            "  always #5 clk = ~clk;",
            "  initial begin",
            "    clk=0;",
            f"    repeat({2**(n_bits+1)}) #10 "
            f"$display(\"q=%b\", {{{', '.join(reversed(bit_ids))}}});",
            f'    $display("PASS: counter_{n_bits}bit");',
            "    $finish;",
            "  end",
            "endmodule",
        ]

        return {
            "name": f"{n_bits}-Bit Ripple Counter",
            "guide": f"A {n_bits}-bit ripple counter. Each DFF toggles on the previous stage's output.",
            "verilog": "\n".join(verilog_lines),
            "testbench": "\n".join(tb_lines),
            "params": {"n_bits": n_bits},
            "dynamic": True,
        }

    @staticmethod
    def _extract_top(verilog: str) -> str:
        """Extract top module name."""
        m = re.search(r"module\s+(\w+)", verilog, re.IGNORECASE)
        return m.group(1) if m else "top"
