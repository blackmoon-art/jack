"""Verilog synthesizer — yosys wrapper.

Pipeline:
  Verilog → yosys synth → Gate-level Netlist + Stats

Extracts gate count, cell types, and provides structured results.
"""

import json as _json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.digital.synthesizer")


def synthesize(verilog: str, top_module: str = "") -> dict:
    """Synthesize Verilog to gate-level netlist using yosys.

    Args:
        verilog: Verilog module source
        top_module: Top module name (auto-detected if empty)

    Returns:
        {"success": bool, "gate_netlist": str, "cells": [dict], "gate_count": int,
         "cell_types": {name: count}, "errors": [str], "warnings": [str]}
    """
    import shutil
    if not shutil.which("yosys"):
        return {"success": False, "gate_netlist": "",
                "cells": [], "gate_count": 0, "cell_types": {},
                "errors": ["yosys not installed. Install: brew install yosys"],
                "warnings": []}

    if not top_module:
        m = re.search(r"module\s+(\w+)", verilog, re.IGNORECASE)
        top_module = m.group(1) if m else "top"

    tmpdir = Path(tempfile.mkdtemp(prefix="yosys_"))
    v_path = tmpdir / "dut.v"
    json_path = tmpdir / "netlist.json"
    verilog_path = tmpdir / "netlist.v"

    try:
        v_path.write_text(verilog.strip())

        # yosys synthesis: synth → simplemap (force gate-level) → write_json → stat
        yosys_script = f"""
read_verilog {v_path}
proc; flatten; opt
synth -top {top_module}
simplemap
write_json {json_path}
write_verilog -noattr {verilog_path}
stat -top {top_module}
"""
        result = subprocess.run(
            ["yosys", "-p", yosys_script.strip()],
            capture_output=True, text=True, timeout=60,
            cwd=str(tmpdir),
        )
        output = (result.stderr + result.stdout)
        logger.info(f"yosys exit={result.returncode}")

        # Parse errors
        errors = []
        warnings = []
        for line in output.split("\n"):
            stripped = line.strip()
            if "ERROR" in stripped.upper():
                errors.append(stripped[:300])
            elif "Warning:" in stripped:
                warnings.append(stripped[:200])

        if result.returncode != 0 and not errors:
            errors.append("yosys synthesis failed")

        # Read gate netlist (Verilog for reference)
        gate_netlist = ""
        if verilog_path.exists():
            gate_netlist = verilog_path.read_text()

        # Read JSON netlist with actual cell instances
        cells = []
        if json_path.exists():
            try:
                jdata = _json.loads(json_path.read_text())
                mod = jdata.get("modules", {}).get(top_module, {})
                # Build bit_id → wire name from ports (preserve multi-bit indices)
                bit_to_wire = {}
                ports = mod.get("ports", {})
                for port_name, port_info in ports.items():
                    bits = sorted(port_info.get("bits", []))
                    if len(bits) == 1:
                        bit_to_wire[bits[0]] = port_name
                    else:
                        for i, bit in enumerate(bits):
                            bit_to_wire[bit] = f"{port_name}[{i}]"
                for cell_name, cell_info in sorted(
                        mod.get("cells", {}).items(),
                        key=lambda kv: kv[0]):
                    ctype = cell_info.get("type", "")
                    if not ctype.startswith("$_"):
                        continue  # skip non-primitives
                    conns = cell_info.get("connections", {})
                    cells.append({
                        "type": ctype,
                        "name": cell_name,
                        "connections": {
                            port: [bit_to_wire.get(b, f"n{b}") for b in bits]
                            for port, bits in conns.items()
                        }
                    })
            except Exception as e:
                logger.warning(f"Failed to parse JSON netlist: {e}")

        # Parse stats: "X cells" after "=== module ===" header
        gate_count = 0
        cell_types = {}
        in_stat_block = False
        for line in output.split("\n"):
            stripped = line.strip()
            # New yosys format: "=== top === ... X cells"
            if stripped.startswith("===") and stripped.endswith("==="):
                in_stat_block = True
                continue
            if in_stat_block:
                m = re.match(r"(\d+)\s+cells?", stripped)
                if m:
                    gate_count = max(gate_count, int(m.group(1)))
                    in_stat_block = False
            # Old yosys format: "Number of cells: N"
            m = re.search(r"Number of cells:\s*(\d+)", line)
            if m:
                gate_count = int(m.group(1))
            # Parse individual cell counts: "$_AND_   5"
            m2 = re.match(r"\s+(\$\w+)\s+(\d+)", line)
            if m2:
                cell_types[m2.group(1)] = int(m2.group(2))

        success = result.returncode == 0 and gate_count > 0

        return {"success": success, "gate_netlist": gate_netlist,
                "cells": cells, "gate_count": gate_count,
                "cell_types": cell_types,
                "errors": errors[:10], "warnings": warnings[:10]}

    except subprocess.TimeoutExpired:
        return {"success": False, "gate_netlist": "",
                "gate_count": 0, "cell_types": {},
                "errors": ["Synthesis timed out (>60s)"], "warnings": []}
    except Exception as e:
        logger.warning(f"yosys failed: {e}")
        return {"success": False, "gate_netlist": "",
                "gate_count": 0, "cell_types": {},
                "errors": [str(e)], "warnings": []}


def _yosys_netlist_to_logic_dsl(gate_netlist: str = "", cells: list | None = None,
                               decompose: bool = False) -> str:
    """Convert yosys gate-level output to logic_svg DSL.

    Accepts either:
    - Verilog text (legacy, may contain assign statements instead of gate primitives)
    - JSON cell list from synthesize() (preferred: preserves cell types)

    yosys primitives:
      $_AND_, $_NAND_, $_OR_, $_NOR_, $_XOR_, $_XNOR_, $_NOT_, $_BUF_,
      $_DFF_P_, $_DFF_N_, $_MUX_

    Maps them to logic_svg DSL: AND(a,b)=y
    """
    PRIM_MAP = {
        "$_AND_": "AND", "$_NAND_": "NAND",
        "$_OR_": "OR", "$_NOR_": "NOR",
        "$_XOR_": "XOR", "$_XNOR_": "XNOR",
        "$_NOT_": "NOT", "$_BUF_": "BUF",
        "$_DFF_P_": "DFF", "$_DFF_N_": "DFF",
        "$_DFF_PP0_": "DFF", "$_DFF_PP1_": "DFF",
        "$_DFF_PN0_": "DFF", "$_DFF_PN1_": "DFF",
        "$_DFF_NP0_": "DFF", "$_DFF_NP1_": "DFF",
        "$_DFF_NN0_": "DFF", "$_DFF_NN1_": "DFF",
        "$_DFFE_PP0P_": "DFF", "$_DFFE_PP1P_": "DFF",
        "$_DFFE_PN0P_": "DFF", "$_DFFE_PN1P_": "DFF",
        "$_DFFE_NP0P_": "DFF", "$_DFFE_NP1P_": "DFF",
        "$_DFFE_NN0P_": "DFF", "$_DFFE_NN1P_": "DFF",
        "$_DFFSR_PPP_": "DFF", "$_DFFSR_NNN_": "DFF",
        "$_SDFF_PP0_": "DFF", "$_SDFF_PP1_": "DFF",
        "$_SDFFE_PP0P_": "DFF", "$_SDFFE_PP1P_": "DFF",
        "$_MUX_": "MUX", "$_MUX16_": "MUX",
        "$_AOI3_": "AOI", "$_OAI3_": "OAI",
        "$_TBUF_": "BUF", "$_DLATCH_P_": "DFF",
    }

    # Preferred: parse from JSON cells
    if cells:
        lines = []
        for cell in cells:
            ctype = cell["type"]
            gate_type = PRIM_MAP.get(ctype)
            if not gate_type:
                continue
            conns = cell.get("connections", {})
            # Output port: Y for combinational, Q for DFF
            out_ports = [b for p in ("Y", "Q") for b in conns.get(p, [])]
            if not out_ports:
                continue
            output = out_ports[0]
            # Input ports: A, B, C(clk), D(data), S(select), E(enable), R(reset)
            inputs = []
            for p in ("A", "B", "C", "D", "S", "E", "R"):
                if p in conns and p not in ("Y", "Q"):
                    inputs.extend(conns[p])
            # Deduplicate while preserving order
            seen = set()
            inputs = [x for x in inputs if not (x in seen or seen.add(x))]
            if inputs:
                lines.append(f"{gate_type}({','.join(inputs)}) = {output}")
        if lines:
            if decompose:
                lines = _decompose_to_and_or_not(lines)
            return "\n".join(lines)

    # Fallback: parse from Verilog text (legacy)
    if gate_netlist:
        lines = []
        for line in gate_netlist.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("module") or line.startswith("endmodule"):
                continue
            if line.startswith("wire") or line.startswith("input") or line.startswith("output"):
                continue
            if line.startswith("assign"):
                continue  # assign statements = yosys didn't map to gates
            # Match: $_AND_ name (.A(a), .B(b), .Y(y));
            m = re.match(r"(\$\w+)\s+\w+\s+\((.+)\)\s*;", line)
            if not m:
                continue
            prim = m.group(1)
            ports_str = m.group(2)
            gate_type = PRIM_MAP.get(prim)
            if not gate_type:
                continue
            # Parse port connections: .A(a), .B(b), .Y(y)
            ports = {}
            for pm in re.finditer(r"\.(\w+)\((\w+)\)", ports_str):
                ports[pm.group(1)] = pm.group(2)
            # Map to logic_svg: gate_type(inputs) = output
            output = ports.get("Y") or ports.get("Q")
            inputs = []
            for p in ("A", "B", "C", "D", "S", "D", "CLK", "R"):
                if p in ports and p not in ("Y", "Q"):
                    inputs.append(ports[p])
            if output and inputs:
                lines.append(f"{gate_type}({','.join(inputs)}) = {output}")
        if lines:
            if decompose:
                lines = _decompose_to_and_or_not(lines)
            return "\n".join(lines)

    return ""


def _decompose_to_and_or_not(lines: list) -> list:
    """Decompose NAND/NOR/XOR/XNOR/MUX to AND/OR/NOT primitives.

    Each complex gate is replaced with its canonical decomposition
    using only AND, OR, NOT gates. Intermediate wires are named
    with _d prefix.
    """
    decomposed = []
    counter = [0]  # mutable counter for unique wire names

    def new_wire():
        counter[0] += 1
        return f"_d{counter[0]}"

    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r'(\w+)\(([^)]+)\)\s*=\s*(\S+)', line)
        if not m:
            decomposed.append(line)
            continue

        gate, inputs_str, output = m.group(1).upper(), m.group(2), m.group(3)
        inputs = [x.strip() for x in inputs_str.split(",")]

        if gate in ("AND", "OR", "NOT", "BUF", "DFF"):
            decomposed.append(line)
        elif gate == "NAND":
            # NAND(a,b) = NOT(AND(a,b))
            w = new_wire()
            decomposed.append(f"AND({','.join(inputs)}) = {w}")
            decomposed.append(f"NOT({w}) = {output}")
        elif gate == "NOR":
            # NOR(a,b) = NOT(OR(a,b))
            w = new_wire()
            decomposed.append(f"OR({','.join(inputs)}) = {w}")
            decomposed.append(f"NOT({w}) = {output}")
        elif gate == "XOR":
            # XOR(a,b) = OR(AND(a,NOT(b)), AND(NOT(a),b))
            a, b = inputs[0], inputs[1]
            w1, w2, w3, w4 = new_wire(), new_wire(), new_wire(), new_wire()
            decomposed.append(f"NOT({a}) = {w1}")
            decomposed.append(f"NOT({b}) = {w2}")
            decomposed.append(f"AND({a},{w2}) = {w3}")
            decomposed.append(f"AND({w1},{b}) = {w4}")
            decomposed.append(f"OR({w3},{w4}) = {output}")
        elif gate == "XNOR":
            # XNOR(a,b) = NOT(XOR(a,b)) → 5 gates
            a, b = inputs[0], inputs[1]
            w1, w2, w3, w4, w5 = new_wire(), new_wire(), new_wire(), new_wire(), new_wire()
            decomposed.append(f"NOT({a}) = {w1}")
            decomposed.append(f"NOT({b}) = {w2}")
            decomposed.append(f"AND({a},{w2}) = {w3}")
            decomposed.append(f"AND({w1},{b}) = {w4}")
            decomposed.append(f"OR({w3},{w4}) = {w5}")
            decomposed.append(f"NOT({w5}) = {output}")
        elif gate == "MUX":
            # MUX(a,b,s) = OR(AND(a,NOT(s)), AND(b,s))
            if len(inputs) < 3:
                decomposed.append(line)
                continue
            a, b, s = inputs[0], inputs[1], inputs[2]
            w1, w2, w3 = new_wire(), new_wire(), new_wire()
            decomposed.append(f"NOT({s}) = {w1}")
            decomposed.append(f"AND({a},{w1}) = {w2}")
            decomposed.append(f"AND({b},{s}) = {w3}")
            decomposed.append(f"OR({w2},{w3}) = {output}")
        else:
            decomposed.append(line)

    return decomposed
