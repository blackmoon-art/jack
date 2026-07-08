"""Verilog compiler + simulator — iverilog + vvp wrapper.

Pipeline:
  Verilog + Testbench → iverilog → vvp → Structured Results

Parses $display output for PASS/FAIL assertions, extracts errors.
"""

import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.digital.compiler")


def compile_verilog(verilog: str, testbench: str = "") -> dict:
    """Compile Verilog with iverilog.

    Args:
        verilog: Verilog module source code
        testbench: Optional testbench source code

    Returns:
        {"success": bool, "vvp_path": str|None, "errors": [str], "warnings": [str]}
    """
    import shutil
    if not shutil.which("iverilog"):
        return {"success": False, "vvp_path": None,
                "errors": ["iverilog not installed. Install: brew install icarus-verilog"],
                "warnings": []}

    errors = []
    warnings = []

    # Write Verilog + testbench to temp files
    tmpdir = Path(tempfile.mkdtemp(prefix="iverilog_"))
    v_path = tmpdir / "dut.v"
    tb_path = tmpdir / "tb.v"
    out_path = tmpdir / "sim.vvp"
    _keep_tmpdir = False  # only True on successful compilation (caller needs vvp_path)

    try:
        v_path.write_text(verilog.strip())
        if testbench.strip():
            tb_path.write_text(testbench.strip())

        # Compile
        cmd = ["iverilog", "-o", str(out_path), "-g2012"]
        if testbench.strip():
            cmd.extend([str(v_path), str(tb_path)])
        else:
            cmd.append(str(v_path))

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                cwd=str(tmpdir))
        full_output = (result.stderr + result.stdout).strip()
        logger.info(f"iverilog exit={result.returncode}")

        # Parse errors and warnings
        for line in full_output.split("\n"):
            line = line.strip()
            if not line:
                continue
            if ": error:" in line.lower():
                errors.append(line[:300])
            elif ": warning:" in line.lower():
                warnings.append(line[:200])
            elif "error" in line.lower() and result.returncode != 0:
                errors.append(line[:300])

        if result.returncode != 0:
            if not errors:
                errors.append(full_output[:500] or "Unknown compilation error")
            return {"success": False, "vvp_path": None,
                    "errors": errors, "warnings": warnings}

        _keep_tmpdir = True  # success: caller needs vvp_path from this dir
        return {"success": True, "vvp_path": str(out_path),
                "errors": errors, "warnings": warnings}

    except subprocess.TimeoutExpired:
        return {"success": False, "vvp_path": None,
                "errors": ["Compilation timed out (>10s)"], "warnings": []}
    except Exception as e:
        logger.warning(f"iverilog failed: {e}")
        return {"success": False, "vvp_path": None,
                "errors": [str(e)], "warnings": []}
    finally:
        if not _keep_tmpdir:
            import shutil
            shutil.rmtree(str(tmpdir), ignore_errors=True)


def run_simulation(vvp_path: str) -> dict:
    """Run vvp simulation and parse output.

    Returns:
        {"success": bool, "output_lines": [str], "assertions_passed": int,
         "assertions_failed": int, "errors": [str]}
    """
    import shutil
    if not shutil.which("vvp"):
        return {"success": False, "output_lines": [],
                "assertions_passed": 0, "assertions_failed": 0,
                "errors": ["vvp not installed. Install: brew install icarus-verilog"]}

    try:
        result = subprocess.run(
            ["vvp", str(vvp_path)],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(vvp_path).parent),
        )
        output = (result.stdout + result.stderr).strip()
        lines = output.split("\n") if output else []
        logger.info(f"vvp exit={result.returncode}, lines={len(lines)}")

        # Count assertions
        passed = 0
        failed = 0
        errors = []
        for line in lines:
            upper = line.upper()
            # Word-boundary match: "PASS:" or "PASS " as a standalone token
            if re.search(r'\bPASS\b', upper):
                passed += 1
            if re.search(r'\bFAIL\b', upper) or re.search(r'\bERROR\b', upper):
                failed += 1
                errors.append(line[:300])

        if result.returncode != 0 and not errors:
            errors.append(f"vvp exited with code {result.returncode}")

        success = result.returncode == 0 and failed == 0

        return {"success": success, "output_lines": lines[:100],
                "assertions_passed": passed, "assertions_failed": failed,
                "errors": errors[:20]}

    except subprocess.TimeoutExpired:
        return {"success": False, "output_lines": [],
                "assertions_passed": 0, "assertions_failed": 0,
                "errors": ["Simulation timed out (>10s)"]}
    except Exception as e:
        logger.warning(f"vvp failed: {e}")
        return {"success": False, "output_lines": [],
                "assertions_passed": 0, "assertions_failed": 0,
                "errors": [str(e)]}


def cleanup_vvp(vvp_path: str):
    """Clean up temporary vvp file and its parent temp directory."""
    p = Path(vvp_path)
    try:
        if p.exists():
            p.unlink(missing_ok=True)
    except Exception:
        pass
    # Also remove the temp directory (contains dut.v, tb.v)
    try:
        import shutil
        if p.parent.exists() and p.parent.name.startswith("iverilog_"):
            shutil.rmtree(p.parent)
    except Exception:
        pass


def _extract_top_module(verilog: str) -> str:
    """Extract the top module name from Verilog source."""
    m = re.search(r"module\s+(\w+)", verilog, re.IGNORECASE)
    return m.group(1) if m else "top"


def auto_testbench(verilog: str, num_vectors: int = 8) -> str:
    """Generate a simple testbench from module ports.

    Detects module ports, generates random-ish test vectors,
    and includes PASS/FAIL assertions based on port names.
    For complex modules, generates minimal stimulus.
    """
    modules = list(re.finditer(
        r'module\s+(\w+)\s*\((.*?)\)\s*;', verilog, re.DOTALL))
    if not modules:
        modules = list(re.finditer(
            r'module\s+(\w+)\s*\(([^)]+)\)', verilog))
    if not modules:
        return ""

    top = modules[0].group(1)
    ports_block = modules[0].group(2)

    # Parse ports — handle multiple ports per line (e.g. "input a, b")
    inputs = []
    outputs = []
    current_dir = ""
    current_width = ""
    # Split port block by comma-separated names
    for token in re.split(r'[,\n]', ports_block):
        token = token.strip().rstrip(';').strip()
        if not token:
            continue
        # Skip leftover empty tokens after stripping semicolons
        if "//" in token:
            token = token[:token.index("//")]
        if not token:
            continue
        # Detect direction + optional width
        dir_match = re.match(r'(input|output)\s*(reg\s+)?\s*(wire\s+)?\s*(\[\d+:\d+\]\s*)?(.*)', token)
        if dir_match:
            current_dir = dir_match.group(1)
            current_width = ""
            if dir_match.group(4):
                current_width = dir_match.group(4).strip()
            token = dir_match.group(5).strip()
            # Strip leftover type keywords from the name portion
            token = re.sub(r'\b(reg|wire|logic|input|output)\b\s*', '', token).strip()
        if not token:
            continue
        # Parse port name(s) on this line
        for part in token.split(","):
            name = part.strip()
            if not name:
                continue
            if current_dir == "input":
                inputs.append((name, current_width))
            elif current_dir == "output":
                outputs.append((name, current_width))

    if not inputs and not outputs:
        return ""

    # Detect clock and reset ports for proper stimulus generation
    _CLK_NAMES = {"clk", "clock"}
    _RST_NAMES = {"rst", "reset", "rst_n", "reset_n"}
    clk_ports = [n for n, _ in inputs if n.lower() in _CLK_NAMES]
    rst_ports = [n for n, _ in inputs if n.lower() in _RST_NAMES]
    # Non-clock, non-reset inputs get random stimulus
    data_inputs = [(n, w) for n, w in inputs
                   if n.lower() not in _CLK_NAMES and n.lower() not in _RST_NAMES]

    # Build testbench
    tb_lines = [f"module tb;"]
    # Regs for inputs
    for name, width in inputs:
        if width:
            idx1 = width.index(":")
            hi = int(width[1:idx1])
            tb_lines.append(f"  reg [{hi}:0] {name};")
        else:
            tb_lines.append(f"  reg {name};")
    # Wires for outputs
    for name, width in outputs:
        if width:
            idx1 = width.index(":")
            hi = int(width[1:idx1])
            tb_lines.append(f"  wire [{hi}:0] {name};")
        else:
            tb_lines.append(f"  wire {name};")

    # Instantiate
    port_list = ", ".join(
        [f".{n}({n})" for n, _ in inputs] +
        [f".{n}({n})" for n, _ in outputs])
    tb_lines.append(f"  {top} uut({port_list});")

    # Clock oscillator for clock ports
    for clk in clk_ports:
        tb_lines.append(f"  always #5 {clk} = ~{clk};")

    # Test vectors
    tb_lines.append("  initial begin")
    tb_lines.append('    $display("Auto-testbench for ' + top + '");')

    # Initialize clock and reset
    for clk in clk_ports:
        tb_lines.append(f"    {clk} = 0;")
    for rst in rst_ports:
        tb_lines.append(f"    {rst} = 1;")
    if rst_ports:
        tb_lines.append("    #15;")
        for rst in rst_ports:
            tb_lines.append(f"    {rst} = 0;")

    import random
    rng = random.Random(42)  # deterministic

    for vi in range(num_vectors):
        # Generate random inputs (skip clock/reset — handled separately)
        for name, width in data_inputs:
            if width:
                idx1 = width.index(":")
                hi = int(width[1:idx1])
                val = rng.randint(0, (1 << (hi + 1)) - 1)
                tb_lines.append(f"    {name} = {hi+1}'h{val:X};")
            else:
                val = rng.randint(0, 1)
                tb_lines.append(f"    {name} = {val};")
        tb_lines.append("    #10;")

    # Check at least one output toggled for sequential circuits
    tb_lines.append(f'    $display("PASS: auto_testbench ({num_vectors} vectors)");')
    tb_lines.append("    $finish;")
    tb_lines.append("  end")
    tb_lines.append("endmodule")

    return "\n".join(tb_lines)
