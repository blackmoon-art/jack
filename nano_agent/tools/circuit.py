"""电路图工具 — 基于 schemdraw 渲染专业电路图。

支持语法:
  - 串联: A -> B -> C
  - 并联: [branch1, branch2]  (分支在两端汇合)
  - 节点命名: component(value) as NODE_NAME
  - 多链: chain1 ; chain2  (用分号分隔独立链路)
  - 节点引用: NODE_NAME -> component  (从已命名节点继续画)
  - 锚点连接: NODE_NAME.emitter -> component  (从晶体管的发射极继续)
            component@anchor  (以指定锚点连接新元件)
  - 方向控制: up, down  (改变后续元件绘制方向, right 恢复默认)
  - 编号元件: r1→resistor, c2→capacitor, led1→LED (自动匹配)

滤波器示例:
  RC 低通: ac(Vin) -> resistor(1k) as n1 -> capacitor(10n) -> ground ; n1 -> line -> open(Vout)

差分放大器示例:
  ac(Vin+) -> npn(Q1)@base as q1 ;
  ac(Vin-) -> npn(Q2)@base as q2 ;
  q1.emitter -> line -> q2.emitter ;
  q1.emitter -> down -> isource(1mA) -> ground ;
  q1.collector -> up -> resistor(10k) as rc1 -> line -> v(VCC) ;
  q2.collector -> up -> resistor(10k) as rc2 -> line -> v(VCC) ;
  rc1.end -> wire -> rc2.end ;
  q1.collector -> right -> open(Vout+) ;
  q2.collector -> right -> open(Vout-)

元件锚点:
  npn/pnp/transistor: base, emitter, collector
  opamp: in1, in2, out, vdd, vss
  所有元件: start, end
"""

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.circuit")


# ── 合法元件名（公开给 LLM）─────────────────────────────
_VALID_COMPONENT_NAMES = (
    # 基础无源元件
    "ac", "v", "battery", "source", "signal",
    "resistor", "r",
    "capacitor", "c",
    "inductor", "l",
    "diode", "d",
    "led",
    "switch", "spst",
    "ground", "gnd",
    "antenna",
    # 有源元件
    "opamp",
    "transistor", "npn", "pnp",
    "isource", "current_source",
    # 信号处理框图元件 (方块图/系统框图)
    "mixer", "lna", "amp", "amplifier",
    "adc", "dac",
    "oscillator", "lo",
    "filter_box", "filter",
    "block", "port", "terminal",
    "combiner", "splitter", "rf",
    # 连接 / 装饰
    "fuse", "lamp", "motor", "speaker", "microphone",
    "line", "wire", "open", "dot",
)

_VALID_NAMES_STR = ", ".join(sorted(set(_VALID_COMPONENT_NAMES)))

# ── 方向伪元件 ────────────────────────────────────────
_DIRECTIONS = {"up", "down", "left", "right"}
_BLOCK_FACTORY = "__block__"  # sentinel for block diagram box elements


class _AnchorRef:
    """让 Anchor 伪装成有 .end 的元素。"""
    def __init__(self, anchor):
        self.end = anchor


class Circuit:
    TOOLS = [
        ("draw_circuit",
         "Draw professional circuit diagrams and signal-processing block diagrams. "
         "For: circuit schematics, filter circuits, differential amplifiers, "
         "RF/signal chains (block diagrams), wiring layouts.\n"
         "\n"
         "**Valid components:** " + _VALID_NAMES_STR + "\n"
         "Block-diagram elements (LNA, mixer, ADC, amp, filter_box, etc.) draw as labeled boxes.\n"
         "Numbered variants (r1, c2, led1, etc.) auto-match to base names.\n"
         "\n"
         "**Syntax:**\n"
         "- Series: `A -> B -> C`\n"
         "- Parallel: `[branch1, branch2]`\n"
         "- Named nodes: `comp(val) as N1`\n"
         "- Multi-chain: `chain1 ; chain2`\n"
         "- Anchor refs: `N1.emitter -> ...` or `comp@base`\n"
         "- Direction: `up`, `down` change direction; `right` restores default\n"
         "\n"
         "**Filter example:**\n"
         "`ac(Vin) -> resistor(1k) as n1 -> capacitor(10n) -> ground ; n1 -> line -> open(Vout)`\n"
         "\n"
         "**Diff-amp example:**\n"
         "`ac(Vin+) -> npn(Q1)@base as q1 ; ac(Vin-) -> npn(Q2)@base as q2 ; "
         "q1.emitter -> line -> q2.emitter ; "
         "q1.emitter -> down -> isource(1mA) -> ground ; "
         "q1.collector -> up -> resistor(10k) as rc1 -> line -> v(VCC) ; "
         "q2.collector -> up -> resistor(10k) as rc2 -> line -> v(VCC) ; "
         "rc1.end -> wire -> rc2.end`\n"
         "\n"
         "**Signal chain block diagram (e.g. FMCW radar IF):**\n"
         "`rf(RF_in) -> lna(LNA) -> mixer as m1 ; "
         "lo(f0) -> down -> m1 ; "
         "m1 -> amp(IF_Amp) -> filter_box(LPF) -> adc(ADC) -> port(DSP)`",
         "draw_circuit",
         {"description": {"type": "string",
                          "description":
                          "Circuit or block diagram description. "
                          "Valid names: " + _VALID_NAMES_STR + ". "
                          "Block elements (mixer,lna,amp,adc,filter_box etc) auto-draw as labeled boxes. "
                          "Series with '->', parallel with '[b1,b2]', named nodes with 'as N1', "
                          "multi-chain with ';', directions with up/down/left/right. "
                          "Signal chain: 'rf(In) -> lna(LNA) -> mixer as m1 ; lo(f0) -> m1 ; m1 -> filter_box(LPF) -> adc(ADC) -> port(Out)'"},
          "title": {"type": "string", "description": "Circuit title (optional)"}},
         ["description"]),
    ]

    def __init__(self, work_dir: str, charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            web_static = Path(__file__).parent.parent.parent / "web" / "static"
            self.charts_dir = web_static / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    # ── 元件名解析 ──────────────────────────────────────────

    @staticmethod
    def _resolve_comp_name(name: str, comp_map: dict):
        """解析元件名，支持编号后缀（r1→r, c2→c）和别名。"""
        if name in comp_map:
            return comp_map[name]
        stripped = name.rstrip("0123456789")
        if stripped and stripped != name and stripped in comp_map:
            return comp_map[stripped]
        return None, {}

    @staticmethod
    def _parse_comp(comp_str: str) -> tuple:
        """解析单个元件字符串。

        'resistor(100Ω) as N1'  → ('resistor', '100Ω', 'resistor(100Ω)', 'N1', None)
        'npn(Q1)@base as q1'    → ('npn', 'Q1', 'npn(Q1)', 'q1', 'base')
        'ground'                → ('ground', '', 'ground', None, None)
        """
        node_name = None
        comp_anchor = None
        c = comp_str.strip()

        # 提取 as NAME 后缀
        if " as " in c:
            c, node_name = c.rsplit(" as ", 1)
            c, node_name = c.strip(), node_name.strip()

        # 提取 @anchor 后缀
        if "@" in c:
            c, comp_anchor = c.rsplit("@", 1)
            c, comp_anchor = c.strip(), comp_anchor.strip()

        if "(" in c and ")" in c:
            n = c.split("(")[0].strip().lower()
            v = c[c.index("(") + 1:c.index(")")].strip()
            return n, v, c, node_name, comp_anchor
        return c.lower(), "", c, node_name, comp_anchor

    # ── 主入口 ──────────────────────────────────────────────

    def draw_circuit(self, description: str, title: str = "") -> str:
        """用 schemdraw 绘制电路图。"""
        import schemdraw
        import schemdraw.elements as elm
        from schemdraw import Drawing

        try:
            d = Drawing(show=False)
            if title:
                d.config(fontsize=14)

            comp_map = {
                # 基础元件
                "battery": (elm.Battery, {}),
                "v": (elm.SourceV, {}),
                "source": (elm.SourceV, {}),
                "signal": (elm.SourceV, {}),
                "ac": (self._get_ac_source(elm), {}),
                "resistor": (elm.Resistor, {}),
                "r": (elm.Resistor, {}),
                "capacitor": (elm.Capacitor, {}),
                "c": (elm.Capacitor, {}),
                "inductor": (elm.Inductor, {}),
                "l": (elm.Inductor, {}),
                "diode": (elm.Diode, {}),
                "d": (elm.Diode, {}),
                "led": (elm.LED, {}),
                "switch": (elm.Switch, {}),
                "spst": (elm.Switch, {}),
                "ground": (elm.Ground, {}),
                "gnd": (elm.Ground, {}),
                "antenna": (elm.Antenna, {}),
                # 有源元件
                "opamp": (elm.Opamp, {}),
                "transistor": (elm.BjtNpn, {}),
                "npn": (elm.BjtNpn, {}),
                "pnp": (elm.BjtPnp, {}),
                "isource": (elm.SourceI, {}),
                "current_source": (elm.SourceI, {}),
                # 信号处理框图元件 (BLOCK_FACTORY sentinel → _make_box)
                "mixer":       (_BLOCK_FACTORY, {}),
                "lna":         (_BLOCK_FACTORY, {}),
                "amp":         (_BLOCK_FACTORY, {}),
                "amplifier":   (_BLOCK_FACTORY, {}),
                "adc":         (_BLOCK_FACTORY, {}),
                "dac":         (_BLOCK_FACTORY, {}),
                "oscillator":  (_BLOCK_FACTORY, {}),
                "lo":          (_BLOCK_FACTORY, {}),
                "filter_box":  (_BLOCK_FACTORY, {}),
                "filter":      (_BLOCK_FACTORY, {}),
                "block":       (_BLOCK_FACTORY, {}),
                "port":        (_BLOCK_FACTORY, {}),
                "terminal":    (_BLOCK_FACTORY, {}),
                "combiner":    (_BLOCK_FACTORY, {}),
                "splitter":    (_BLOCK_FACTORY, {}),
                "rf":          (_BLOCK_FACTORY, {}),
                # 连接 / 装饰
                "fuse": (elm.Fuse, {}),
                "lamp": (elm.Lamp, {}),
                "motor": (elm.Motor, {}),
                "speaker": (elm.Speaker, {}),
                "microphone": (elm.Mic, {}),
                "line": (elm.Line, {}),
                "wire": (elm.Line, {}),
                "open": (elm.Dot, {}),
                "dot": (elm.Dot, {}),
            }

            named: dict[str, object] = {}  # NAME → schemdraw element
            last = None
            direction = "right"

            chains = self._split_chains(description)
            if not chains:
                return "Error: no components in circuit description"

            for chain_desc in chains:
                last, direction = self._draw_chain(
                    chain_desc, comp_map, named, last, d, elm, direction)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"circuit_{ts}.png"
            filepath = self.charts_dir / filename

            d.save(str(filepath))
            logger.info(f"Circuit saved: {filename}")

            img_url = f"/charts/{filename}"
            title_alt = title or "Circuit"
            return f"![{title_alt}]({img_url})\n{img_url}"

        except ImportError:
            return "Error: schemdraw is not installed. Run: pip install schemdraw"
        except Exception as e:
            logger.exception(f"Circuit drawing failed: {e}")
            return f"Error drawing circuit: {e}"

    # ── 链拆分 ──────────────────────────────────────────────

    @staticmethod
    def _split_chains(description: str) -> list:
        """按 ; 拆分链路，括号内分号不误拆。"""
        chains = []
        current = ""
        depth = 0
        for ch in description:
            if ch == ";" and depth == 0:
                c = current.strip()
                if c:
                    chains.append(c)
                current = ""
            else:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth = max(0, depth - 1)
                current += ch
        c = current.strip()
        if c:
            chains.append(c)
        return chains

    # ── 锚点引用解析 ────────────────────────────────────────

    @staticmethod
    def _parse_node_ref(first_token: str, named: dict):
        """解析链首的节点引用，支持锚点语法。

        'N1'         → (element, None)
        'N1.emitter' → (AnchorRef, None)
        返回 (last_obj, remaining_prefix_to_strip) 或 (None, None)
        """
        token = first_token.rstrip("->").strip()
        if not token:
            return None, None

        if "." in token:
            node_name, anchor_name = token.split(".", 1)
            if node_name in named:
                elem = named[node_name]
                try:
                    anchor = getattr(elem, anchor_name)
                except AttributeError:
                    anchor = elem.end
                return _AnchorRef(anchor), token
        elif token in named:
            return named[token], token
        return None, None

    # ── 单链绘制 ────────────────────────────────────────────

    def _try_connect_node(self, part_data: str, named: dict,
                          last, d, elm) -> tuple:
        """如果是节点引用，画线连接到该节点。返回 (new_last, was_handled)。

        支持:
          - 简单引用: n2 → 连线到 n2.end (或 .center)
          - 锚点引用: q2.emitter → 连线到 q2 的 emitter 锚点
        """
        token = part_data.strip()

        # 检查: 整个 token 是否就是命名节点 (如 n2)
        if token in named:
            elem = named[token]
            anchor = self._get_anchor(elem)
            if last is not None:
                d.add(elm.Line().at(self._get_anchor(last)).to(anchor))
            return _AnchorRef(anchor), True

        # 检查: token 包含 . 且 base 在 named 中 (如 q2.emitter)
        if "." in token:
            base, anchor_name = token.split(".", 1)
            base, anchor_name = base.strip(), anchor_name.strip()
            if base in named:
                elem = named[base]
                try:
                    anchor = getattr(elem, anchor_name)
                except AttributeError:
                    anchor = self._get_anchor(elem)
                if last is not None:
                    d.add(elm.Line().at(self._get_anchor(last)).to(anchor))
                return _AnchorRef(anchor), True

        return None, False

    def _draw_chain(self, chain_desc: str, comp_map: dict,
                    named: dict, last, d, elm, direction: str):
        """绘制一条链。返回 (new_last, current_direction)。"""
        chain_desc = chain_desc.strip()

        # ── 检查链首：命名节点引用（可带锚点）──
        first_token = chain_desc.split()[0] if chain_desc else ""
        ref_obj, ref_token = self._parse_node_ref(first_token, named)
        if ref_obj is not None:
            last = ref_obj
            chain_desc = chain_desc[len(ref_token):].strip()
            if chain_desc.startswith("->"):
                chain_desc = chain_desc[2:].strip()

        if not chain_desc:
            return last, direction

        # ── 解析组件列表 ──────────────────────────────
        parts = self._parse_parts(chain_desc)
        if not parts:
            return last, direction

        for part_type, part_data in parts:
            if part_type == "direction":
                direction = part_data
            elif part_type == "series":
                # ── 尝试中链节点引用 (如 q2.emitter, n2) ──
                ref_last, handled = self._try_connect_node(
                    part_data, named, last, d, elm)
                if handled:
                    last = ref_last
                    continue

                n, v, lbl, node_name, comp_anchor = self._parse_comp(part_data)
                el_obj = self._place_component(
                    n, v, lbl, last, d, elm, comp_map, direction, comp_anchor)
                last = el_obj
                if node_name:
                    named[node_name] = el_obj
            elif part_type == "parallel":
                last = self._draw_parallel(part_data, comp_map, named,
                                           last, d, elm, direction)

        return last, direction

    # ── 组件列表解析 ────────────────────────────────────────

    @staticmethod
    def _parse_parts(description: str) -> list:
        """解析串联/并联组件 + 方向伪元件。返回 [(type, data), ...]"""
        parts = []
        remaining = description.strip()
        while remaining:
            remaining = remaining.strip()
            if remaining.startswith("["):
                depth = 0
                end = -1
                for idx, ch in enumerate(remaining):
                    if ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            end = idx
                            break
                if end < 0:
                    break
                bracket_content = remaining[1:end]
                branches = Circuit._split_branches(bracket_content)
                parts.append(("parallel", branches))
                remaining = remaining[end + 1:]
                if remaining.startswith("->"):
                    remaining = remaining[2:]
            elif "->" in remaining:
                idx = remaining.index("->")
                part = remaining[:idx].strip()
                if part:
                    if part.lower() in _DIRECTIONS:
                        parts.append(("direction", part.lower()))
                    else:
                        parts.append(("series", part))
                remaining = remaining[idx + 2:]
            else:
                part = remaining.strip()
                if part:
                    if part.lower() in _DIRECTIONS:
                        parts.append(("direction", part.lower()))
                    else:
                        parts.append(("series", part))
                break
        return parts

    @staticmethod
    def _split_branches(content: str) -> list:
        """在顶层逗号处拆分并联分支（哨兵逗号确保末尾分支被收集）。"""
        branches = []
        depth = 0
        current = ""
        for ch in content + ",":
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                b = current.strip()
                if b:
                    branches.append(b)
                current = ""
            else:
                current += ch
        return branches

    # ── 元件放置 ────────────────────────────────────────────

    @staticmethod
    def _make_box(label_text: str, elm):
        """创建框图方块元件。尝试 schemdraw Box，回退到带标签的线。"""
        try:
            return elm.Box().label(label_text)
        except AttributeError:
            return elm.Line().label(f"[{label_text}]")

    @staticmethod
    def _get_anchor(last, anchor: str = "end"):
        """安全获取元件的锚点。回退链：anchor → center → (0,0)。"""
        try:
            return getattr(last, anchor)
        except AttributeError:
            try:
                return last.center
            except AttributeError:
                return (0, 0)

    def _place_component(self, name: str, value: str, label_text: str,
                         last, d, elm, comp_map: dict,
                         direction: str = "right",
                         comp_anchor: str = None):
        """放置单个元件，返回元件对象。"""
        comp_cls, kwargs = self._resolve_comp_name(name, comp_map)

        if comp_cls is None:
            raise ValueError(
                f"Unknown component '{name}'. Valid names: {_VALID_NAMES_STR}"
            )

        kwargs = dict(kwargs)
        if value:
            kwargs["label"] = value

        try:
            # 框图元件：用 _make_box 创建带标签方块
            if comp_cls == _BLOCK_FACTORY:
                box_label = value if value else name.upper()
                box_label = box_label.replace("_", " ").title()
                box = self._make_box(box_label, elm)
                if last is not None:
                    box = box.at(self._get_anchor(last))
                d.add(box)
                return box

            if last is None:
                el = comp_cls(**kwargs)
            elif name in ("ground", "gnd"):
                el = comp_cls(**kwargs).at(self._get_anchor(last))
            elif comp_anchor:
                # @anchor: 以指定锚点连接到 last
                el = comp_cls(**kwargs).anchor(comp_anchor).at(self._get_anchor(last))
            else:
                el = comp_cls(**kwargs)
                # 把方向应用到元件自身，而不是创建新的方向线段
                # e.g. el.down() 设置电容向下摆放，而非用 elm.down() 覆盖元件
                if direction != "right":
                    dir_method = getattr(el, direction, None)
                    if dir_method is not None:
                        el = dir_method()
                el = el.at(self._get_anchor(last))
            d.add(el)
            return el
        except Exception as e:
            logger.warning(f"Failed to add {name}: {e}")
            fallback_anchor = self._get_anchor(last) if last else (0, 0)
            el = elm.Element().label(f"${label_text}$").right().at(fallback_anchor)
            d.add(el)
            return el

    # ── 并联分支绘制 ────────────────────────────────────────

    def _draw_parallel(self, branches: list, comp_map: dict,
                       named: dict, last, d, elm, direction: str) -> object:
        """绘制并联分支组，返回汇合后的 last 元件。"""
        if not last:
            last = elm.Line()
            d.add(last)

        split_point = last.end
        d.add(elm.Dot().at(split_point))

        branch_ends = []

        for bi, branch_str in enumerate(branches):
            branch_comps = [c.strip() for c in branch_str.split("->") if c.strip()]
            if bi == 0:
                draw_dir = getattr(elm, direction)
                first_line = draw_dir().at(split_point)
                d.add(first_line)
                br_last = first_line
                for comp_str in branch_comps:
                    n, v, lbl, node_name, comp_anchor = self._parse_comp(comp_str)
                    br_last = self._place_component(
                        n, v, lbl, br_last, d, elm, comp_map, direction, comp_anchor)
                    if node_name:
                        named[node_name] = br_last
                branch_ends.append(br_last.end)
                last = br_last
            else:
                offset = 0.6 * bi
                d.add(elm.Line().at(split_point).up(offset))
                d.add(elm.Dot())
                draw_dir = getattr(elm, direction)
                br_start = draw_dir()
                d.add(br_start)
                br_last = br_start
                for comp_str in branch_comps:
                    n, v, lbl, node_name, comp_anchor = self._parse_comp(comp_str)
                    br_last = self._place_component(
                        n, v, lbl, br_last, d, elm, comp_map, direction, comp_anchor)
                    if node_name:
                        named[node_name] = br_last
                branch_ends.append(br_last.end)
                d.add(elm.Line().down(offset))
                d.add(elm.Dot())

        if len(branch_ends) > 1:
            max_x = max(pt[0] for pt in branch_ends)
            main_y = branch_ends[0][1]
            join_point = (max_x + 0.5, main_y)
            for bi, end_pt in enumerate(branch_ends):
                if bi > 0:
                    d.add(elm.Line().at(end_pt).to(join_point))
            d.add(elm.Dot().at(join_point))
            draw_dir = getattr(elm, direction)
            last_line = draw_dir().at(join_point)
            d.add(last_line)
            last = last_line

        return last

    # ── 辅助 ─────────────────────────────────────────────────

    @staticmethod
    def _get_ac_source(elm):
        """获取 AC 正弦源元件，兼容不同版本的 schemdraw。"""
        try:
            return elm.SourceSin
        except AttributeError:
            try:
                from schemdraw.elements.sources import SourceSin
                return SourceSin
            except ImportError:
                return elm.SourceV
