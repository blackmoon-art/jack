"""电路图工具 — 基于 schemdraw 渲染专业电路图。"""

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.circuit")


class Circuit:
    TOOLS = [
        ("draw_circuit",
         "Draw professional circuit diagrams with proper electrical symbols. "
         "For: circuit schematics, electronic diagrams, wiring layouts. "
         "Components: battery, resistor, capacitor, inductor, diode, LED, transistor, "
         "switch(spst), ground, antenna, opamp, IC labels, wire connections. "
         "Use '->' for series connections. For parallel branches use square brackets:\n"
         "  '[branch1, branch2, ...]' — branches split from and rejoin at the same nodes.\n"
         "Example series: 'battery(9V) -> switch -> resistor(100Ω) -> LED -> ground'\n"
         "Example parallel: 'battery(5V) -> [resistor(1kΩ) -> LED, capacitor(10μF)] -> ground'",
         "draw_circuit",
         {"description": {"type": "string",
                          "description": "Circuit: series with '->' between components. "
                                         "Parallel branches with '[branch1, branch2]'. "
                                         "Example: 'battery(5V) -> [R1(1k) -> LED1, R2(470) -> LED2] -> ground'"},
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

    def draw_circuit(self, description: str, title: str = "") -> str:
        """用 schemdraw 绘制电路图。"""
        import schemdraw
        import schemdraw.elements as elm
        from schemdraw import Drawing

        try:
            d = Drawing(show=False)
            if title:
                d.config(fontsize=14)

            # 解析组件（支持并联语法 [branch1, branch2]）
            import re as _re_circuit
            parts = []
            remaining = description.strip()
            while remaining:
                remaining = remaining.strip()
                if remaining.startswith("["):
                    # 并联分支: [a -> b, c -> d]
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
                        return "Error: unmatched '[' in circuit description"
                    bracket_content = remaining[1:end]
                    # 按逗号拆分各分支（只在顶层，不在嵌套括号内拆分）
                    branches = []
                    depth2 = 0
                    current_branch = ""
                    for ch in bracket_content + ",":
                        if ch == "[" or ch == "(":
                            depth2 += 1
                        elif ch == "]" or ch == ")":
                            depth2 -= 1
                        if ch == "," and depth2 == 0:
                            branches.append(current_branch.strip())
                            current_branch = ""
                        else:
                            current_branch += ch
                    if current_branch.strip():
                        branches.append(current_branch.strip())
                    parts.append(("parallel", branches))
                    remaining = remaining[end+1:]
                    if remaining.startswith("->"):
                        remaining = remaining[2:]
                elif "->" in remaining:
                    idx = remaining.index("->")
                    part = remaining[:idx].strip()
                    if part:
                        parts.append(("series", part))
                    remaining = remaining[idx+2:]
                else:
                    part = remaining.strip()
                    if part:
                        parts.append(("series", part))
                    break
            if not parts:
                return "Error: no components in circuit description"

            # 映射组件名到 schemdraw 元件
            comp_map = {
                "battery": (elm.Battery, {}),
                "v": (elm.SourceV, {}),
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
                "opamp": (elm.Opamp, {}),
                "transistor": (elm.BjtNpn, {}),
                "npn": (elm.BjtNpn, {}),
                "pnp": (elm.BjtPnp, {}),
                "fuse": (elm.Fuse, {}),
                "lamp": (elm.Lamp, {}),
                "motor": (elm.Motor, {}),
                "speaker": (elm.Speaker, {}),
                "microphone": (elm.Mic, {}),
                "line": (elm.Line, {}),
                "wire": (elm.Line, {}),
                "dot": (elm.Dot, {}),
            }

            def _place_component(name: str, value: str, label_text: str, last, d):
                """放置单个元件，返回新的 last。"""
                comp_cls, kwargs = comp_map.get(name, (None, {}))
                if comp_cls is None:
                    if last is None:
                        el = elm.Line().label(f"${label_text}$")
                    else:
                        el = elm.Line().label(f"${label_text}$").right().at(last.end)
                    d.add(el)
                    return el

                kwargs = dict(kwargs)
                if value:
                    kwargs["label"] = value
                try:
                    if last is None:
                        el = comp_cls(**kwargs)
                    elif name in ("ground", "gnd"):
                        el = comp_cls(**kwargs).at(last.end)
                    else:
                        el = comp_cls(**kwargs).right().at(last.end)
                    d.add(el)
                    return el
                except Exception as e:
                    logger.warning(f"Failed to add {name}: {e}")
                    el = elm.Element().label(f"${label_text}$").right().at(last.end if last else (0, 0))
                    d.add(el)
                    return el

            def _parse_comp(comp_str: str) -> tuple:
                """解析 'resistor(100Ω)' → ('resistor', '100Ω', 'resistor(100Ω)')"""
                c = comp_str.strip()
                if "(" in c and ")" in c:
                    n = c.split("(")[0].strip().lower()
                    v = c[c.index("(")+1:c.index(")")].strip()
                    return n, v, c
                return c.lower(), "", c

            def _draw_branch(branch_parts, start, d):
                """绘制一条分支（串行），返回末端元件。异常不中断其他分支。"""
                cur = start
                for pt in branch_parts:
                    if isinstance(pt, str):
                        try:
                            n, v, lbl = _parse_comp(pt)
                            cur = _place_component(n, v, lbl, cur, d)
                        except Exception as e:
                            logger.warning(f"Circuit: failed to place '{pt}' in branch: {e}")
                            # 画一个占位标签，不中断
                            fallback = elm.Element().label(f"${pt}$").right().at(cur.end if cur else (0, 0))
                            d.add(fallback)
                            cur = fallback
                return cur

            last = None
            for part_type, part_data in parts:
                if part_type == "series":
                    n, v, lbl = _parse_comp(part_data)
                    last = _place_component(n, v, lbl, last, d)
                elif part_type == "parallel":
                    # 并联分支: 从 split_point 分叉，各分支终点汇合到 join_point
                    branches = part_data
                    if not last:
                        last = elm.Line()
                        d.add(last)
                    split_point = last.end
                    d.add(elm.Dot().at(split_point))  # 分叉点标记

                    branch_ends = []
                    all_branch_endpoints = []  # 收集所有分支末端坐标
                    for bi, branch_str in enumerate(branches):
                        branch_comps = [c.strip() for c in branch_str.split("->") if c.strip()]
                        if bi == 0:
                            # 第一分支：从 split_point 向右画主路径
                            first_line = elm.Line().right().at(split_point)
                            d.add(first_line)
                            br_last = _draw_branch(branch_comps[:-1], first_line, d) if len(branch_comps) > 1 else first_line
                            # 最后一个组件单独放置
                            if branch_comps:
                                n, v, lbl = _parse_comp(branch_comps[-1])
                                br_last = _place_component(n, v, lbl, br_last, d)
                            branch_ends.append(br_last.end)
                            last = br_last
                        else:
                            # 其他分支：从 split_point 向上或向下偏移
                            offset = 0.6 * bi
                            d.add(elm.Line().at(split_point).up(offset))
                            d.add(elm.Dot())
                            br_start = elm.Line().right()
                            d.add(br_start)
                            br_last = _draw_branch(branch_comps, br_start, d)
                            branch_ends.append(br_last.end)
                            # 汇合线：画回水平线
                            d.add(elm.Line().down(offset))
                            d.add(elm.Dot())

                    # 汇聚点：取所有分支终点中最右的点，用连线连接
                    if len(branch_ends) > 1:
                        max_x = max(pt[0] for pt in branch_ends)
                        main_y = branch_ends[0][1]
                        join_point = (max_x + 0.5, main_y)
                        # 将各非主分支的终点连到汇聚点
                        for bi, end_pt in enumerate(branch_ends):
                            if bi > 0:
                                d.add(elm.Line().at(end_pt).to(join_point))
                                d.add(elm.Dot().at(join_point))
                        # 汇聚点向右延续，作为下游入口
                        last_line = elm.Line().right().at(join_point)
                        d.add(last_line)
                        last = last_line

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
