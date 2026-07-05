"""Shared SPICE utilities — used by analog_svg, spice_renderer, spice_simulator.

Centralizes duplicated models, subcircuit checks, and E-source fallback logic.
"""

import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

# ═══════════ Constants ═══════════

GROUND_NAMES = frozenset({"0", "gnd", "GND"})

OPAMP_SUBCKT = """\
.subckt opamp in_p in_n out vcc vss
G1 0 n1 in_p in_n 1
Rop1 n1 0 100k
Cop1 n1 0 1.59e-4
Eop1 out 0 n1 0 1
Rop2 out 0 75
.ends opamp
"""

DIODE_MODEL = ".model DEFAULT_D D (IS=1e-14 RS=1 N=1)\n"

# Real opamp models (simplified single-pole, realistic GBW/slew)
LM741_MODEL = """\
.subckt lm741 in_p in_n out vcc vss
* LM741-like: GBW≈1MHz, slew≈0.5V/µs, Aol≈200k
G1 0 n1 in_p in_n 1m
R1 n1 0 200k
C1 n1 0 79.6p
E1 out 0 n1 0 1
Rout out 0 75
.ends lm741
"""

TL081_MODEL = """\
.subckt tl081 in_p in_n out vcc vss
* TL081-like: GBW≈3MHz, slew≈13V/µs, JFET input
G1 0 n1 in_p in_n 10m
R1 n1 0 100k
C1 n1 0 53.1p
E1 out 0 n1 0 1
Rout out 0 50
.ends tl081
"""

# BJT models (NPN/PNP)
NPN_MODEL = ".model NPN NPN (IS=1e-14 BF=200 BR=5 VAF=100 IKF=0.1 ISE=1e-13 NE=1.5 NF=1 RC=10 RE=1 RB=100 CJE=2p CJC=1p TF=0.3n TR=10n)\n"
PNP_MODEL = ".model PNP PNP (IS=1e-14 BF=100 BR=3 VAF=50 IKF=0.05 ISE=1e-13 NE=1.5 NF=1 RC=10 RE=1 RB=100 CJE=2p CJC=1p TF=0.5n TR=20n)\n"

# MOSFET models (NMOS/PMOS — Level 1 Shichman-Hodges)
NMOS_MODEL = ".model NMOS NMOS (LEVEL=1 VTO=1.5 KP=200u LAMBDA=0.01 GAMMA=0.5 PHI=0.7 CGSO=100p CGDO=100p CGBO=200p)\n"
PMOS_MODEL = ".model PMOS PMOS (LEVEL=1 VTO=-1.5 KP=100u LAMBDA=0.02 GAMMA=0.5 PHI=0.7 CGSO=100p CGDO=100p CGBO=200p)\n"

# Value parsing units (shared with analog_svg templates)
_UNITS = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "μ": 1e-6,
          "m": 1e-3, "k": 1e3, "K": 1e3, "kHz": 1e3, "MHz": 1e6,
          "GHz": 1e9, "Hz": 1, "Meg": 1e6, "M": 1e6, "G": 1e9}

# ═══════════ Value helpers ═══════════


def parse_value(s: str) -> float:
    """Parse SPICE-style value string to float. '1k' → 1000, '10n' → 1e-8."""
    s = str(s).strip()
    if not s:
        return 0
    for unit, scale in sorted(_UNITS.items(), key=lambda x: -len(x[0])):
        if s.endswith(unit):
            try:
                return float(s[:-len(unit)]) * scale
            except ValueError:
                pass
    try:
        return float(s)
    except ValueError:
        return 0


def format_value(v: float) -> str:
    """Format float to compact SPICE-style string. 1590 → '1.59k', 1e-7 → '100n'."""
    if v == 0:
        return "0"
    abs_v = abs(v)
    for unit, scale in [("Meg", 1e6), ("k", 1e3), ("", 1),
                         ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12)]:
        if abs_v >= scale:
            val = v / scale
            if abs(val - round(val)) < 0.001 and abs(val) >= 10:
                return f"{int(round(val))}{unit}"
            if abs(val) >= 1:
                return f"{val:.2f}".rstrip("0").rstrip(".") + unit
            return f"{val:.3f}".rstrip("0").rstrip(".") + unit
    val = v / 1e-12
    return f"{val:.1f}".rstrip("0").rstrip(".") + "p"

# ═══════════ Ngspice detection ═══════════


def check_ngspice() -> bool:
    """Check if ngspice is available on PATH."""
    import shutil
    return shutil.which("ngspice") is not None


@lru_cache(maxsize=1)
def check_subckt_support() -> bool:
    """One-time check: does ngspice support .subckt?

    ngspice-46 Homebrew on Apple Silicon has a broken subcircuit parser.
    """
    test = ".subckt t 1 2\nR1 1 2 1k\n.ends\nX1 3 4 t\n.op\n.end\n"
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".cir", delete=False
        ) as f:
            f.write(test)
            f.flush()
            try:
                r = subprocess.run(
                    ["ngspice", "-b", f.name],
                    capture_output=True, text=True, timeout=5,
                )
                return "Mismatch" not in r.stderr and "Mismatch" not in r.stdout
            except Exception:
                return False
    except Exception:
        return False
    finally:
        try:
            Path(f.name).unlink(missing_ok=True)
        except Exception:
            pass

# ═══════════ Opamp handling ═══════════


def has_opamp(spice: str) -> bool:
    """Check if SPICE netlist contains opamp (X component) instances."""
    return bool(re.search(r'(?:^|\s)X\w+\s', spice, re.MULTILINE))


def has_diode(spice: str) -> bool:
    """Check if SPICE netlist contains diode (D component) instances."""
    return bool(re.search(r'(?:^|\s)D\d\w*\s', spice, re.MULTILINE))


def replace_opamp_with_e_source(spice: str, gain: int = 100000) -> str:
    """Replace X... opamp with inline behavioral VCVS (E-source).

    Opamp pin order: in_p in_n out vcc vss
    E-source: V(out) = gain * (V(in_p) - V(in_n))
    Ignores vcc/vss (small-signal AC analysis doesn't need power rails).
    """
    result = []
    for line in spice.split("\n"):
        tokens = line.strip().split()
        if tokens and tokens[0].upper().startswith("X") and len(tokens) >= 4:
            xname = tokens[0][1:]  # strip leading X
            in_p, in_n, out = tokens[1], tokens[2], tokens[3]
            result.append(f"E{xname} {out} 0 {in_p} {in_n} {gain}")
        else:
            result.append(line)
    return "\n".join(result)


def prepare_opamp_spice(spice: str, gain: int = 100000) -> str:
    """Prepare SPICE with opamp handling: inject model or E-source fallback."""
    if not has_opamp(spice):
        return spice
    if check_subckt_support():
        if ".subckt opamp" not in spice.lower():
            return OPAMP_SUBCKT.strip() + "\n" + spice
        return spice
    return replace_opamp_with_e_source(spice, gain)


def extract_nodes(spice: str) -> list[str]:
    """Extract unique non-ground node names from SPICE netlist."""
    nodes = set()
    for line in spice.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith(("*", "#", ".")):
            continue
        tokens = line.split()
        if len(tokens) < 3:
            continue
        ctype = tokens[0][0].upper()
        if ctype in ("R", "C", "L", "D"):
            nodes.update(tokens[1:3])
        elif ctype == "V":
            nodes.update(tokens[1:3])
        elif ctype == "X":
            nodes.update(tokens[1:-1])
    return sorted(n for n in nodes if n not in GROUND_NAMES)
