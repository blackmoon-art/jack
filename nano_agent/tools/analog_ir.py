"""Analog Circuit Intermediate Representation — SPICE export + validation.

Independent of rendering (analog_svg.py). Provides:
  - Component: a single circuit element
  - SimulationCommand: .ac / .tran / .dc / .op directive
  - AnalogCircuit: full circuit → valid ngspice netlist
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ═══════════ Dataclasses ═══════════

@dataclass
class Component:
    """One analog circuit element, independent of SPICE syntax.

    Nodes can be strings ("in", "out") or integers. Node 0 is always GND.
    """
    type: str            # 'R', 'C', 'L', 'D', 'V', 'I', 'Q', 'X'
    name: str            # e.g. "R1", "Q1"
    nodes: list[str]     # [n_plus, n_minus, ...] — string or int, 0=GND
    value: str = ""      # "1k", "10n", "AC 1"; empty for X
    model: str = ""      # Q: "npn"/"pnp", X: "opamp"


@dataclass
class SimulationCommand:
    """A single SPICE simulation directive."""
    sim_type: str        # "ac" | "tran" | "dc" | "op"
    params: dict = field(default_factory=dict)
    # .ac: {"sweep":"dec", "points":100, "start":"1", "stop":"1Meg"}
    # .tran: {"tstep":"1u", "tstop":"1m"}
    # .dc: {"source":"V1", "start":"0", "stop":"5", "step":"0.1"}


# ═══════════ Opamp subcircuit macro-model ═══════════

_OPAMP_SUBCKT = """* Simple 2-pole opamp macro-model (~80dB gain, ~3MHz GBW)
.subckt opamp in+ in- out vcc vee
Rin in+ in- 1Meg
E1 1 0 in+ in- 1e5
R1 1 2 1k
C1 2 0 1u
E2 3 0 2 0 1
R2 3 out 100
C2 out 0 10p
Rout out 0 1k
.ends opamp
"""

# ═══════════ AnalogCircuit ═══════════

class AnalogCircuit:
    """Complete analog circuit spec: components + simulation + probes.

    Can export to valid ngspice netlist with .control block.
    """

    def __init__(self, title: str = "",
                 components: list[Component] | None = None,
                 simulations: list[SimulationCommand] | None = None,
                 probes: list[str] | None = None):
        self.title = title
        self.components = components or []
        self.simulations = simulations or []
        self.probes = probes or []

    # ── SPICE export ──

    def to_spice(self) -> str:
        """Export a valid ngspice netlist string with .control / .endc block."""
        lines = []
        if self.title:
            lines.append(f"* {self.title}")
        lines.append("")

        # Node mapping: 0=GND, others get assigned integer IDs starting at 100
        node_map: dict[str, str] = {"0": "0", "gnd": "0", "GND": "0"}
        next_id = 100
        for c in self.components:
            for n in c.nodes:
                n_str = str(n)
                if n_str not in node_map:
                    try:
                        # already integer
                        node_map[n_str] = str(int(n_str))
                    except ValueError:
                        node_map[n_str] = str(next_id)
                        next_id += 1

        # Component lines
        has_diode = any(c.type == "D" for c in self.components)
        has_bjt = any(c.type == "Q" for c in self.components)
        has_opamp = any(c.type == "X" for c in self.components)

        for c in self.components:
            nids = " ".join(node_map[str(n)] for n in c.nodes)
            t = c.type
            if t == "R":
                lines.append(f"{c.name} {nids} {c.value or '1k'}")
            elif t == "C":
                lines.append(f"{c.name} {nids} {c.value or '1n'}")
            elif t == "L":
                lines.append(f"{c.name} {nids} {c.value or '1m'}")
            elif t == "D":
                model = c.model or "DEFAULT_D"
                lines.append(f"{c.name} {nids} {model}")
            elif t == "V":
                lines.append(f"{c.name} {nids} {c.value or 'DC 0'}")
            elif t == "I":
                lines.append(f"{c.name} {nids} {c.value or '1m'}")
            elif t == "Q":
                model = c.model or "NPN"
                lines.append(f"{c.name} {nids} {model}")
            elif t == "X":
                model = c.model or "opamp"
                lines.append(f"{c.name} {nids} {model}")

        lines.append("")

        # Model definitions
        if has_diode:
            lines.append(".model DEFAULT_D D(Is=1e-14 Rs=1 Cjo=1p)")
        if has_bjt:
            lines.append(".model NPN NPN(Is=1e-14 Bf=200 Vaf=50 Rb=100 Cjc=1p Cje=2p)")
            lines.append(".model PNP PNP(Is=1e-14 Bf=100 Vaf=50 Rb=100 Cjc=1p Cje=2p)")
        if has_opamp:
            lines.append(_OPAMP_SUBCKT)

        lines.append("")

        # Simulation commands
        for sim in self.simulations:
            p = sim.params
            if sim.sim_type == "ac":
                sweep = p.get("sweep", "dec")
                points = p.get("points", 100)
                start = p.get("start", "1")
                stop = p.get("stop", "1Meg")
                lines.append(f".ac {sweep} {points} {start} {stop}")
            elif sim.sim_type == "tran":
                tstep = p.get("tstep", "1u")
                tstop = p.get("tstop", "1m")
                lines.append(f".tran {tstep} {tstop}")
            elif sim.sim_type == "dc":
                source = p.get("source", "V1")
                start = p.get("start", "0")
                stop = p.get("stop", "5")
                step = p.get("step", "0.1")
                lines.append(f".dc {source} {start} {stop} {step}")
            elif sim.sim_type == "op":
                lines.append(".op")

        lines.append("")

        # Probes
        if self.probes:
            for pr in self.probes:
                lines.append(f".probe {pr}")

        # .control block for batch mode
        lines.append(".control")
        if self.simulations and self.simulations[0].sim_type == "op":
            lines.append("  print all")
        lines.append("  run")
        lines.append("  write sim_output.raw all")
        lines.append("  quit")
        lines.append(".endc")
        lines.append("")
        lines.append(".end")
        return "\n".join(lines)

    # ── Validation ──

    def validate(self) -> list[str]:
        """Return list of warning/error strings. Empty list = valid."""
        issues = []
        if not self.components:
            issues.append("Error: no components in circuit")
            return issues

        # Check component pin counts
        pin_counts = {"R": 2, "C": 2, "L": 2, "D": 2, "V": 2, "I": 2, "Q": 3, "X": 3}
        for c in self.components:
            expected = pin_counts.get(c.type)
            if expected and len(c.nodes) < expected:
                issues.append(
                    f"Warning: {c.name}({c.type}) has {len(c.nodes)} nodes, "
                    f"expected >= {expected}"
                )

        # Check for floating nodes (nodes that only belong to one component)
        node_refs: dict[str, int] = {}
        for c in self.components:
            for n in c.nodes:
                node_refs[str(n)] = node_refs.get(str(n), 0) + 1
        floating = [n for n, refs in node_refs.items() if refs == 1 and n != "0"]
        if floating:
            issues.append(
                f"Warning: floating nodes (only 1 connection): {', '.join(floating[:10])}"
            )

        # Check simulation source requirements
        if self.simulations:
            sim = self.simulations[0]
            if sim.sim_type == "ac":
                has_ac = any(
                    c.type == "V" and "AC" in c.value.upper()
                    for c in self.components
                )
                if not has_ac:
                    issues.append(
                        "Warning: .ac simulation but no AC voltage source found. "
                        "Add a V source with 'AC 1' value."
                    )

        return issues

    # ── Factory from SPICE netlist (reuse analog_svg.py parser pattern) ──

    @classmethod
    def from_spice_netlist(cls, text: str, title: str = "") -> "AnalogCircuit":
        """Parse SPICE netlist text into an AnalogCircuit.

        Supports R / C / L / D / V / I / Q / X components.
        Lines starting with . * # are ignored.
        """
        comps = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith((".", "*", "#")):
                continue
            parts = line.split()
            if not parts:
                continue
            prefix = parts[0][0].upper()
            name = parts[0]
            # Validate name: must be letter + alphanumeric (e.g. R1, Vin, Qout)
            if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', name):
                continue
            rest = parts[1:]

            if prefix == "R":
                comps.append(Component(type="R", name=name,
                    nodes=rest[:2], value=rest[2] if len(rest) > 2 else ""))
            elif prefix == "C":
                comps.append(Component(type="C", name=name,
                    nodes=rest[:2], value=rest[2] if len(rest) > 2 else ""))
            elif prefix == "L":
                comps.append(Component(type="L", name=name,
                    nodes=rest[:2], value=rest[2] if len(rest) > 2 else ""))
            elif prefix == "D":
                comps.append(Component(type="D", name=name,
                    nodes=rest[:2], value=rest[2] if len(rest) > 2 else ""))
            elif prefix == "V":
                comps.append(Component(type="V", name=name,
                    nodes=rest[:2], value=" ".join(rest[2:]) if len(rest) > 2 else "DC 0"))
            elif prefix == "I":
                comps.append(Component(type="I", name=name,
                    nodes=rest[:2], value=rest[2] if len(rest) > 2 else ""))
            elif prefix == "Q":
                model = rest[3] if len(rest) > 3 else "NPN"
                comps.append(Component(type="Q", name=name,
                    nodes=rest[:3], value="", model=model))
            elif prefix == "X":
                comps.append(Component(type="X", name=name,
                    nodes=rest[:5] if len(rest) >= 5 else rest, value="", model="opamp"))

        return cls(title=title, components=comps)
