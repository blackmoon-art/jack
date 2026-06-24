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
         "Describe the circuit from left to right, listing each component and its connections.\n"
         "Example: 'battery(9V) -> switch -> resistor(100Ω) -> LED -> ground'",
         "draw_circuit",
         {"description": {"type": "string",
                          "description": "Circuit description: list components left to right with values in (). "
                                         "Example: 'battery(5V) -> resistor(1kΩ) -> capacitor(10μF) -> ground'"},
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

            # 解析组件
            parts = [p.strip() for p in description.split("->") if p.strip()]
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

            last = None
            for i, part in enumerate(parts):
                # 解析: "resistor(100Ω)" 或 "resistor" 或 "battery(5V)"
                name = part.split("(")[0].strip().lower() if "(" in part else part.strip().lower()
                value = ""
                if "(" in part and ")" in part:
                    value = part[part.index("(")+1:part.index(")")].strip()

                comp_cls, kwargs = comp_map.get(name, (None, {}))
                if comp_cls is None:
                    logger.warning(f"Unknown component: {name}, using label")
                    # 未知组件用标签
                    label = f"${part}$"
                    if last is None:
                        last = elm.Line().label(label)
                    else:
                        last = elm.Line().label(label).at(last.end)
                    continue

                kwargs = dict(kwargs)
                label_parts = []
                if value:
                    label_parts.append(value)
                if name.upper() != value.upper() and name not in ("line", "wire", "dot"):
                    pass  # already labeled by value

                if label_parts:
                    kwargs["label"] = " ".join(label_parts)

                try:
                    if last is None:
                        # 第一个组件
                        if name == "ground" or name == "gnd":
                            elem = comp_cls(**kwargs)
                            d.add(elem)
                            last = elem
                        else:
                            elem = comp_cls(**kwargs)
                            d.add(elem)
                            last = elem
                    else:
                        # 根据类型决定放置方式
                        if name == "ground" or name == "gnd":
                            elem = comp_cls(**kwargs).at(last.end)
                            d.add(elem)
                        else:
                            elem = comp_cls(**kwargs).right().at(last.end)
                            d.add(elem)
                            last = elem
                except Exception as e:
                    logger.warning(f"Failed to add component {name}: {e}")
                    # fallback: draw a box with the component name
                    try:
                        elem = elm.Element().label(f"${part}$").right().at(last.end if last else (0, 0))
                        d.add(elem)
                        last = elem
                    except Exception:
                        continue

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
