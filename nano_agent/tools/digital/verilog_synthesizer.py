"""Verilog synthesizer — yosys wrapper.

Pipeline:
  Verilog → yosys synth → Gate-level Netlist + Stats

Extracts gate count, cell types, and provides structured results.
"""

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
        {"success": bool, "gate_netlist": str, "gate_count": int,
         "cell_types": {name: count}, "errors": [str], "warnings": [str]}
    """
    import shutil
    if not shutil.which("yosys"):
        return {"success": False, "gate_netlist": "",
                "gate_count": 0, "cell_types": {},
                "errors": ["yosys not installed. Install: brew install yosys"],
                "warnings": []}

    if not top_module:
        m = re.search(r"module\s+(\w+)", verilog, re.IGNORECASE)
        top_module = m.group(1) if m else "top"

    tmpdir = Path(tempfile.mkdtemp(prefix="yosys_"))
    v_path = tmpdir / "dut.v"
    netlist_path = tmpdir / "netlist.v"

    try:
        v_path.write_text(verilog.strip())

        # yosys synthesis script (simplified: synth does proc/opt/techmap internally)
        yosys_script = f"""
read_verilog {v_path}
synth -top {top_module}
write_verilog -noattr {netlist_path}
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

        # Read gate netlist
        gate_netlist = ""
        if netlist_path.exists():
            gate_netlist = netlist_path.read_text()

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
                "gate_count": gate_count, "cell_types": cell_types,
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


def _yosys_netlist_to_logic_dsl(gate_netlist: str) -> str:
    """Convert yosys gate-level Verilog to logic_svg DSL.

    yosys outputs primitives like:
      $_AND_ and1 (.A(a), .B(b), .Y(y));
      $_NOT_ not1 (.A(a), .Y(y));
    Maps them to logic_svg DSL: AND(a,b)=y
    """
    lines = []
    prim_map = {
        "$_AND_": "AND", "$_NAND_": "NAND",
        "$_OR_": "OR", "$_NOR_": "NOR",
        "$_XOR_": "XOR", "$_XNOR_": "XNOR",
        "$_NOT_": "NOT", "$_BUF_": "BUF",
        "$_DFF_P_": "DFF", "$_DFF_N_": "DFF",
        "$_MUX_": "MUX",
    }
    for line in gate_netlist.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("module") or line.startswith("endmodule"):
            continue
        if line.startswith("wire") or line.startswith("input") or line.startswith("output"):
            continue
        # Match: $_AND_ name (.A(a), .B(b), .Y(y));
        m = re.match(r"(\$\w+)\s+\w+\s+\((.+)\)\s*;", line)
        if not m:
            continue
        prim = m.group(1)
        ports_str = m.group(2)
        gate_type = prim_map.get(prim)
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
    return "\n".join(lines)
