"""SPICE → Schemdraw 渲染编译器。

Pipeline:
  SPICE Netlist → Connectivity Graph → Layout Engine → Schemdraw DSL → SVG

Zero-dependency fallback: if schemdraw is not installed, falls back to pure-Python
SVG renderer from analog_svg.
"""

import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.spice_renderer")

# ═══════════════════════════════════════════════════════════════
# Stage 1: SPICE → Connectivity Graph
# ═══════════════════════════════════════════════════════════════

def _build_graph(spice_text: str) -> dict:
    """Parse SPICE netlist into connectivity graph.

    Returns:
        {
            "components": [Component, ...],
            "nets": {"net_name": Net, ...},
            "sources": [comp_idx, ...],    # AC source indices
            "ground_nets": {"0", "gnd", "GND"},
        }

    Each Component:
        {"type": "R", "name": "R1", "pins": [("in", "L"), ("out", "R")],
         "value": "1k", "model": ""}

    Each Net:
        {"name": "in", "connections": [(comp_idx, pin_idx), ...], "is_ground": bool}
    """
    _SKIP = (
        ".model", ".subckt", ".ends", ".op", ".ac", ".tran", ".dc",
        ".end", ".probe", ".plot", ".print", ".options", ".temp",
        ".include", ".lib", ".param", ".func", ".global", ".ic",
        ".nodeset", ".save", ".measure", ".alter",
    )

    # ── Pin side definitions ──
    _PIN_SIDES = {
        "R": ["L", "R"],
        "C": ["L", "R"],
        "L": ["L", "R"],
        "D": ["L", "R"],
        "V": ["L", "R"],      # V+: left, V-: right
        "X": ["L", "L", "R", "T", "B"],  # in-, in+, out, vcc, vss
    }

    components = []
    nets = {}
    sources = []
    ground_nets = {"0", "gnd", "GND"}

    for line in spice_text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("*") or line.startswith("#"):
            continue
        if line.lower().startswith(_SKIP):
            continue

        tokens = line.split()
        if len(tokens) < 2:
            continue

        first = tokens[0]
        ctype = first[0].upper()
        cname = first

        if ctype not in ("R", "C", "L", "D", "V", "X"):
            continue

        # Parse by component type
        if ctype in ("R", "C", "L"):
            if len(tokens) < 4:
                continue
            raw_nodes = tokens[1:3]
            value = tokens[3]
            model = ""
        elif ctype == "D":
            raw_nodes = tokens[1:3]
            value = ""
            model = ""
        elif ctype == "V":
            if len(tokens) < 4:
                continue
            raw_nodes = tokens[1:3]
            value = " ".join(tokens[3:])
            model = ""
        elif ctype == "X":
            if len(tokens) < 3:
                continue
            raw_nodes = tokens[1:-1]
            model = tokens[-1]
            value = ""

        # Assign pin sides
        sides = _PIN_SIDES.get(ctype, ["L", "R"])
        # Pad sides if component has more pins than defined sides
        while len(sides) < len(raw_nodes):
            sides.append("R")
        pins = list(zip(raw_nodes, sides[:len(raw_nodes)]))

        comp_idx = len(components)
        comp = {
            "type": ctype,
            "name": cname,
            "pins": pins,
            "value": value,
            "model": model,
        }
        components.append(comp)

        # Track AC sources
        if ctype == "V" and "AC" in value.upper():
            sources.append(comp_idx)

        # Build net connections
        for pin_idx, (net_name, _) in enumerate(pins):
            if net_name not in nets:
                nets[net_name] = {
                    "name": net_name,
                    "connections": [],
                    "is_ground": net_name in ground_nets,
                }
            nets[net_name]["connections"].append((comp_idx, pin_idx))

    return {
        "components": components,
        "nets": nets,
        "sources": sources,
        "ground_nets": ground_nets,
    }


# ═══════════════════════════════════════════════════════════════
# Stage 2: Layout Engine
# ═══════════════════════════════════════════════════════════════

def _layout(graph: dict) -> dict:
    """BFS-based layout: determine component positions and branch structure.

    Returns:
        {
            "main_chain": [comp_idx, ...],      # ordered left→right
            "branches": [(parent_col, comp_idx, direction), ...],
            "feedback": [(from_col, comp_idx), ...],
            "terminals": {"input": [net_name], "output": [net_name]},
        }
    """
    comps = graph["components"]
    nets = graph["nets"]
    sources = graph["sources"]
    ground_nets = graph["ground_nets"]

    if not comps:
        return {"main_chain": [], "branches": [], "feedback": [], "terminals": {}}

    # ── Find signal sources and starting nets ──
    start_nets = []
    terminal_outputs = set()
    if sources:
        for si in sources:
            src = comps[si]
            # source pin0 (+) is the signal output net
            if len(src["pins"]) >= 1:
                net_name = src["pins"][0][0]
                if net_name not in ground_nets:
                    start_nets.append(net_name)
    if not start_nets:
        # No AC source: pick any non-ground net
        for name in nets:
            if name not in ground_nets:
                start_nets.append(name)
                break

    # ── BFS to find main signal chain ──
    visited_comps = set(sources)  # sources are already placed at col 0
    visited_nets = set(ground_nets)
    main_chain = list(sources)  # sources first

    # BFS queue: (net_name, parent_col, direction)
    from collections import deque
    queue = deque()

    for sn in start_nets:
        if sn not in visited_nets:
            visited_nets.add(sn)
            queue.append((sn, len(main_chain) - 1 if main_chain else 0))

    branches = []   # (parent_col, comp_idx, direction)
    feedback = []   # (from_col, comp_idx)
    col_counter = len(main_chain)

    while queue:
        net_name, parent_col = queue.popleft()
        if net_name not in nets:
            continue

        for comp_idx, pin_idx in nets[net_name]["connections"]:
            if comp_idx in visited_comps:
                continue

            comp = comps[comp_idx]
            ctype = comp["type"]

            # Determine if this is a main-chain or branch component
            # Branch: has a pin connected to GND (R/C/L to GND)
            # Main chain: signal passes through
            is_branch = False
            if ctype in ("R", "C", "L"):
                # Check if other pin is GND
                for pidx, (pn, _) in enumerate(comp["pins"]):
                    if pidx != pin_idx and pn in ground_nets:
                        is_branch = True
                        break

            if is_branch:
                branches.append((parent_col, comp_idx, "down"))
                visited_comps.add(comp_idx)
                # Mark the other pin as visited (it's GND)
                for pn, _ in comp["pins"]:
                    if pn in ground_nets:
                        visited_nets.add(pn)
            else:
                # Main chain component
                main_chain.append(comp_idx)
                visited_comps.add(comp_idx)
                col_counter += 1

                # Queue other non-GND pins as next nets
                for pidx, (pn, _) in enumerate(comp["pins"]):
                    if pidx != pin_idx and pn not in visited_nets:
                        visited_nets.add(pn)
                        queue.append((pn, col_counter - 1))

    # ── Add unvisited components as branches at the end ──
    for ci, comp in enumerate(comps):
        if ci not in visited_comps:
            branches.append((max(0, len(main_chain) - 1), ci, "down"))
            visited_comps.add(ci)

    # ── Identify terminals ──
    # Input terminals: nets that only appear as source inputs
    all_pin_nets = set()
    output_nets = set()
    for comp in comps:
        for pn, side in comp["pins"]:
            all_pin_nets.add(pn)
            if side == "R":
                output_nets.add(pn)

    input_nets = []
    for sn in start_nets:
        if sn in nets and sn not in ground_nets:
            input_nets.append(sn)

    # Output terminals: nets from "R" pins of last main-chain components
    # that don't go anywhere else
    output_terminals = []
    for net_name, net_info in nets.items():
        if net_name in ground_nets:
            continue
        # A net is an output if all its connections are "receiving" (pin side Right)
        all_receiving = True
        for ci, pi in net_info["connections"]:
            comp = comps[ci]
            if pi < len(comp["pins"]) and comp["pins"][pi][1] == "L":
                all_receiving = False
                break
        if all_receiving and len(net_info["connections"]) == 1:
            output_terminals.append(net_name)

    # Fallback: last component's right-side pin nets
    if not output_terminals and main_chain:
        last = comps[main_chain[-1]]
        for pn, side in last["pins"]:
            if side == "R" and pn not in ground_nets:
                output_terminals.append(pn)

    return {
        "main_chain": main_chain,
        "branches": branches,
        "feedback": feedback,
        "terminals": {"input": input_nets, "output": output_terminals},
    }


# ═══════════════════════════════════════════════════════════════
# Stage 3: Schemdraw Codegen
# ═══════════════════════════════════════════════════════════════

# SPICE type → schemdraw element constructor
_ELEMENT_MAP = {
    "R": ("Resistor", "IEC"),
    "C": ("Capacitor", ""),
    "L": ("Inductor2", ""),
    "D": ("Diode", ""),
    "V": None,   # handled separately (AC vs DC)
    "X": ("Opamp", ""),
}


def _make_element(comp: dict):
    """Create a schemdraw element for a SPICE component."""
    import schemdraw.elements as elm

    ctype = comp["type"]
    value = comp.get("value", "")

    if ctype == "V":
        val_upper = value.upper()
        if "SIN" in val_upper or "AC" in val_upper:
            el = elm.SourceSin()
        elif "DC" in val_upper:
            el = elm.Battery()
        elif "PULSE" in val_upper:
            el = elm.SourcePulse()
        else:
            el = elm.SourceV()
    elif ctype == "X":
        el = elm.Opamp()
    elif ctype == "D":
        el = elm.Diode()
    else:
        cls_name, style = _ELEMENT_MAP.get(ctype, ("Resistor", "IEC"))
        el_cls = getattr(elm, cls_name)
        el = el_cls()

    # Label with value
    if value and ctype != "V":
        el.label(value)

    return el


def _generate_schemdraw(graph: dict, layout: dict, title: str = ""):
    """Generate schemdraw Drawing from layout."""
    from schemdraw import Drawing

    d = Drawing()
    comps = graph["components"]
    main_chain = layout["main_chain"]
    branches = layout["branches"]

    if not comps:
        return d

    # ── Place main chain components ──
    branch_at_col = {}  # col → [(comp_idx, direction)]
    for parent_col, comp_idx, direction in branches:
        branch_at_col.setdefault(parent_col, []).append((comp_idx, direction))

    placed = set()

    for col, comp_idx in enumerate(main_chain):
        comp = comps[comp_idx]
        ctype = comp["type"]
        el = _make_element(comp)

        if col == 0:
            d.add(el)
        else:
            d.add(el.right())

        placed.add(comp_idx)

        # Handle branches at this column
        if col in branch_at_col:
            for bci, bdir in branch_at_col[col]:
                bcomp = comps[bci]
                bel = _make_element(bcomp)
                d.push()
                d.add(bel.down())
                # Add ground if branch goes to GND
                graph_nets = graph["nets"]
                for pn, _ in bcomp["pins"]:
                    if pn in graph["ground_nets"]:
                        import schemdraw.elements as elm
                        d.add(elm.Ground())
                        break
                d.pop()
                placed.add(bci)

    # ── Handle remaining branches (after last main chain element) ──
    last_col = len(main_chain) - 1 if main_chain else 0
    for col, branches_list in branch_at_col.items():
        if col > last_col:
            for bci, bdir in branches_list:
                if bci not in placed:
                    bcomp = comps[bci]
                    bel = _make_element(bcomp)
                    d.push()
                    d.add(bel.down())
                    import schemdraw.elements as elm
                    d.add(elm.Ground())
                    d.pop()
                    placed.add(bci)

    return d


# ═══════════════════════════════════════════════════════════════
# Stage 4: Render
# ═══════════════════════════════════════════════════════════════

def _check_ngspice() -> bool:
    """Check if ngspice is available on PATH."""
    import shutil
    return shutil.which("ngspice") is not None


# Minimal universal opamp model for Ngspice validation
_OPAMP_SUBCKT = """
.subckt opamp in_p in_n out vcc vss
* Simple behavioral opamp: gain=100k, single-pole at 10Hz
G1 0 n1 in_p in_n 1
R1 n1 0 100k
C1 n1 0 1.59e-4
E1 out 0 n1 0 1
Rout out 0 75
.ends opamp
"""

_DIODE_MODEL = """
.model DEFAULT_D D (IS=1e-14 RS=1 N=1)
"""


def _validate_with_ngspice(spice_text: str, work_dir: Path) -> tuple[bool, str]:
    """Run ngspice in batch mode to validate a SPICE netlist.

    Auto-injects a basic opamp model and .op analysis.
    Returns (is_valid, error_message).
    """
    # Prepare netlist: inject models + .op analysis
    full_spice = spice_text.strip()
    has_opamp = "XU" in full_spice or " X" in full_spice
    has_diode = full_spice.upper().startswith("D")

    if has_opamp and ".subckt opamp" not in full_spice.lower():
        full_spice = _OPAMP_SUBCKT + "\n" + full_spice
    if has_diode and ".model" not in full_spice.lower():
        full_spice = _DIODE_MODEL + "\n" + full_spice

    full_spice += "\n.op\n.end\n"

    cir_path = work_dir / "_validate.cir"
    cir_path.write_text(full_spice)

    try:
        result = subprocess.run(
            ["ngspice", "-b", str(cir_path)],
            capture_output=True, text=True, timeout=15,
            cwd=str(work_dir),
        )
        output = result.stderr + result.stdout

        # Check for critical errors
        errors = []
        for line in output.split("\n"):
            upper = line.upper()
            if ("ERROR" in upper or "FATAL" in upper) and "WARNING" not in upper:
                # Skip false positives
                if "no errors" in upper.lower() or "0 error" in upper.lower():
                    continue
                errors.append(line.strip())

        if errors:
            return False, errors[0]  # Return first error

        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Ngspice validation timed out (>15s)"
    except FileNotFoundError:
        return True, ""  # ngspice not installed — skip
    finally:
        # Cleanup temp files
        for pat in ("_validate.cir", "_validate.out", "_validate.raw",
                     "_validate.log", "_validate.tr0", "_validate.ac0"):
            p = work_dir / pat
            if p.exists():
                p.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
# Main Renderer Class
# ═══════════════════════════════════════════════════════════════

class SpiceRenderer:
    TOOLS = [
        ("draw_analog_spice",
         "Draw analog circuits from a SPICE netlist. "
         "Renders professional IEEE-standard circuit diagrams using schemdraw.\n"
         "\n"
         "**Supported components:** R, C, L, D, V (AC/DC), X (op-amp)\n"
         "**Format:** Standard SPICE netlist, one component per line.\n"
         "Node '0', 'gnd', or 'GND' = ground.\n"
         "\n"
         "**Examples:**\n"
         "- RC low-pass: `Vin in 0 AC 1\\nR1 in out 1k\\nC1 out 0 10n`\n"
         "- Sallen-Key: `Vin in 0 AC 1\\nR1 in n1 10k\\nR2 n1 n2 10k\\n"
         "C1 n1 out 1n\\nC2 n2 0 1n\\nXU1 n2 out out vcc 0 opamp`\n"
         "- Inverting amp: `Vin in 0 AC 1\\nR1 in n1 1k\\nRf n1 out 10k\\n"
         "XU1 n1 0 out vcc 0 opamp`",
         "draw_analog_spice",
         {"spice": {"type": "string",
                    "description":
                    "SPICE netlist. R/C/L: name n1 n2 value. "
                    "V: name n+ n- value (AC or DC). X: name nodes... model. "
                    "Example: 'Vin in 0 AC 1\\nR1 in out 1k\\nC1 out 0 10n'"},
          "title": {"type": "string", "description": "Optional diagram title"}},
         ["spice"]),
    ]

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            self.charts_dir = (Path(__file__).parent.parent.parent
                               / "web" / "static" / "charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def draw_analog_spice(self, spice: str, title: str = "") -> str:
        """SPICE netlist → Schemdraw SVG.

        Fallback: if schemdraw is not installed, uses pure-Python SVG.
        """
        try:
            # Stage 0: Ngspice validation (skip if not installed)
            ngspice_msg = ""
            if _check_ngspice():
                valid, err = _validate_with_ngspice(spice, self.charts_dir)
                if not valid:
                    ngspice_msg = f"\n\n⚠️ **Ngspice validation failed:** {err}"

            # Stage 1: Build connectivity graph
            graph = _build_graph(spice)
            if not graph["components"]:
                return "Error: no valid SPICE components found. " \
                       "Supported: R, C, L, D, V, X."

            # Stage 2: Layout
            layout = _layout(graph)

            # Stage 3+4: Generate schemdraw + render
            svg = self._render_schemdraw(graph, layout, title)
        except Exception as e:
            logger.exception(f"SpiceRenderer failed: {e}")
            # Fallback to pure-Python renderer
            try:
                return self._fallback_render(spice, title)
            except Exception as e2:
                return f"Error rendering circuit: {e}\nFallback also failed: {e2}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        spice_block = f"\n\n**SPICE Netlist:**\n```spice\n{spice.strip()}\n```"
        return f"![{title or 'Analog Circuit'}]({url})\n{url}{spice_block}{ngspice_msg}"

    def _render_schemdraw(self, graph: dict, layout: dict, title: str = "") -> str:
        """Generate schemdraw Drawing and render to SVG string."""
        from schemdraw import Drawing

        # Native SVG backend — avoids matplotlib, cleaner SVG output
        d = Drawing(canvas='svg', unit=3)
        comps = graph["components"]
        main_chain = layout["main_chain"]
        branches = layout["branches"]
        ground_nets = graph["ground_nets"]

        if not comps:
            return "<svg></svg>"

        # Index branches by parent column
        branch_at_col = {}
        for parent_col, comp_idx, direction in branches:
            branch_at_col.setdefault(parent_col, []).append((comp_idx, direction))

        placed = set()
        last_direction = None  # track last placement direction for proper flow

        # ── Place main chain ──
        for col, comp_idx in enumerate(main_chain):
            comp = comps[comp_idx]
            el = _make_element(comp)

            if col == 0:
                d.add(el)
            else:
                d.add(el.right())

            placed.add(comp_idx)
            last_direction = "right"

            # ── Branches at this column ──
            if col in branch_at_col:
                for bci, bdir in branch_at_col[col]:
                    if bci in placed:
                        continue
                    bcomp = comps[bci]
                    bel = _make_element(bcomp)
                    d.push()
                    d.add(bel.down())
                    last_direction = "down"

                    # Ground symbol for GND-connected pins
                    for pn, _ in bcomp["pins"]:
                        if pn in ground_nets:
                            import schemdraw.elements as elm
                            d.add(elm.Ground())
                            break
                    d.pop()
                    last_direction = "right"
                    placed.add(bci)

        # ── Remaining unplaced branches ──
        last_col = len(main_chain) - 1 if main_chain else 0
        for col in sorted(branch_at_col.keys()):
            if col <= last_col:
                continue
            for bci, bdir in branch_at_col[col]:
                if bci in placed:
                    continue
                bcomp = comps[bci]
                bel = _make_element(bcomp)
                d.push()
                d.add(bel.down())
                import schemdraw.elements as elm
                d.add(elm.Ground())
                d.pop()
                placed.add(bci)

        # ── Render to SVG string ──
        svg_bytes = d.get_imagedata('svg')
        svg = svg_bytes.decode('utf-8') if isinstance(svg_bytes, bytes) else str(svg_bytes)

        return svg

    def _fallback_render(self, spice: str, title: str = "") -> str:
        """Fallback: use analog_svg's pure-Python renderer."""
        from nano_agent.tools.analog_svg import _parse_spice, _render_svg

        components = _parse_spice(spice)
        if not components:
            return "Error: no valid SPICE components found."

        svg = _render_svg(components, title)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = self.charts_dir / f"analog_{ts}.svg"
        fp.write_text(svg, encoding="utf-8")
        url = f"/charts/{fp.name}"

        return f"![{title or 'Analog Circuit'}]({url})\n{url}\n\n(rendered with pure-Python fallback)"
