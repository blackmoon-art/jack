"""逻辑门 SVG 渲染器 — 零依赖，纯 Python stdlib。

DSL 格式 (每行一个门):
  GATE(input1, input2, ...) = output_name

支持的门:
  组合逻辑: AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF
  时序逻辑: DFF
  复合单元: MUX

示例 — 半加器:
  XOR(A, B) = Sum
  AND(A, B) = Carry

示例 — 全加器:
  XOR(A, B) = g1
  XOR(g1, Cin) = Sum
  AND(A, B) = g2
  AND(g1, Cin) = g3
  OR(g2, g3) = Cout

示例 — 2级同步器:
  BUF(async_in) = s1
  BUF(s1, clk) = synced

示例 — D触发器:
  DFF(D, clk) = Q

示例 — 多路选择器:
  MUX(A, B, sel) = Y
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.logic_svg")


class LogicSVG:
    TOOLS = [
        ("draw_logic",
         "Draw digital logic circuit diagrams as SVG. "
         "Pure logic gate netlist. One gate per line.\n"
         "\n"
         "**Format:** `GATE(input1, input2, ...) = output`\n"
         "**Gates:** AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, DFF, MUX\n"
         "NOT has 1 input. BUF can have 1 or 2 (with clk).\n"
         "DFF(D, clk) = Q  — D flip-flop, clk input has triangle marker.\n"
         "MUX(A, B, sel) = Y  — 2:1 multiplexer.\n"
         "First use of a name = input port. Reuse = internal wire.\n"
         "\n"
         "**Half-adder:**\n"
         "`XOR(A, B) = Sum\nAND(A, B) = Carry`\n"
         "\n"
         "**Full-adder:**\n"
         "`XOR(A, B) = g1\nXOR(g1, Cin) = Sum\nAND(A, B) = g2\nAND(g1, Cin) = g3\nOR(g2, g3) = Cout`\n"
         "\n"
         "**Synchronizer:**\n"
         "`BUF(async_in, clk) = s1\nBUF(s1, clk) = synced`\n"
         "\n"
         "**D flip-flop:**\n"
         "`DFF(D, clk) = Q`\n"
         "\n"
         "**Multiplexer:**\n"
         "`MUX(A, B, sel) = Y`",
         "draw_logic",
         {"description": {"type": "string",
                          "description":
                          "Logic gate netlist. One gate per line. "
                          "GATE(input1, input2) = output. "
                          "Gates: AND,OR,NOT,NAND,NOR,XOR,XNOR,BUF,DFF,MUX. "
                          "DFF(D, clk)=Q for D flip-flop. "
                          "MUX(A,B,sel)=Y for 2:1 multiplexer. "
                          "Example: 'XOR(A,B)=Sum\\nAND(A,B)=Carry' for half-adder"},
          "title": {"type": "string", "description": "Diagram title"}},
         ["description"]),
    ]

    # ── 门形状定义 ──────────────────────────────────
    GATE_SHAPES = {
        "AND":   ("and",   False),
        "NAND":  ("and",   True),
        "OR":    ("or",    False),
        "NOR":   ("or",    True),
        "XOR":   ("xor",   False),
        "XNOR":  ("xor",   True),
        "NOT":   ("not",   True),
        "BUF":   ("buf",   False),
        "DFF":   ("dff",   False),
        "MUX":   ("mux",   False),
    }

    def __init__(self, work_dir: str = "", charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            web_static = Path(__file__).parent.parent.parent / "web" / "static"
            self.charts_dir = web_static / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    def draw_logic(self, description: str, title: str = "",
                    layout: str = "sugiyama", seed: int | None = None) -> str:
        """解析 DSL → 布局 → 渲染 SVG。

        layout param kept for API compatibility.
        seed: if set, perturbs barycenter weights for layout variation on retry.
        """
        try:
            gates, inputs, outputs = self._parse(description)
            if not gates:
                return "Error: no valid gates found. Format: GATE(a,b) = out"

            # ── Circuit validation ──
            warnings = LogicSVG.validate_circuit(gates, inputs, outputs)

            svg = self._render(gates, inputs, outputs, title, layout=layout, seed=seed)
        except Exception as e:
            return f"Error drawing logic: {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"logic_{ts}.svg"
        filepath = self.charts_dir / filename
        filepath.write_text(svg, encoding="utf-8")
        url = f"/charts/{filename}"

        # Build result with validation warnings
        result_parts = [f"![{title or 'Logic'}]({url})\n{url}"]
        if warnings:
            result_parts.append("\n### ⚠️ Circuit Warnings")
            for w in warnings[:8]:
                result_parts.append(f"- {w}")
        return "\n".join(result_parts)
        return f"![{title or 'Logic'}]({url})\n{url}"

    # ── DSL 解析 ───────────────────────────────────

    @staticmethod
    def _parse(desc: str) -> tuple[list, set, set]:
        """解析 DSL，返回 (gates, inputs, outputs)。"""
        gates = []
        all_inputs = set()
        all_outputs = set()
        internal = set()

        for line in desc.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(
                r'(AND|OR|NOT|NAND|NOR|XOR|XNOR|BUF|DFF|MUX)'
                r'\(([^)]+)\)\s*=\s*([\w\[\]]+)', line, re.IGNORECASE)
            if not m:
                continue
            gtype = m.group(1).upper()
            raw_inputs = [x.strip() for x in m.group(2).split(",")]
            output = m.group(3).strip()

            gate = {"type": gtype, "inputs": raw_inputs, "output": output}
            gates.append(gate)
            all_outputs.add(output)
            for inp in raw_inputs:
                all_inputs.add(inp)

        # 纯输入 = 在 inputs 中但不在 outputs 中
        true_inputs = all_inputs - all_outputs
        # 纯输出 = 在 outputs 中但不在 inputs 中 → 外部输出
        true_outputs = all_outputs - all_inputs
        # 都在 = 内部反馈线 (如 DFF→组合逻辑→DFF)
        internal = all_inputs & all_outputs

        # For sequential circuits: keep DFF outputs as visible output ports
        # even if they feed back (e.g. count[0] drives both NOT gate and output pin)
        for g in gates:
            if g["type"] == "DFF" and g["output"] in internal:
                true_outputs.add(g["output"])
                internal.discard(g["output"])

        return gates, true_inputs, true_outputs

    # ── SVG 渲染 ───────────────────────────────────

    W, H = 100, 70      # 门尺寸
    IX, IY = 2, 10      # 输入引脚间距
    PIN = 10            # 引脚突出长度
    PORT_W, PORT_H = 48, 28  # 端口尺寸
    COL_GAP = 120       # 列间距
    ROW_GAP = 80        # 行间距
    WIRE_SPREAD = 12    # 多线时的垂直展开距离
    COLORS = {
        "bg": "#1a1a2e", "fg": "#e0e0e0", "grid": "#333",
        "gate_fill": "#2a2a4e", "gate_stroke": "#7c3aed",
        "wire": "#7c3aed", "port_fill": "#0f172a",
        "port_stroke": "#3b82f6", "text": "#e0e0e0",
        "title": "#e0e0e0",
    }
    FONT = "monospace"

    def _render(self, gates, inputs, outputs, title="", layout="sugiyama", seed=None) -> str:
        """Layout + SVG rendering. layout param kept for API compat, always Sugiyama."""
        return self._render_iterative(gates, inputs, outputs, title, seed=seed)

    def _render_iterative(self, gates, inputs, outputs, title="",
                           max_attempts=16, target_score=8.0, seed=None) -> str:
        """Metric-driven iterative layout: try different params, score with SVG analyzer, keep best.

        Uses the same score_layout_quality() that the gate check uses,
        ensuring internal optimization and gate threshold are aligned.
        seed: if set, passed through to barycenter perturbation for layout variation.
        """
        n_gates = len(gates)
        best_svg = None
        best_score = -1
        best_config = ""

        if n_gates <= 10:
            spacings = [(150, 130), (180, 160)]
            sortings = ["barycenter", "median", "natural"]
            channels = [28, 40]
        elif n_gates <= 30:
            # Include wider col_gap for row-based multi-bit adders
            # (only 3-4 columns need more horizontal room)
            spacings = [(240, 220), (300, 280), (360, 340), (400, 120)]
            sortings = ["barycenter", "median", "natural"]
            channels = [40, 52, 64]
        else:
            spacings = [(340, 300), (440, 380), (580, 480)]
            sortings = ["natural", "barycenter"]
            channels = [52, 68, 92]

        # Pre-filter: fast-score all combos with internal scorer, then
        # render+SVG-score only the top candidates to save CPU.
        MAX_RENDER = 5  # max combos to actually render
        prescores = []
        for col_gap, row_gap in spacings:
            for sorting in sortings:
                for channel_h in channels:
                    iscore = LogicSVG._score_layout_internal(
                        gates, inputs, outputs, col_gap, row_gap, channel_h)
                    prescores.append((iscore, col_gap, row_gap, sorting, channel_h))

        # Sort by internal score descending, keep top MAX_RENDER
        prescores.sort(key=lambda x: x[0], reverse=True)
        top_combos = prescores[:MAX_RENDER]

        # Render only the best pre-scored combos
        candidates = []
        for iscore, col_gap, row_gap, sorting, channel_h in top_combos:
            try:
                svg_xml = self._render_sugiyama_with_params(
                    gates, inputs, outputs, title,
                    col_gap, row_gap, channel_h, sorting, seed=seed)
                s = LogicSVG.score_layout_quality(svg_xml).get("score", 0)
                candidates.append((s, svg_xml,
                    f"sugiyama gap={col_gap}/{row_gap} sort={sorting} ch={channel_h}"))
            except Exception:
                pass

        # Pick best (or use fallback if all candidates failed)
        if not candidates:
            best_svg = self._render_sugiyama_with_params(
                gates, inputs, outputs, title, 120, 80, 12, "barycenter")
            best_config = "fallback"
            best_score = 0
        else:
            best = max(candidates, key=lambda c: c[0])
            best_score, best_svg, best_config = best

        logger.info(f"Layout: best={best_config} score={best_score:.1f}")
        return best_svg

    @staticmethod
    def _score_layout_internal(gates, inputs, outputs, col_gap, row_gap,
                                channel_h) -> float:
        """Fast internal layout scorer — estimates quality from geometry.

        Without rendering SVG, computes:
          - gate density (gates per column)
          - span ratio (width vs height balance)
          - estimated wire congestion

        Returns 0-10 score.
        """
        import math
        n_gates = len(gates)
        if n_gates == 0:
            return 10.0

        # Build depth map (same as Sugiyama)
        produced_by = {}
        for i, g in enumerate(gates):
            produced_by[g["output"]] = i

        depth = {}
        visiting = set()

        def get_depth(gi):
            if gi in depth:
                return depth[gi]
            if gi in visiting:
                return 1
            visiting.add(gi)
            g = gates[gi]
            max_in = 0
            for inp in g["inputs"]:
                if inp in produced_by:
                    max_in = max(max_in, get_depth(produced_by[inp]))
            visiting.discard(gi)
            depth[gi] = max_in + 1
            return depth[gi]

        for i in range(n_gates):
            get_depth(i)

        cols = {}
        for i in range(n_gates):
            d = depth[i]
            cols.setdefault(d, []).append(i)

        max_depth = max(cols.keys()) if cols else 0
        max_col_size = max(len(v) for v in cols.values()) if cols else 1

        # Metrics
        score = 10.0

        # 1. Gate distribution (balanced columns = better)
        col_sizes = [len(v) for v in cols.values()]
        if col_sizes:
            avg_size = sum(col_sizes) / len(col_sizes)
            imbalance = max(abs(s - avg_size) for s in col_sizes) / max(avg_size, 1)
            score -= imbalance * 2.0  # -0 to -2

        # 2. Aspect ratio (prefer wider than tall)
        total_cols = max_depth + (1 if inputs else 0)
        total_rows = max_col_size
        aspect = (total_cols * col_gap) / max(total_rows * (row_gap + channel_h), 1)
        if aspect < 0.5:
            score -= 2.0  # too narrow
        elif aspect > 6.0:
            score -= 1.0  # too wide
        else:
            score += 0.5  # bonus

        # 3. Depth penalty (deep circuits = harder to read)
        if max_depth > 5:
            score -= (max_depth - 5) * 0.5

        # 4. Row density (too many gates per column)
        if max_col_size > 6:
            score -= (max_col_size - 6) * 0.5

        # 5. Spacing bonus (wider = more readable)
        if col_gap >= 150:
            score += 1.0

        # 6. Channel bonus (more routing space)
        if channel_h >= 20:
            score += 0.5

        # 7. Small circuit bonus
        if n_gates <= 5:
            score += 1.0

        return max(0.0, min(10.0, score))

    def _render_sugiyama_with_params(self, gates, inputs, outputs, title,
                                      col_gap, row_gap, channel_h,
                                      sorting="barycenter", seed=None) -> str:
        """Render Sugiyama with explicit parameters (no auto-scaling)."""
        return self._render_sugiyama(gates, inputs, outputs, title,
                                     _col_gap=col_gap, _row_gap=row_gap,
                                     _channel_h=channel_h, _sorting=sorting,
                                     _seed=seed)

    @staticmethod
    def _detect_clusters(gates):
        """Group gates into macro-node clusters for cleaner layout.

        Detects:
          - Counter bit: NOT(q_i)=n + DFF(prev_q, n)=q_i  (feedback cycle)
          - Feed-forward: output of A goes exclusively to B

        Returns: cluster_id for each gate index (list of ints, same length as gates)
        """
        n = len(gates)
        cluster_of = list(range(n))  # initially each gate is its own cluster

        # Build adjacency
        produced_by = {}
        consumed_by = {}
        for i, g in enumerate(gates):
            produced_by[g["output"]] = i
            for inp in g["inputs"]:
                consumed_by.setdefault(inp, []).append(i)

        # Pattern 1: Counter bit — NOT and DFF with mutual dependency
        for i, g in enumerate(gates):
            if g["type"] != "NOT":
                continue
            not_out = g["output"]
            # NOT output must go to exactly one DFF
            consumers = consumed_by.get(not_out, [])
            if len(consumers) != 1:
                continue
            dff_idx = consumers[0]
            dff_gate = gates[dff_idx]
            if dff_gate["type"] != "DFF":
                continue
            # DFF output must go back to this NOT (feedback cycle)
            dff_out = dff_gate["output"]
            not_consumers_of_dff = consumed_by.get(dff_out, [])
            if i not in not_consumers_of_dff:
                continue
            # Valid counter bit cluster: merge NOT and DFF
            cid = min(cluster_of[i], cluster_of[dff_idx])
            cluster_of[i] = cid
            cluster_of[dff_idx] = cid

        # Pattern 2: HalfAdder — two gates sharing the same input set
        # (e.g. XOR+AND sharing a,b; NAND+OR sharing a,b after synthesis)
        for i in range(n):
            for j in range(i + 1, n):
                # Only cluster singletons (not already in CounterBit etc.)
                if cluster_of[i] != i or cluster_of[j] != j:
                    continue
                # Check for identical input sets (order-independent)
                inputs_i = frozenset(gates[i]["inputs"])
                inputs_j = frozenset(gates[j]["inputs"])
                if inputs_i == inputs_j and len(inputs_i) >= 2:
                    # Valid HalfAdder cluster: merge the two gates
                    cid = min(cluster_of[i], cluster_of[j])
                    # Also propagate: update any gates previously clustered
                    old_i, old_j = cluster_of[i], cluster_of[j]
                    for k in range(n):
                        if cluster_of[k] == old_i or cluster_of[k] == old_j:
                            cluster_of[k] = cid

        # Compress cluster IDs to contiguous range
        id_map = {}
        next_id = 0
        result = []
        for c in cluster_of:
            if c not in id_map:
                id_map[c] = next_id
                next_id += 1
            result.append(id_map[c])
        return result

    @staticmethod
    def _detect_adder_stages(gates, cluster_of):
        """Detect full-adder / ripple-adder structure and assign layout stages.

        Single-bit: returns {gate_index: stage_number} (column-based).
        Multi-bit:  returns ({gate_index: local_stage}, {gate_index: bit_index})
                    for row-per-bit grid layout (bit=row, stage=column).
        Returns {} if not an adder.
        """
        n = len(gates)
        has_xor = any(g["type"] == "XOR" for g in gates)
        if not has_xor or n < 5:
            return {}

        # Build adjacency
        produced_by = {}
        consumed_by = {}
        for i, g in enumerate(gates):
            produced_by[g["output"]] = i
            for inp in g["inputs"]:
                consumed_by.setdefault(inp, []).append(i)

        # Primary inputs: wires not produced by any gate
        all_outputs = set(produced_by.keys())
        primary_inputs = set()
        for g in gates:
            for inp in g["inputs"]:
                if inp not in all_outputs:
                    primary_inputs.add(inp)

        # ── Step 1: Find "first XORs" — XOR gates taking two primary inputs ──
        # These are the per-bit anchors: XOR(a[i], b[i]). For a single-bit
        # adder there's one; for ripple adders there's one per bit.
        first_xors = []  # [(gate_idx, output_wire)]
        for gi, g in enumerate(gates):
            if g["type"] == "XOR" and all(inp in primary_inputs for inp in g["inputs"]):
                first_xors.append((gi, g["output"]))

        if not first_xors:
            return {}

        # ── Step 2: Assign gates to bits ──
        # Build a dependency map: each gate → max bit index based on inputs.
        # Primary inputs have bit indices (from name), otherwise propagate.

        def _primary_bit(name):
            """Extract bit index from a primary input name like a[2] or b[1]."""
            m = re.search(r'\[(\d+)\]', name)
            return int(m.group(1)) if m else None

        # Map primary inputs to bit indices
        primary_bit = {}
        for inp in primary_inputs:
            b = _primary_bit(inp)
            if b is not None:
                primary_bit[inp] = b

        # Assign each gate to a bit
        gate_bit = {}  # gate_idx → bit number
        # Multiple passes to propagate bit assignments
        changed = True
        while changed:
            changed = False
            for gi, g in enumerate(gates):
                if gi in gate_bit:
                    continue
                bit = None
                for inp in g["inputs"]:
                    if inp in primary_bit:
                        b = primary_bit[inp]
                        bit = max(bit, b) if bit is not None else b
                    elif inp in produced_by:
                        producer = produced_by[inp]
                        if producer in gate_bit:
                            b = gate_bit[producer]
                            bit = max(bit, b) if bit is not None else b
                if bit is not None:
                    gate_bit[gi] = bit
                    changed = True

        # Default unassigned gates to bit 0
        for gi in range(n):
            if gi not in gate_bit:
                gate_bit[gi] = 0

        # Count bits
        num_bits = max(gate_bit.values()) + 1 if gate_bit else 1
        STAGES_PER_BIT = 4

        # ── Step 4: Assign stages within each bit ──
        stage = {}
        bit_of = {}  # gate_idx → bit_idx (for multi-bit row layout)

        for bit_idx in range(num_bits):
            bit_gates = [gi for gi in range(n) if gate_bit.get(gi, 0) == bit_idx]
            bit_first_xors = [(gi, out) for gi, out in first_xors
                              if gate_bit.get(gi, 0) == bit_idx]

            if not bit_first_xors:
                for i, gi in enumerate(bit_gates):
                    if gi not in stage:
                        stage[gi] = (i % STAGES_PER_BIT) + 1
                        bit_of[gi] = bit_idx
                continue

            first_xor_gi, first_xor_out = bit_first_xors[0]
            first_xor_inputs = frozenset(gates[first_xor_gi]["inputs"])

            # Stage 1: first XOR + gates sharing its inputs (HA1)
            stage[first_xor_gi] = 1
            bit_of[first_xor_gi] = bit_idx
            for gi in bit_gates:
                if gi == first_xor_gi:
                    continue
                if frozenset(gates[gi]["inputs"]) == first_xor_inputs:
                    stage[gi] = 1
                    bit_of[gi] = bit_idx

            # Stage 2: AND gates consuming primary inputs (carry terms)
            for gi in bit_gates:
                if gi in stage:
                    continue
                g_inputs = set(gates[gi]["inputs"])
                primary_hits = sum(1 for inp in g_inputs if inp in primary_inputs)
                if gates[gi]["type"] in ("AND",) and primary_hits >= 1:
                    stage[gi] = 2
                    bit_of[gi] = bit_idx

            # Stage 3: sum XOR + intermediate carry OR
            for gi in bit_gates:
                if gi in stage:
                    continue
                g_inputs = set(gates[gi]["inputs"])
                if gates[gi]["type"] == "XOR" and first_xor_out in g_inputs:
                    stage[gi] = 3
                    bit_of[gi] = bit_idx
                elif gates[gi]["type"] == "OR":
                    depends_on_earlier = False
                    for inp in g_inputs:
                        if inp in produced_by:
                            producer = produced_by[inp]
                            if producer in stage and stage[producer] <= 2:
                                depends_on_earlier = True
                                break
                    if depends_on_earlier:
                        stage[gi] = 3
                        bit_of[gi] = bit_idx

            # Stage 4: remaining gates (final carry OR, etc.)
            for gi in bit_gates:
                if gi not in stage:
                    stage[gi] = 4
                    bit_of[gi] = bit_idx

        # Validate
        stages_used = set(stage.values())
        if len(stages_used) >= 3 and len(stage) >= n * 0.8:
            if num_bits > 1:
                return (stage, bit_of)
            return stage
        return {}

    @staticmethod
    def validate_circuit(gates, inputs, outputs):
        """Validate circuit logic before rendering. Returns list of warnings.

        Checks:
          - DFF has D and CLK inputs
          - Each output wire has exactly one driver (no multiple drivers)
          - No floating inputs (driven by no gate)
          - No combinational loops (cycles without DFF)
          - Pattern matching: counter/adder connectivity
        """
        issues = []
        produced_by = {}
        consumed_by = {}
        for i, g in enumerate(gates):
            produced_by[g["output"]] = i
            for inp in g["inputs"]:
                consumed_by.setdefault(inp, []).append(i)

        # ── 1. DFF validation ──
        # Convention: DFF(CLK, D, [RST], [EN]) = Q — CLK is inputs[0], D is inputs[1]
        for i, g in enumerate(gates):
            if g["type"] != "DFF":
                continue
            if len(g["inputs"]) < 2:
                issues.append(f"DFF {g['output']}: too few inputs ({len(g['inputs'])}, need CLK+D)")
            elif len(g["inputs"]) >= 2:
                # inputs[0]=CLK, inputs[1]=D. Check D has a driver (not floating).
                d_wire = g["inputs"][1]
                if d_wire not in produced_by and d_wire not in inputs:
                    issues.append(f"DFF {g['output']}: D input '{d_wire}' has no driver")

        # ── 2. Multiple drivers ──
        driver_count = {}
        for i, g in enumerate(gates):
            out = g["output"]
            driver_count.setdefault(out, []).append(i)
        for wire, drivers in driver_count.items():
            if len(drivers) > 1:
                types = [gates[d]["type"] for d in drivers]
                issues.append(f"Wire '{wire}' driven by {len(drivers)} gates: {types}")

        # ── 3. Floating inputs ──
        for i, g in enumerate(gates):
            for inp in g["inputs"]:
                if inp.lower() in ("clk", "rst", "reset", "en", "enable", "vcc", "vdd",
                                    "gnd", "vss", "1'b0", "1'b1", "1'd0", "1'd1"):
                    continue
                if inp not in produced_by and inp not in inputs:
                    issues.append(f"Gate {g['type']}:{g['output']} has floating input '{inp}'")

        # ── 4. Combinational loop detection ──
        # DFS on combinational gates only (exclude DFF which break cycles)
        comb_gates = {i for i, g in enumerate(gates) if g["type"] != "DFF"}
        visited = set()
        visiting = set()

        def has_comb_cycle(gi):
            if gi in visiting:
                return True
            if gi in visited or gi not in comb_gates:
                return False
            visiting.add(gi)
            g = gates[gi]
            for inp in g["inputs"]:
                if inp in produced_by:
                    pi = produced_by[inp]
                    if has_comb_cycle(pi):
                        visiting.discard(gi)
                        return True
            visiting.discard(gi)
            visited.add(gi)
            return False

        for i in comb_gates:
            if i not in visited:
                if has_comb_cycle(i):
                    issues.append(f"Combinational loop detected involving gate {gates[i]['type']}:{gates[i]['output']}")
                    break  # one loop is enough

        # ── 5. Pattern matching ──
        # Counter: NOT+DFF pairs with feedback
        counter_bits = 0
        for i, g in enumerate(gates):
            if g["type"] != "NOT":
                continue
            not_out = g["output"]
            dff_consumers = [c for c in consumed_by.get(not_out, [])
                           if gates[c]["type"] == "DFF"]
            for dff_idx in dff_consumers:
                dff_out = gates[dff_idx]["output"]
                if i in consumed_by.get(dff_out, []):
                    counter_bits += 1
        if counter_bits >= 3:
            # Verify counter chain: DFF_i output → next DFF clock
            chain_ok = True
            for i, g in enumerate(gates):
                if g["type"] != "DFF":
                    continue
                dff_out = g["output"]
                next_clk = False
                for c in consumed_by.get(dff_out, []):
                    if gates[c]["type"] == "DFF":
                        next_clk = True
                if not next_clk and counter_bits > 1:
                    # Last DFF might not drive another DFF clock
                    pass
            if not chain_ok:
                issues.append("Counter chain broken: DFF output not driving next DFF clock")

        # Adder: look for ripple-carry chain (XOR sum + AND/NAND carry)
        adder_stages = 0
        for i, g in enumerate(gates):
            if g["type"] in ("XOR", "XNOR") and g["output"] not in inputs:
                out_name = g["output"]
                # Check if this XOR output goes to an output port
                if out_name in outputs or any(
                    "sum" in out_name.lower() or "s[" in out_name.lower()
                    for _ in [1]):
                    # Look for associated carry gate
                    for inp in g["inputs"]:
                        if inp in produced_by:
                            pi = produced_by[inp]
                            if gates[pi]["type"] in ("AND", "NAND", "OR"):
                                adder_stages += 1
                                break
        # Heuristic: XOR gates producing outputs suggest adder structure
        xor_outputs = [i for i, g in enumerate(gates)
                      if g["type"] in ("XOR", "XNOR") and g["output"] in outputs]
        if len(xor_outputs) >= 2 and adder_stages < len(xor_outputs):
            issues.append(
                f"Possible broken adder: {len(xor_outputs)} XOR outputs "
                f"but only {adder_stages} carry gates detected")

        return issues

    def _render_sugiyama(self, gates, inputs, outputs, title="",
                          _col_gap=None, _row_gap=None, _channel_h=None,
                          _sorting=None, _seed=None) -> str:
        """Classic Sugiyama layered layout (for small circuits ≤10 gates)."""
        LogicSVG._wire_seq = 0
        n_gates = len(gates)
        # Use explicit params if provided, otherwise auto-scale
        if _col_gap is not None:
            col_gap, row_gap = _col_gap, _row_gap or _col_gap
            channel_h = _channel_h or 12
            sorting = _sorting or "barycenter"
        elif n_gates <= 10:
            col_gap, row_gap = 120, 80
            channel_h = 12
            sorting = "barycenter"
        elif n_gates <= 30:
            col_gap, row_gap = 150, 100
            channel_h = 16
            sorting = "natural"
        else:
            col_gap, row_gap = 180, 120
            channel_h = 20
            sorting = "natural"

        # ── Phase 0: Cluster detection (counter bits, etc.) ──
        cluster_of = LogicSVG._detect_clusters(gates)
        # Build cluster → gate list mapping
        cluster_gates = {}
        for gi, ci in enumerate(cluster_of):
            cluster_gates.setdefault(ci, []).append(gi)

        # ── Phase 1: Topological depth assignment with feedback detection ──
        produced_by = {}
        consumed_by = {}  # wire_name → [(gate_index, input_index), ...]
        for i, g in enumerate(gates):
            produced_by[g["output"]] = i
            for inp in g["inputs"]:
                consumed_by.setdefault(inp, []).append(i)

        # Feedback edges: (producer_gate_idx, consumer_gate_idx, wire_name)
        # These are cycle-closing edges — routing them through a dedicated bus
        # avoids crossing forward-going wires.
        feedback_edges = set()

        depth = {}
        visiting = set()
        def get_depth(gi, from_gate=None):
            if gi in depth:
                return depth[gi]
            if gi in visiting:
                # Cycle detected: edge from parent to gi is feedback
                if from_gate is not None:
                    # Find which wire connects from_gate → gi
                    g_out = gates[from_gate]["output"]
                    if g_out in consumed_by:
                        for cgi in consumed_by[g_out]:
                            if cgi == gi:
                                feedback_edges.add((from_gate, gi, g_out))
                                break
                return 1
            visiting.add(gi)
            g = gates[gi]
            max_in = 0
            for inp in g["inputs"]:
                if inp in produced_by:
                    producer = produced_by[inp]
                    # Skip known feedback edges to make graph acyclic
                    if (producer, gi, inp) in feedback_edges:
                        continue
                    max_in = max(max_in, get_depth(producer, gi))
            visiting.discard(gi)
            depth[gi] = max_in + 1
            return depth[gi]

        for i in range(len(gates)):
            get_depth(i)

        # Second pass: re-run depth assignment ignoring ALL feedback edges
        # (some edges weren't marked in first pass because the cycle was
        # detected from the other direction)
        if feedback_edges:
            depth.clear()
            for i in range(len(gates)):
                get_depth(i)

        # ── Phase 1.4: Adder-aware depth remapping ──
        # Detect full adder / ripple adder structure.
        # Single-bit: stage → column (data flow left-to-right).
        # Multi-bit:  stage → column, bit → row (row-per-bit grid layout).
        adder_result = LogicSVG._detect_adder_stages(gates, cluster_of)
        adder_bit_of = None  # {gate_idx: bit_index} for multi-bit
        if adder_result:
            if isinstance(adder_result, tuple):
                adder_stage, adder_bit_of = adder_result
            else:
                adder_stage = adder_result
            for gi, stage in adder_stage.items():
                depth[gi] = stage

        # ── Phase 1.5: CounterBit depth remapping ──
        # Each CounterBit cluster (DFF+NOT) gets its own column for the
        # classic ripple counter look. HalfAdder clusters (NDN+OR etc.)
        # keep their original topological depths — they use Phase 2.0
        # column merging + Phase 2.3 row adjacency instead.
        counterbit_clusters = []
        for ci, members in cluster_gates.items():
            if len(members) <= 1:
                continue
            # Only remap clusters that contain a DFF (CounterBit pattern)
            has_dff = any(gates[gi]["type"] == "DFF" for gi in members)
            if has_dff:
                counterbit_clusters.append((ci, members))

        counterbit_clusters.sort(key=lambda x: min(depth.get(gi, 999) for gi in x[1]))

        next_cb_depth = 1
        for ci, members in counterbit_clusters:
            for gi in members:
                depth[gi] = next_cb_depth
            next_cb_depth += 1

        # Shift non-CounterBit gates past the CounterBit columns
        if counterbit_clusters:
            depth_offset = next_cb_depth - 1
            cb_gate_set = set()
            for _, members in counterbit_clusters:
                cb_gate_set.update(members)
            for gi in range(len(gates)):
                if gi not in cb_gate_set:
                    depth[gi] = depth.get(gi, 0) + depth_offset

        # Group gates by depth
        cols = {}
        for i, g in enumerate(gates):
            d = depth[i]
            cols.setdefault(d, []).append(i)

        max_depth = max(cols.keys()) if cols else 0
        total_cols = max_depth + 1
        if inputs:
            total_cols += 1

        # ── Phase 2: Barycenter crossing minimization ──
        # Assign initial row positions (by gate index within column)
        row_of = {}  # gate_index → row within its column
        col_of = {}  # gate_index → column index
        col_of_input = {}  # input_name → column (0 if inputs exist, else -1)

        # Sort key: bus signals by bit index (a[0],b[0],a[1],b[1]...),
        # non-bus signals (clk, cin) after. Keeps per-bit inputs adjacent.
        def _input_sort(name):
            m = re.search(r'\[(\d+)\]', name)
            return (int(m.group(1)) if m else 9999, name)

        if inputs:
            input_col = 0
            for ri, name in enumerate(sorted(inputs, key=_input_sort)):
                col_of_input[name] = input_col
        else:
            input_col = -1

        # Seed-based initial row randomization for layout variation on retry
        import random as _random
        _rng = _random.Random(_seed) if _seed is not None else None
        if _rng is not None:
            for d in cols:
                _rng.shuffle(cols[d])

        for d in sorted(cols.keys()):
            col_idx = d + (1 if inputs else 0)
            for ri, gi in enumerate(cols[d]):
                row_of[gi] = ri
                col_of[gi] = col_idx

        # ── Cluster collapsing: merge cluster members into shared column ──
        # Run BEFORE barycenter so crossing reduction can optimize the merged layout.
        for ci, members in cluster_gates.items():
            if len(members) <= 1:
                continue
            min_col = min(col_of[gi] for gi in members)
            for gi in members:
                col_of[gi] = min_col

        # Recompute total columns after cluster merging
        active_cols = set(col_of.values())
        total_cols = len(active_cols)

        # Helper: get position (col, row) for a wire name
        def wire_pos(name):
            if name in col_of_input:
                # Input port: row from sorted position
                inames = sorted(inputs, key=_input_sort)
                return (col_of_input[name], inames.index(name))
            if name in produced_by:
                gi = produced_by[name]
                return (col_of[gi], row_of[gi])
            # Output port: will be placed later
            return None

        # Phase 2: Sorting strategy
        if sorting == "random":
            for d in sorted(cols.keys()):
                (_rng or _random).shuffle(cols[d])
                for ri, gi in enumerate(cols[d]):
                    row_of[gi] = ri
        elif sorting == "natural":
            # Natural ordering by input signal index (+ seed jitter)
            def _input_order(gi):
                g = gates[gi]
                for inp in g["inputs"]:
                    m = re.search(r'\[(\d+)\]', inp)
                    if m:
                        return int(m.group(1))
                return 0
            for d in sorted(cols.keys()):
                if _rng is not None:
                    cols[d].sort(key=lambda gi: _input_order(gi) + _rng.uniform(-1.5, 1.5))
                else:
                    cols[d].sort(key=_input_order)
                for ri, gi in enumerate(cols[d]):
                    row_of[gi] = ri
        elif _sorting is not None or n_gates <= 20:
            # Full 3-pass barycenter (explicit or default for small circuits)
            for _pass in range(3):
                # Left → right: sort by input barycenter
                for d in sorted(cols.keys()):
                    if d == 1 and not inputs:
                        continue
                    bary = {}
                    for gi in cols[d]:
                        g = gates[gi]
                        input_rows = []
                        for inp in g["inputs"]:
                            wpos = wire_pos(inp)
                            if wpos:
                                input_rows.append(wpos[1])
                        b = sum(input_rows) / len(input_rows) if input_rows else float("inf")
                        if _rng is not None:
                            b += _rng.uniform(-1.5, 1.5)
                        bary[gi] = b
                    cols[d].sort(key=lambda gi: (bary[gi], row_of.get(gi, 0)))

                for d in sorted(cols.keys()):
                    for ri, gi in enumerate(cols[d]):
                        row_of[gi] = ri

                # Right → left: sort by output barycenter
                for d in sorted(cols.keys(), reverse=True):
                    bary = {}
                    for gi in cols[d]:
                        out_name = gates[gi]["output"]
                        consumer_rows = []
                        for cgi in consumed_by.get(out_name, []):
                            if cgi in row_of:
                                consumer_rows.append(row_of[cgi])
                        b = sum(consumer_rows) / len(consumer_rows) if consumer_rows else float("inf")
                        if _rng is not None:
                            b += _rng.uniform(-1.5, 1.5)
                        bary[gi] = b
                    cols[d].sort(key=lambda gi: (bary[gi], row_of.get(gi, 0)))

                for d in sorted(cols.keys()):
                    for ri, gi in enumerate(cols[d]):
                        row_of[gi] = ri
        elif _sorting == "median":
            # 3-pass Median heuristic: use median instead of mean for robustness
            for _pass in range(3):
                # Left → right: sort by input median
                for d in sorted(cols.keys()):
                    if d == 1 and not inputs:
                        continue
                    med = {}
                    for gi in cols[d]:
                        g = gates[gi]
                        input_rows = []
                        for inp in g["inputs"]:
                            wpos = wire_pos(inp)
                            if wpos:
                                input_rows.append(wpos[1])
                        if input_rows:
                            sorted_rows = sorted(input_rows)
                            n = len(sorted_rows)
                            if n % 2 == 1:
                                m = sorted_rows[n // 2]
                            else:
                                m = (sorted_rows[n // 2 - 1] + sorted_rows[n // 2]) / 2
                            if _rng is not None:
                                m += _rng.uniform(-1.0, 1.0)
                            med[gi] = m
                        else:
                            med[gi] = float("inf")
                    cols[d].sort(key=lambda gi: (med[gi], row_of.get(gi, 0)))
                for d in sorted(cols.keys()):
                    for ri, gi in enumerate(cols[d]):
                        row_of[gi] = ri
                # Right → left: sort by output median
                for d in sorted(cols.keys(), reverse=True):
                    med = {}
                    for gi in cols[d]:
                        out_name = gates[gi]["output"]
                        consumer_rows = []
                        for cgi in consumed_by.get(out_name, []):
                            if cgi in row_of:
                                consumer_rows.append(row_of[cgi])
                        if consumer_rows:
                            sorted_rows = sorted(consumer_rows)
                            n = len(sorted_rows)
                            if n % 2 == 1:
                                m = sorted_rows[n // 2]
                            else:
                                m = (sorted_rows[n // 2 - 1] + sorted_rows[n // 2]) / 2
                            if _rng is not None:
                                m += _rng.uniform(-1.0, 1.0)
                            med[gi] = m
                        else:
                            med[gi] = float("inf")
                    cols[d].sort(key=lambda gi: (med[gi], row_of.get(gi, 0)))
                for d in sorted(cols.keys()):
                    for ri, gi in enumerate(cols[d]):
                        row_of[gi] = ri
        else:
            # Large circuits: natural ordering by input signal index
            # (preserves bit-slice structure in adders, ALUs, etc.)
            def _input_order(gi):
                """Sort key: extract numeric index from input names like a[3]."""
                g = gates[gi]
                for inp in g["inputs"]:
                    # Match bit-indexed names: a[3], b[7], count[2]
                    m = re.search(r'\[(\d+)\]', inp)
                    if m:
                        return int(m.group(1))
                return 0
            for d in sorted(cols.keys()):
                if _rng is not None:
                    cols[d].sort(key=lambda gi: _input_order(gi) + _rng.uniform(-1.5, 1.5))
                else:
                    cols[d].sort(key=_input_order)
                for ri, gi in enumerate(cols[d]):
                    row_of[gi] = ri

        # Update col_of after sorting
        for d in sorted(cols.keys()):
            col_idx = d + (1 if inputs else 0)
            for gi in cols[d]:
                col_of[gi] = col_idx

        # ── Phase 2.3: Cluster row adjacency (CounterBit, AdderBit, etc.) ──
        # Each cluster occupies its own column (from Phase 1.5). Within each
        # column, stack cluster members vertically: DFF on top, NOT below.
        _struct_order = {"DFF": 0, "HA": 0, "FA": 0, "XOR": 0,
                         "NOT": 1, "AND": 1, "OR": 1, "NAND": 1,
                         "NOR": 1, "XNOR": 1, "BUFFER": 1, "BUF": 1}
        for ci, members in cluster_gates.items():
            if len(members) <= 1:
                continue
            members_sorted = sorted(members,
                key=lambda gi: _struct_order.get(gates[gi].get("type", ""), 2))
            base_row = min(row_of.get(gi, 999) for gi in members_sorted)
            for ri, gi in enumerate(members_sorted):
                row_of[gi] = base_row + ri

        # Renumber rows within each column to be contiguous
        for d in cols:
            cols[d].sort(key=lambda gi: row_of.get(gi, 0))
        for d in cols:
            for ri, gi in enumerate(cols[d]):
                row_of[gi] = ri

        # ── Phase 2.4: Adder row-per-bit ordering ──
        # For multi-bit ripple adders, override row order so each bit
        # occupies its own row. Bit 0 at top, Bit N at bottom.
        # Within each column, gates are grouped by bit.
        if adder_bit_of:
            for gi, bit_idx in adder_bit_of.items():
                if gi in row_of:
                    row_of[gi] = bit_idx
            # Re-renumber after bit-based row assignment
            for d in cols:
                cols[d].sort(key=lambda gi: row_of.get(gi, 0))
            for d in cols:
                for ri, gi in enumerate(cols[d]):
                    row_of[gi] = ri

        # ── Phase 2.5: Compaction — tighten sparse adjacent columns ──
        # Build reverse map: column index → list of gate indices
        col_gates = {}
        for gi, ci in col_of.items():
            col_gates.setdefault(ci, []).append(gi)
        # Count wires between adjacent column pairs
        wire_count_between = {}  # (col_i, col_j) → wire count
        for gi, g in enumerate(gates):
            gi_col = col_of.get(gi)
            if gi_col is None:
                continue
            for inp in g["inputs"]:
                if inp in produced_by:
                    pi = produced_by[inp]
                    pi_col = col_of.get(pi)
                    if pi_col is not None and pi_col != gi_col:
                        key = (min(pi_col, gi_col), max(pi_col, gi_col))
                        wire_count_between[key] = wire_count_between.get(key, 0) + 1
        # Assign gap multipliers based on gate count AND wire density
        compacted_gap = {}  # old_col → gap multiplier
        sorted_cols = sorted(col_gates.keys())
        for i, col in enumerate(sorted_cols):
            n_gates = len(col_gates[col])
            # Check wire density to adjacent columns
            n_wires_left = wire_count_between.get((col - 1, col), 0) if col > min(sorted_cols) else 0
            n_wires_right = wire_count_between.get((col, col + 1), 0) if col < max(sorted_cols) else 0
            max_wires = max(n_wires_left, n_wires_right)
            # Only compact truly sparse columns with minimal wiring (≤1 wire crossing)
            if n_gates <= 1 and max_wires <= 1:
                compacted_gap[col] = 0.75
            elif n_gates == 0:
                compacted_gap[col] = 0.5  # empty column (unlikely but possible)
            else:
                compacted_gap[col] = 1.0

        # ── Phase 3: Y-position assignment with routing channels ──
        # Add extra spacing between rows for routing tracks
        max_gates_in_col = max(len(v) for v in cols.values()) if cols else 1
        ROW_SPACING = row_gap + channel_h  # add routing channels
        svg_h = max(max_gates_in_col, len(inputs), len(outputs)) * ROW_SPACING + 80

        # Compute compacted column X positions
        compacted_col_x = {}
        cx = 60 + (col_gap if inputs else 0)  # first gate column after input
        prev_col = -1
        for col in sorted(compacted_gap.keys()):
            if prev_col >= 0:
                # Use average of the two gaps
                gap = col_gap * (compacted_gap.get(prev_col, 1.0) + compacted_gap.get(col, 1.0)) / 2
                cx += gap
            compacted_col_x[col] = cx
            prev_col = col
        total_compacted_width = cx + col_gap + 100
        svg_w = max(total_cols * col_gap + 100, int(total_compacted_width))

        # Gate positions (X from compacted columns, Y from row index)
        gate_y = {}  # gate_index → y center
        for d in sorted(cols.keys()):
            for ri, gi in enumerate(cols[d]):
                gate_y[gi] = 50 + ri * ROW_SPACING + ROW_SPACING // 2
        # Compute compacted gate X positions
        gate_positions_temp = {}
        for gi, ci in col_of.items():
            gx = compacted_col_x.get(ci, 60 + ci * col_gap)
            gy = gate_y[gi]
            gate_positions_temp[gi] = (gx, gy)
        # Update svg_w from compacted width
        if gate_positions_temp:
            max_gx = max(gx for gx, _ in gate_positions_temp.values())
            svg_w = max(svg_w, int(max_gx + col_gap + 60))

        # Feedback bus: reserve space below all gates for routing feedback edges
        max_gate_y = max(gate_y.values()) + self.H // 2 if gate_y else 100
        FEEDBACK_BUS_Y = max_gate_y + 30
        FEEDBACK_TRACK_H = 10  # vertical spacing per feedback wire
        svg_h = max(svg_h, FEEDBACK_BUS_Y + len(feedback_edges) * FEEDBACK_TRACK_H + 40)

        # Build quick lookup: (to_gate_idx, input_wire_name) is feedback
        feedback_lookup = set()
        for (fg, tg, wn) in feedback_edges:
            feedback_lookup.add((tg, wn))

        # ── Channel routing: assign each wire a unique track ──
        # Track index → horizontal position between columns
        # We assign tracks per column-pair to avoid overlapping wires
        track_assignments = {}  # (from_name, to_gate_idx) → track_index
        col_track_counters = {}  # (from_col, to_col) → next_track

        def assign_track(from_name, to_gate_idx):
            """Assign a unique horizontal routing track for this wire."""
            from_pos = wire_pos(from_name)
            if not from_pos or to_gate_idx not in col_of:
                return 0
            from_col = from_pos[0]
            to_col = col_of[to_gate_idx]
            key = (from_col, to_col)
            track = col_track_counters.get(key, 0)
            col_track_counters[key] = track + 1
            return track

        # Pre-assign tracks for all connections (skip feedback edges)
        for gi, g in enumerate(gates):
            for inp in g["inputs"]:
                if (gi, inp) not in feedback_lookup:
                    assign_track(inp, gi)

        # ── Build SVG ──
        svg = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {svg_w} {svg_h}",
            "width": str(svg_w), "height": str(svg_h),
        })
        ET.SubElement(svg, "rect", {
            "width": str(svg_w), "height": str(svg_h),
            "fill": self.COLORS["bg"],
        })

        if title:
            ET.SubElement(svg, "text", {
                "x": str(svg_w // 2), "y": "24",
                "text-anchor": "middle", "fill": self.COLORS["title"],
                "font-family": self.FONT, "font-size": "14",
                "font-weight": "bold",
            }).text = title

        # ── Draw input ports ──
        port_positions = {}
        input_col_x = 60
        input_names = sorted(inputs, key=_input_sort)
        for ri, name in enumerate(input_names):
            y = 50 + ri * ROW_SPACING + ROW_SPACING // 2
            port_positions[name] = (input_col_x, y, True)
            self._draw_port(svg, input_col_x, y, name, True)
        # Update col_of_input with actual y positions
        for ri, name in enumerate(input_names):
            col_of_input[name] = (input_col_x if inputs else 0,
                                  50 + ri * ROW_SPACING + ROW_SPACING // 2)

        # ── Draw gates ──
        gate_positions = {}
        for d in sorted(cols.keys()):
            # Use compacted X if available, otherwise fall back to uniform grid
            for ri, gi in enumerate(cols[d]):
                g = gates[gi]
                gx = gate_positions_temp.get(gi, (60 + (d + (1 if inputs else 0)) * col_gap, 0))[0]
                gy = gate_y[gi]
                gate_positions[gi] = (gx, gy)
                self._draw_gate(svg, gx, gy, g["type"], g.get("label", ""))

                if g["type"] == "DFF" and len(g["inputs"]) >= 2:
                    nin = len(g["inputs"])
                    clk_off = (1 - (nin - 1) / 2) * self.IY
                    self._draw_clock_triangle(svg, gx, gy + clk_off)

                # Output port entry (used for wiring)
                out_name = g["output"]
                out_x = gx + self.W // 2 + self.PIN
                out_y = gy
                port_positions[out_name] = (out_x, out_y, False)

        # ── Phase 4.5: Output Alignment — ports at driving gate Y, per-column ──
        output_col_x, output_y_map = self._place_output_ports(
            svg, outputs, produced_by, gate_positions, gate_y,
            col_gap, ROW_SPACING, port_positions, col_of)

        # ── Draw wires with strict column-gap routing ──
        # Build sorted list of all safe vertical channels (gaps between gate columns)
        all_gate_x = set()
        for gx, gy in gate_positions.values():
            all_gate_x.add(gx)
        sorted_x = sorted(all_gate_x)
        # Safe channels: halfway between adjacent gate columns, plus edges
        safe_channels = []
        if inputs:
            safe_channels.append(60)  # input port column
        for i in range(len(sorted_x) - 1):
            safe_channels.append((sorted_x[i] + sorted_x[i+1]) / 2)
        # Output channel
        safe_channels.append(output_col_x - self.PORT_W // 2)

        safe_channels = sorted(set(safe_channels))

        # Build forbidden x-ranges (gate bodies + margin)
        forbidden = []
        for gx, gy in gate_positions.values():
            forbidden.append((gx - self.W // 2 - 6, gx + self.W // 2 + 6))

        def route_mid(px, gix):
            """Find a safe vertical channel between px and gix."""
            lo, hi = min(px, gix), max(px, gix)
            # Find channels strictly between lo and hi, excluding gate bodies
            candidates = [ch for ch in safe_channels
                          if lo + 10 < ch < hi - 10
                          and not any(fx1 < ch < fx2 for fx1, fx2 in forbidden)]
            if candidates:
                # Pick the channel closest to midpoint
                target = (lo + hi) / 2
                best = min(candidates, key=lambda ch: abs(ch - target))
                return best
            # Fallback: midpoint, but push outside gate if needed
            mid = (lo + hi) / 2
            for fx1, fx2 in forbidden:
                if fx1 < mid < fx2:
                    # Push to nearest edge
                    mid = fx2 + 4 if (mid - fx1) > (fx2 - mid) else fx1 - 4
            return mid

        gap_tracks = {}

        for gi, g in enumerate(gates):
            gx, gy = gate_positions[gi]
            nin = len(g["inputs"])

            for ii, inp_name in enumerate(g["inputs"]):
                if inp_name not in port_positions:
                    continue
                # Skip feedback edges — routed separately via dedicated bus
                if (gi, inp_name) in feedback_lookup:
                    continue

                px, py, _ = port_positions[inp_name]
                iy_off = (ii - (nin - 1) / 2) * self.IY
                gix = gx - self.W // 2 - self.PIN
                giy = gy + iy_off

                mid_x = route_mid(px, gix)
                ch_key = round(mid_x)
                track = gap_tracks.get(ch_key, 0)
                gap_tracks[ch_key] = track + 1
                mid_x += (track - 1) * 4

                self._route_with_detour(svg, gate_positions, px, py, mid_x, gix, giy)

        # ── Draw feedback edges through dedicated bus below all gates ──
        feedback_track = 0
        for (fg, tg, wn) in sorted(feedback_edges):
            if fg not in gate_positions or tg not in gate_positions:
                continue
            fgx, fgy = gate_positions[fg]
            tgx, tgy = gate_positions[tg]

            # Find which input of tg uses wire wn
            tg_gate = gates[tg]
            try:
                inp_idx = tg_gate["inputs"].index(wn)
            except ValueError:
                continue
            tg_iy_off = (inp_idx - (len(tg_gate["inputs"]) - 1) / 2) * self.IY

            # Start: from_gate output pin (right side)
            sx = fgx + self.W // 2 + self.PIN
            sy = fgy
            # End: to_gate input pin (left side)
            ex = tgx - self.W // 2 - self.PIN
            ey = tgy + tg_iy_off
            # Feedback bus Y level
            fy = FEEDBACK_BUS_Y + feedback_track * FEEDBACK_TRACK_H
            feedback_track += 1

            # Orthogonal route: out → down → left → up → in
            self._draw_wire_seg(svg, sx, sy, sx, fy)       # down to bus
            self._draw_wire_seg(svg, sx, fy, ex, fy)       # left along bus
            self._draw_wire_seg(svg, ex, fy, ex, ey)       # up to input pin

        # Output port connections are drawn by _place_output_ports (stub wires).
        return ET.tostring(svg, encoding="unicode")

    def _route_with_detour(self, svg, gate_positions, px, py, mid_x, gix, giy, channels=None, shared=None):
        """Orthogonal routing: H→V→H, detour if approach hits a gate."""
        hx1, hx2 = sorted([mid_x, gix])
        for (gx, gy) in gate_positions.values():
            gx1 = gx - self.W//2 - 4
            gx2 = gx + self.W//2 + 4
            gy1 = gy - self.H//2 - 4
            gy2 = gy + self.H//2 + 4
            if hx1 < gx2 and hx2 > gx1 and gy1 < giy < gy2:
                detour_y = gy2 + 8 if (giy - gy1) > (gy2 - giy) else gy1 - 8
                self._draw_wire_seg(svg, px, py, mid_x, py)
                self._draw_wire_seg(svg, mid_x, py, mid_x, detour_y)
                self._draw_wire_seg(svg, mid_x, detour_y, gix, detour_y)
                self._draw_wire_seg(svg, gix, detour_y, gix, giy)
                return
        self._draw_wire_seg(svg, px, py, mid_x, py)
        self._draw_wire_seg(svg, mid_x, py, mid_x, giy)
        self._draw_wire_seg(svg, mid_x, giy, gix, giy)

    def _draw_wire_seg(self, svg, x1, y1, x2, y2):
        """Draw a single straight wire segment."""
        ET.SubElement(svg, "line", {
            "x1": str(round(x1, 1)), "y1": str(round(y1, 1)),
            "x2": str(round(x2, 1)), "y2": str(round(y2, 1)),
            "stroke": self.COLORS["wire"],
            "stroke-width": "1.5",
        })

    def _draw_clock_triangle(self, svg, cx, cy):
        """在 DFF 左边缘画时钟三角标记（向内指向门体）。"""
        gate_left = cx - self.W // 2
        pin_x = gate_left - self.PIN
        tri_w = self.PIN  # 三角形宽度 = pin 长度
        tri_h = 5
        d = (f"M{pin_x},{cy - tri_h} "
             f"L{pin_x},{cy + tri_h} "
             f"L{gate_left},{cy} Z")
        ET.SubElement(svg, "path", {
            "d": d, "fill": "none",
            "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.2",
            "stroke-linejoin": "round",
        })

    # ── 门形状绘制 ─────────────────────────────────

    def _draw_gate(self, svg, cx, cy, gtype, label=""):
        shape, bubble = self.GATE_SHAPES.get(gtype, ("and", False))
        x = cx - self.W // 2
        y = cy - self.H // 2

        g = ET.SubElement(svg, "g")

        if shape == "and":
            # D-shape
            d = (f"M{x},{y + self.H} L{x},{y} "
                 f"A{self.W},{self.H // 2} 0 0,1 {x},{y + self.H} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })
        elif shape == "or":
            # Shield — 左(x)宽，右(x+W)尖
            r = self.W * 0.5
            d = (f"M{x},{y} "
                 f"Q{x + self.W * 0.5},{y + self.H * 0.15} {x + self.W},{cy} "
                 f"Q{x + self.W * 0.5},{y + self.H * 0.85} {x},{y + self.H} "
                 f"Q{x - r},{cy} {x},{y} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })
        elif shape == "xor":
            r = self.W * 0.5
            d = (f"M{x},{y} "
                 f"Q{x + self.W * 0.4},{y + self.H * 0.1} {x + self.W},{cy} "
                 f"Q{x + self.W * 0.4},{y + self.H * 0.9} {x},{y + self.H} "
                 f"Q{x - r},{cy} {x},{y} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })
            # XOR 额外输入弧线
            ed = (f"M{x - self.W * 0.02},{y} "
                  f"Q{x - r * 0.7},{cy} {x - self.W * 0.02},{y + self.H}")
            ET.SubElement(g, "path", {
                "d": ed, "fill": "none",
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.2",
            })
        elif shape in ("not", "buf"):
            # Triangle
            d = (f"M{x},{y} L{x},{y + self.H} L{x + self.W * 0.7},{cy} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })
        elif shape == "dff":
            # Rectangular box (standard sequential element symbol)
            ET.SubElement(g, "rect", {
                "x": str(x), "y": str(y), "width": str(self.W), "height": str(self.H),
                "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
            })
        elif shape == "mux":
            # Trapezoid: wide left (inputs), narrow right (output)
            left_margin = 5
            right_margin = self.H * 0.25
            d = (f"M{x},{y + left_margin} "
                 f"L{x},{y + self.H - left_margin} "
                 f"L{x + self.W},{y + self.H - right_margin} "
                 f"L{x + self.W},{y + right_margin} Z")
            ET.SubElement(g, "path", {
                "d": d, "fill": self.COLORS["gate_fill"],
                "stroke": self.COLORS["gate_stroke"], "stroke-width": "1.5",
                "stroke-linejoin": "round",
            })
            # "0" / "1" 标签 (标记 sel=0 选哪个输入, sel=1 选哪个)
            nin = 2  # 数据输入: A, B
            for ii in range(nin):
                iy_off = (ii - (nin - 1) / 2) * self.IY
                ET.SubElement(g, "text", {
                    "x": str(x + 8), "y": str(cy + iy_off + 3),
                    "text-anchor": "start", "fill": self.COLORS["text"],
                    "font-family": self.FONT, "font-size": "7",
                }).text = str(ii)
            # sel 标签在底部
            ET.SubElement(g, "text", {
                "x": str(cx), "y": str(y + self.H + 11),
                "text-anchor": "middle", "fill": self.COLORS["port_stroke"],
                "font-family": self.FONT, "font-size": "7",
            }).text = "sel"

        # 气泡 (在输出侧，右侧)
        if bubble:
            bx = x + self.W + 6 if shape != "not" else x + self.W * 0.8
            ET.SubElement(g, "circle", {
                "cx": str(bx), "cy": str(cy), "r": "4",
                "fill": "none", "stroke": self.COLORS["gate_stroke"],
                "stroke-width": "1.2",
            })

        # 标签
        if label:
            ET.SubElement(g, "text", {
                "x": str(cx), "y": str(cy + 2), "text-anchor": "middle",
                "fill": self.COLORS["text"], "font-family": self.FONT,
                "font-size": "8", "dy": "0.3em",
            }).text = label
        else:
            ET.SubElement(g, "text", {
                "x": str(cx), "y": str(cy + 2), "text-anchor": "middle",
                "fill": self.COLORS["text"], "font-family": self.FONT,
                "font-size": "8", "dy": "0.3em",
            }).text = gtype  # full gate type name (AND, NAND, XOR, XNOR, etc.)

    # ── Output Alignment ───────────────────────────

    def _place_output_ports(self, svg, outputs, produced_by,
                             gate_positions, gate_y, col_gap,
                             ROW_SPACING, port_positions, col_of=None):
        """Phase 4.5: Place output ports at their driver gate's column edge.

        Each output port is placed at its driver gate's center Y, at the right
        edge of the gate's OWN column (not at the global right edge). This
        makes ripple counters, adders, etc. visually associate each output with
        its bit-slice column.
        """
        max_gate_x = max(gx for gx, _ in gate_positions.values()) if gate_positions else 400
        global_output_x = max_gate_x + col_gap * 0.3

        # Build column → x-position map from gate positions
        col_x_map = {}
        if col_of:
            for gi, (gx, _) in gate_positions.items():
                ci = col_of.get(gi)
                if ci is not None:
                    # Store the rightmost gate x in this column
                    cur = col_x_map.get(ci, 0)
                    col_x_map[ci] = max(cur, gx)

        gate_output_count = {}
        used_positions = {}  # (x, y) → gate_index
        output_y_map = {}

        for name in sorted(outputs):
            if name in produced_by:
                gi = produced_by[name]
                driver_gy = gate_y.get(gi, 0)
                # Place port at driver's column right edge, or global right edge
                ci = col_of.get(gi) if col_of else None
                port_x = col_x_map.get(ci, global_output_x) + col_gap * 0.25 if ci is not None else global_output_x
                count = gate_output_count.get(gi, 0)
                gate_output_count[gi] = count + 1
                y = driver_gy + count * (self.PORT_H + 2)
            else:
                port_x = global_output_x
                y = 50 + len(output_y_map) * ROW_SPACING + ROW_SPACING // 2

            # Collision check at same (x, y)
            used_key = (round(port_x), round(y))
            y_try = y
            while used_key in used_positions and used_positions[used_key] != gi:
                y_try += self.PORT_H + 2
                used_key = (round(port_x), round(y_try))
            used_positions[used_key] = gi
            y = y_try
            output_y_map[name] = y
            port_positions[name] = (port_x, y, False)
            self._draw_port(svg, port_x, y, name, False)

            # Stub wire from gate output pin to port
            if name in produced_by:
                gi = produced_by[name]
                gx, gy = gate_positions[gi]
                sx = gx + self.W // 2 + self.PIN
                sy = gy
                ex = port_x - self.PORT_W // 2
                self._draw_wire_seg(svg, sx, sy, ex, sy)  # horizontal to port
                if abs(sy - y) > 2:
                    self._draw_wire_seg(svg, ex, sy, ex, y)  # vertical to port Y

        # Return max output x for SVG width calculation
        return max(global_output_x,
                   max((x for x, _ in used_positions.keys()), default=global_output_x)), output_y_map

    # ── 端口 ────────────────────────────────────────

    def _draw_port(self, svg, x, y, name, is_input):
        pw, ph = self.PORT_W, self.PORT_H
        if is_input:
            px = x - pw // 2
        else:
            px = x - pw // 2
        py = y - ph // 2

        ET.SubElement(svg, "rect", {
            "x": str(px), "y": str(py), "width": str(pw), "height": str(ph),
            "rx": "4", "fill": self.COLORS["port_fill"],
            "stroke": self.COLORS["port_stroke"], "stroke-width": "1.2",
        })
        ET.SubElement(svg, "text", {
            "x": str(x), "y": str(y + 2), "text-anchor": "middle",
            "fill": self.COLORS["port_stroke"], "font-family": self.FONT,
            "font-size": "10", "dy": "0.35em", "font-weight": "bold",
        }).text = name

    # ── 连线 ────────────────────────────────────────

    _wire_seq = 0  # 全局连线序号，用于错开避免重叠

    def _draw_wire(self, svg, x1, y1, x2, y2):
        """画正交连线（水平→垂直→水平），自动错开避免重叠。"""
        LogicSVG._wire_seq += 1
        spread = (LogicSVG._wire_seq % 5 - 2) * self.WIRE_SPREAD * 0.3
        mid_x = (x1 + x2) / 2 + spread
        d = f"M{x1},{y1} L{mid_x},{y1} L{mid_x},{y2} L{x2},{y2}"
        ET.SubElement(svg, "path", {
            "d": d, "fill": "none", "stroke": self.COLORS["wire"],
            "stroke-width": "1.5", "stroke-linejoin": "round",
        })

    # ── 布局质量分析 ────────────────────────────────

    @staticmethod
    def score_layout_quality(svg_content: str) -> dict:
        """Analyze rendered SVG for layout/wiring quality issues.

        Checks:
          - Wire crossings (intersecting orthogonal wire segments)
          - Wire-to-gate clearance (wires passing through gate bodies)
          - Gate-gate overlap
          - Wire density / congestion

        Returns:
          {"score": 0-10, "crossings": int, "overlaps": int,
           "issues": [str], "details": [str]}
        """
        import re
        import math

        result = {"score": 10.0, "crossings": 0, "overlaps": 0,
                  "issues": [], "details": []}

        try:
            root = ET.fromstring(svg_content)
        except Exception:
            result["score"] = 10.0
            result["issues"].append("Could not parse SVG for layout analysis")
            return result

        # ── Collect elements ──
        wires = []       # [(x1,y1, x2,y2, mid_x, mid_y), ...] — 3-segment paths
        gates = []       # [(x1,y1, x2,y2, cx,cy, gtype), ...]
        ports = []       # [(x1,y1, x2,y2, name, is_input), ...]

        ns = "http://www.w3.org/2000/svg"

        # Gate shapes: paths/rects with fill != "none" (in groups)
        for g_elem in root.findall(f".//{{{ns}}}g"):
            gate_type = ""
            for t in g_elem.findall(f".//{{{ns}}}text"):
                gate_type = (t.text or "").strip()
                break
            # Path-based gates (AND, OR, XOR, NOT, MUX)
            for p in g_elem.findall(f".//{{{ns}}}path"):
                fill = p.get("fill", "")
                if fill and fill != "none":
                    bbox = LogicSVG._path_bbox(p.get("d", ""))
                    if bbox:
                        x1, y1, x2, y2 = bbox
                        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                        gates.append((x1, y1, x2, y2, cx, cy, gate_type))
            # Rect-based gates (DFF)
            for r in g_elem.findall(f".//{{{ns}}}rect"):
                fill = r.get("fill", "")
                if fill and fill != "none" and not r.get("rx"):
                    rx = float(r.get("x", 0))
                    ry = float(r.get("y", 0))
                    rw = float(r.get("width", 0))
                    rh = float(r.get("height", 0))
                    cx, cy = rx + rw / 2, ry + rh / 2
                    gates.append((rx, ry, rx + rw, ry + rh, cx, cy, gate_type))

        # Port rects
        for r in root.findall(f".//{{{ns}}}rect"):
            rx = float(r.get("x", 0))
            ry = float(r.get("y", 0))
            rw = float(r.get("width", 0))
            rh = float(r.get("height", 0))
            if r.get("rx"):  # has border-radius → port
                name = ""
                for t in root.findall(f".//{{{ns}}}text"):
                    tx = float(t.get("x", 0))
                    ty = float(t.get("y", 0))
                    if abs(tx - (rx + rw / 2)) < rw and abs(ty - (ry + rh / 2)) < rh:
                        name = (t.text or "").strip()
                        break
                is_input = rx < 200  # heuristic: left side = input
                ports.append((rx, ry, rx + rw, ry + rh, name, is_input))

        # Wire lines: stroke="#7c3aed" <line> elements
        # Treat each segment independently for crossing detection
        for elem in root.findall(f".//{{{ns}}}line"):
            stroke = elem.get("stroke", "")
            if "#7c3aed" in (stroke or ""):
                x1 = float(elem.get("x1", 0))
                y1 = float(elem.get("y1", 0))
                x2 = float(elem.get("x2", 0))
                y2 = float(elem.get("y2", 0))
                mid_x = (x1 + x2) / 2
                mid_y = (y1 + y2) / 2
                wires.append((x1, y1, x2, y2, mid_x, mid_y))

        # Also handle legacy <path> wires
        for p in root.findall(f".//{{{ns}}}path"):
            fill = p.get("fill", "")
            stroke = p.get("stroke", "")
            if fill == "none" and "#7c3aed" in (stroke or ""):
                d = p.get("d", "")
                segs = LogicSVG._parse_wire_segments(d)
                if segs:
                    wires.append(segs)

        # ── Check 1: Wire-wire crossings (segment-level) ──
        for i, w1 in enumerate(wires):
            for j, w2 in enumerate(wires):
                if j <= i:
                    continue
                if LogicSVG._wires_cross(w1, w2):
                    result["crossings"] += 1

        # ── Check 2: Wire through gate ──
        wire_ov = 0
        for w in wires:
            for g in gates:
                if LogicSVG._wire_hits_rect(w, g[:4]):
                    wire_ov += 1
        result["overlaps"] += wire_ov

        # ── Check 3: Gate-gate overlap ──
        gate_ov = 0
        for i, g1 in enumerate(gates):
            for j, g2 in enumerate(gates):
                if j <= i:
                    continue
                if LogicSVG._rects_overlap(g1[:4], g2[:4]):
                    gate_ov += 1
        result["overlaps"] += gate_ov
        result["details"].append(f"wire_overlaps={wire_ov}, gate_overlaps={gate_ov}")

        # ── Metrics ──
        n_gates = max(len(gates), 1)
        n_inputs = len(ports)
        n_outputs = sum(1 for p in ports if not p[5])

        wire_lengths = [((w[2]-w[0])**2 + (w[3]-w[1])**2)**0.5 for w in wires]
        avg_wire_len = sum(wire_lengths) / len(wire_lengths) if wire_lengths else 0

        gate_xs = sorted(set((g[0] + g[2]) / 2 for g in gates))
        n_columns = len(gate_xs) if gate_xs else 1
        n_logical = len(wires)

        # Gate types
        gate_types = {}
        for g in gates:
            gt = g[6] or "unknown"
            gate_types[gt] = gate_types.get(gt, 0) + 1

        # Canvas
        canvas_w = int(root.get("width", 0))
        canvas_h = int(root.get("height", 0))
        aspect_ratio = canvas_w / max(canvas_h, 1)

        # Span
        gxs = [(g[0] + g[2]) / 2 for g in gates]
        gate_span_x = max(gxs) - min(gxs) if gxs else 0
        gate_span_y = max(g[1] for g in gates) - min(g[1] for g in gates) if gates else 0

        # ── Weighted scoring: cross+overlap 0.60, density 0.20, wire_len/hierarchy/fanout/aspect 0.05 each ──
        import math

        # 1. Cross+overlap score (0.60): cross weight 3, overlap weight 4
        # Scale crossings by sqrt(n_gates) so larger circuits aren't
        # overly penalised (a 28-gate adder naturally has more crossings
        # than an 8-gate counter).
        complexity_norm = max(n_gates * math.sqrt(max(n_gates, 1)), 1.0)
        cross_ratio = result["crossings"] / complexity_norm
        overlap_ratio = result["overlaps"] / max(n_gates * 0.5, 1.0)
        cross_sub = 10 * math.exp(-cross_ratio / 2.0)
        overlap_sub = 10 * math.exp(-overlap_ratio / 0.5)
        cross_score = (3 * cross_sub + 4 * overlap_sub) / 7

        # 2. Wire length score (0.20)
        norm_len = avg_wire_len / max(canvas_w, 1)
        wire_score = max(0, 10 - norm_len * 6)

        # 3. Gate density score (0.15)
        density = n_gates / max(canvas_w * canvas_h, 1) * 1e6
        density_score = max(0, 10 - density * 0.015)

        # 4. Hierarchy score (0.15)
        if 2 <= n_columns <= 14:
            hierarchy_score = 10
        elif n_columns <= 28:
            hierarchy_score = 8
        else:
            hierarchy_score = max(5, 10 - (n_columns - 26) * 0.3)

        # 5. Fanout score (0.10)
        wires_per_gate = n_logical / max(n_gates, 1)
        fanout_score = 10 * math.exp(-wires_per_gate / 100.0)

        # 6. Aspect score (0.10)
        if 0.6 <= aspect_ratio <= 5.0:
            aspect_score = 10
        elif 0.3 <= aspect_ratio <= 8.0:
            aspect_score = 7
        else:
            aspect_score = 5

        result["score"] = (
            0.60 * cross_score +
            0.05 * wire_score +
            0.20 * density_score +
            0.05 * hierarchy_score +
            0.05 * fanout_score +
            0.05 * aspect_score
        )
        result["score"] = max(0.0, min(10.0, result["score"]))

        result["metrics"] = {
            "gates": n_gates,
            "gate_types": gate_types,
            "inputs": n_inputs,
            "outputs": n_outputs,
            "canvas": f"{canvas_w}x{canvas_h}",
            "aspect_ratio": round(aspect_ratio, 2),
            "logic_wires": n_logical,
            "columns": n_columns,
            "avg_wire_len": round(avg_wire_len, 1),
            "crossings": result["crossings"],
            "overlaps": result["overlaps"],
        }

        result["summary"] = (
            f"Layout: {n_gates} gates on {canvas_w}x{canvas_h} canvas, "
            f"aspect {aspect_ratio:.2f}, {n_inputs} inputs/{n_outputs} outputs. "
            f"Logic wires: {n_logical}, avg length {avg_wire_len:.0f}px, "
            f"{n_columns} columns. "
            f"Issues: {result['crossings']} crossings, {result['overlaps']} overlaps. "
            f"Gate types: {gate_types}. "
            f"Score: {result['score']:.0f}/10."
        )

        if result["crossings"] > 0:
            result["issues"].append(f"{result['crossings']} wire crossing(s)")
        if result["overlaps"] > 0:
            result["issues"].append(f"{result['overlaps']} wire/gate overlap(s)")
        if result["crossings"] == 0 and result["overlaps"] == 0:
            result["issues"].append("Clean layout — no issues")

        return result

    @staticmethod
    def _path_bbox(d: str) -> tuple | None:
        """Approximate bounding box of an SVG path.

        Filters out arc/curve control params that aren't real coordinates.
        Only considers points after M, L, or at path start.
        """
        import re
        # Split into segments: M, L, A, Q, C commands
        # Simple approach: find all coordinate pairs (x,y) near M/L commands
        # and skip A's extra params (rx, ry, rotation, large-arc, sweep)
        raw_nums = [float(x) for x in re.findall(r"[-]?\d+\.?\d*", d)]
        if len(raw_nums) < 2:
            return None

        # Parse the path to extract actual points
        tokens = re.findall(r'[A-Za-z]|[-]?\d+\.?\d*', d)
        points = []
        i = 0
        x, y = 0, 0
        while i < len(tokens):
            t = tokens[i]
            if t in 'ML':  # MoveTo, LineTo: next 2 numbers are (x,y)
                if i + 2 < len(tokens):
                    x = float(tokens[i+1])
                    y = float(tokens[i+2])
                    points.append((x, y))
                    i += 3
                else:
                    i += 1
            elif t in 'ACQ':  # Arc/Curve: skip extra params, last 2 are (x,y)
                # Find the (x,y) at end: work backwards
                nums_after = [float(x) for x in tokens[i+1:] if re.match(r'[-]?\d', x)]
                if len(nums_after) >= 2:
                    x = nums_after[-2]
                    y = nums_after[-1]
                    points.append((x, y))
                i = len(tokens)  # skip rest
            elif t == 'Z':  # ClosePath
                i += 1
            else:
                # Number: part of previous command
                i += 1

        if not points:
            # Fallback: use raw nums
            xs = [raw_nums[i] for i in range(0, len(raw_nums), 2) if i+1 < len(raw_nums)]
            ys = [raw_nums[i] for i in range(1, len(raw_nums), 2) if i < len(raw_nums)]
            if xs and ys:
                return (min(xs), max(0, min(ys)), max(xs), max(ys))
            return None

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _parse_wire_segments(d: str) -> list | None:
        """Parse an orthogonal wire path into segments.

        Format: M x1,y1 L mx,y1 L mx,y2 L x2,y2
        Returns: (x1,y1, x2,y2, mid_x, mid_y) or None
        """
        import re
        nums = [float(x) for x in re.findall(r"[-]?\d+\.?\d*", d)]
        if len(nums) < 8:
            return None
        # Points: (x1,y1), (mx1,y1), (mx2,y2), (x2,y2)
        x1, y1 = nums[0], nums[1]
        x2, y2 = nums[6], nums[7]
        mx = nums[2]  # mid x
        my = nums[5]  # mid y
        return (x1, y1, x2, y2, mx, my)

    @staticmethod
    def _wires_cross(w1: tuple, w2: tuple) -> bool:
        """Check if two wire segments cross (one horizontal, one vertical)."""
        x1a, y1a, x2a, y2a, mxa, mya = w1
        x1b, y1b, x2b, y2b, mxb, myb = w2

        # Don't count crossing if wires share an endpoint (fan-out / junction)
        TOL = 15
        eps_a = [(x1a, y1a), (x2a, y2a)]
        eps_b = [(x1b, y1b), (x2b, y2b)]
        for ax, ay in eps_a:
            for bx, by in eps_b:
                if abs(ax - bx) < TOL and abs(ay - by) < TOL:
                    return False

        # Only check horizontal vs vertical segments
        segs = []
        for x1, y1, x2, y2 in [(x1a, y1a, x2a, y2a), (x1b, y1b, x2b, y2b)]:
            if abs(x1 - x2) > abs(y1 - y2):
                segs.append(("h", min(x1, x2), max(x1, x2), y1))
            else:
                segs.append(("v", x1, min(y1, y2), max(y1, y2)))

        s1, s2 = segs
        if s1[0] == s2[0]:
            return False

        h_seg = s1 if s1[0] == "h" else s2
        v_seg = s1 if s1[0] == "v" else s2

        _, hx1, hx2, hy = h_seg
        _, vx, vy1, vy2 = v_seg

        return hx1 < vx < hx2 and vy1 < hy < vy2

    @staticmethod
    def _wire_hits_rect(wire: tuple, rect: tuple) -> bool:
        """Check if wire passes through a gate body (not connecting to it)."""
        x1, y1, x2, y2 = wire[0], wire[1], wire[2], wire[3]
        rx1, ry1, rx2, ry2 = rect
        TOL = 50
        for ex, ey in [(x1, y1), (x2, y2)]:
            if rx1 - TOL <= ex <= rx2 + TOL and ry1 - TOL <= ey <= ry2 + TOL:
                return False
        return LogicSVG._seg_intersects_rect(x1, y1, x2, y2, rx1, ry1, rx2, ry2)

    @staticmethod
    def _seg_intersects_rect(sx1, sy1, sx2, sy2, rx1, ry1, rx2, ry2) -> bool:
        """Check if a line segment intersects an axis-aligned rectangle."""
        # Expand rect slightly for tolerance
        margin = 2
        rx1 -= margin; ry1 -= margin
        rx2 += margin; ry2 += margin
        # Quick reject: segment entirely outside rect
        if max(sx1, sx2) < rx1 or min(sx1, sx2) > rx2:
            return False
        if max(sy1, sy2) < ry1 or min(sy1, sy2) > ry2:
            return False
        # Horizontal segment crossing rect
        if abs(sy1 - sy2) < 0.5:  # horizontal
            return (ry1 <= sy1 <= ry2 and
                    min(sx1, sx2) <= rx2 and max(sx1, sx2) >= rx1)
        # Vertical segment crossing rect
        if abs(sx1 - sx2) < 0.5:  # vertical
            return (rx1 <= sx1 <= rx2 and
                    min(sy1, sy2) <= ry2 and max(sy1, sy2) >= ry1)
        # Diagonal: rough box intersection
        return not (max(sx1, sx2) < rx1 or min(sx1, sx2) > rx2 or
                    max(sy1, sy2) < ry1 or min(sy1, sy2) > ry2)

    @staticmethod
    def _rects_overlap(r1: tuple, r2: tuple) -> bool:
        """Check if two rectangles overlap."""
        return not (r1[2] < r2[0] or r2[2] < r1[0] or
                    r1[3] < r2[1] or r2[3] < r1[1])
