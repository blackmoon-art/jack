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

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                cwd=str(tmpdir))
        full_output = (result.stderr + result.stdout).strip()
        logger.info(f"iverilog exit={result.returncode}")

        # Parse errors and warnings
        for line in full_output.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Typical iverilog format: "file.v:5: error: ..." or "file.v:5: warning: ..."
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

        return {"success": True, "vvp_path": str(out_path),
                "errors": errors, "warnings": warnings}

    except subprocess.TimeoutExpired:
        return {"success": False, "vvp_path": None,
                "errors": ["Compilation timed out (>30s)"], "warnings": []}
    except Exception as e:
        logger.warning(f"iverilog failed: {e}")
        return {"success": False, "vvp_path": None,
                "errors": [str(e)], "warnings": []}


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
            capture_output=True, text=True, timeout=30,
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
            if "PASS" in upper:
                passed += 1
            if "FAIL" in upper or "ERROR" in upper:
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
                "errors": ["Simulation timed out (>30s)"]}
    except Exception as e:
        logger.warning(f"vvp failed: {e}")
        return {"success": False, "output_lines": [],
                "assertions_passed": 0, "assertions_failed": 0,
                "errors": [str(e)]}


def cleanup_vvp(vvp_path: str):
    """Clean up temporary vvp file."""
    p = Path(vvp_path)
    try:
        if p.exists():
            p.unlink(missing_ok=True)
    except Exception:
        pass


def _extract_top_module(verilog: str) -> str:
    """Extract the top module name from Verilog source."""
    m = re.search(r"module\s+(\w+)", verilog, re.IGNORECASE)
    return m.group(1) if m else "top"
