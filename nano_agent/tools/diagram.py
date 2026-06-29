"""图表工具：mermaid_chart, drawio_diagram。

Mermaid: 通过 mermaid.ink API 生成 PNG，免费无需 Key。
Draw.io: 生成 XML 并提供 diagrams.net 编辑/查看链接。
"""

import base64
import json
import logging
import urllib.parse
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.diagram")

MERMAID_INK = "https://mermaid.ink"


class Diagram:
    # 工具注册声明
    TOOLS = [
        ("mermaid_chart",
         "Generate diagrams & structural drawings from Mermaid syntax (PNG). Layout is auto-optimized.\n"
         "Use for: geometric proofs, math diagrams (Pythagoras, geometry), architecture, flowcharts,\n"
         "state machines, org charts, sequence/timing diagrams (交互时序图 → sequenceDiagram).\n"
         "NOT for coordinate graphs or data plots — use generate_chart for those.\n"
         "Sequence/timing (时序图): 'sequenceDiagram', shows interactions between components over time.\n"
         "Architecture: 'flowchart TB', group layers with subgraph.\n"
         "Shapes: [(DB)] database, [/API/] gateway, ([Svc]) service, [Client] client, {Auth} decision.\n"
         "Arrows: --> request, -.-> async, ==> critical. State machines: stateDiagram-v2.\n"
         "⚠️ Privacy: code is sent to mermaid.ink / plantuml.com for rendering. Do NOT include secrets or PII.",
         "mermaid_chart",
         {"code": {"type": "string",
                   "description": (
                       "Architecture: flowchart TB + subgraph per layer. "
                       "Sequence/timing (时序图): sequenceDiagram + participants + messages (->>,-->>). "
                       "Shapes: [(DB)]=database, [/API/]=gateway, ([Svc])=service, [Client]=client, {Auth}=decision. "
                       "Arrows: --> data, -.-> async, ==> critical. "
                       "Flowchart: short labels. State: stateDiagram-v2."
                   )},
          "theme": {"type": "string", "description": "Theme: dark or default (default: dark)"}},
         ["code"]),
        ("drawio_diagram",
         "Generate a Draw.io diagram — ideal for complex state machines, architecture, UML. "
         "Returns an editable diagrams.net link with perfect auto-layout. "
         "Use this for state machines with >5 states or any diagram where layout quality matters.",
         "drawio_diagram",
         {"description": {"type": "string",
                          "description": "Diagram description using arrows for connections. "
                          "Example: 'Idle -> Loading: start\\nLoading -> Ready: success\\nLoading -> Error: fail'"},
          "diagram_type": {"type": "string", "description": "Diagram type: flowchart, state-machine, architecture, uml, er"}},
         ["description"]),
    ]

    def __init__(self, work_dir: str, charts_dir: str = ""):
        if charts_dir:
            self.charts_dir = Path(charts_dir)
        else:
            web_static = Path(__file__).parent.parent.parent / "web" / "static"
            self.charts_dir = web_static / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)

    # ── Mermaid ──────────────────────────────────────────

    # 图表类型布局优化配置
    _CHART_CONFIGS = {
        "flowchart":      {"flowchart": {"useMaxWidth": True, "htmlLabels": True, "curve": "basis", "rankSpacing": 60, "nodeSpacing": 35, "padding": 25}},
        "graph":          {"flowchart": {"useMaxWidth": True, "htmlLabels": True, "curve": "basis", "rankSpacing": 60, "nodeSpacing": 35, "padding": 25}},
        "stateDiagram":   {},
        "stateDiagram-v2": {},
        "sequenceDiagram": {"sequence": {"useMaxWidth": True, "mirrorActors": False, "actorMargin": 80, "messageMargin": 40}},
        "classDiagram":   {"class": {"useMaxWidth": True}},
        "erDiagram":      {"er": {"useMaxWidth": True}},
        "gantt":          {"gantt": {"useMaxWidth": True, "barHeight": 25, "fontSize": 12}},
        "pie":            {"pie": {"useMaxWidth": True}},
    }

    # ── PlantUML 状态图渲染 ──────────────────────────────

    PLANTUML_BASE = "https://www.plantuml.com/plantuml"

    @staticmethod
    def _plantuml_encode(data: bytes) -> str:
        """PlantUML 自定义 base64 编码（deflate 后使用）。"""
        res = []
        for i in range(0, len(data), 3):
            b1, b2 = data[i], data[i+1] if i+1 < len(data) else 0
            b3 = data[i+2] if i+2 < len(data) else 0
            c1, c2 = b1 >> 2, ((b1 & 0x3) << 4) | (b2 >> 4)
            c3, c4 = ((b2 & 0xF) << 2) | (b3 >> 6), b3 & 0x3F
            for c in (c1, c2, c3, c4):
                if c < 10: res.append(chr(48 + c))
                elif c < 36: res.append(chr(65 + c - 10))
                elif c < 62: res.append(chr(97 + c - 36))
                elif c == 62: res.append('-')
                else: res.append('_')
        extra = (3 - len(data) % 3) % 3
        return ''.join(res[:len(res)-extra] if extra else res)

    def _render_plantuml(self, code: str) -> str:
        """用 PlantUML 渲染状态图（布局远优于 Mermaid stateDiagram）。"""
        import re as _re3

        lines = code.strip().split("\n")
        plant_lines = ["@startuml"]
        for line in lines:
            s = line.strip()
            if not s or _re3.match(r'stateDiagram', s, _re3.IGNORECASE):
                continue
            # 分隔线跳过
            if s == "---" or s == "--":
                continue
            # 转换: A --> B: label → A --> B : label
            m = _re3.match(r'(\S+?)\s*-->\s*([^:\s]+)\s*:?\s*(.*)', s)
            if m:
                src, tgt, lbl = m.group(1), m.group(2), m.group(3).strip()
                lbl_suffix = f" : {lbl}" if lbl else ""
                plant_lines.append(f"{src} --> {tgt}{lbl_suffix}")
                continue
            # note right of X: text → note right of X : text（冒号切分，支持中文）
            if _re3.match(r'note\s+(right|left)\s+of\s+', s, _re3.IGNORECASE):
                # 格式: note <pos> of <name>: <text>  或  note <pos> of <name> : <text>
                header, _, rest = s.partition(" of ")
                pos = header.split()[-1] if header else "right"
                if ":" in rest:
                    name, _, text = rest.partition(":")
                    plant_lines.append(f"note {pos} of {name.strip()} : {text.strip()}")
                else:
                    plant_lines.append(f"note {pos} of {rest.strip()}")
                continue
            # 复合状态 state Name { → state Name {
            if _re3.match(r'state\s+.*\{', s):
                plant_lines.append(s)
                continue
            if s in ("}", "}"):
                plant_lines.append(s)
                continue
            # state 声明: state "Name" as Alias 或 state Name: desc
            m3 = _re3.match(r'state\s+(.+?)\s*:?\s*(.*)', s)
            if m3:
                state_def = m3.group(1).strip()
                desc = m3.group(2).strip()
                if desc:
                    plant_lines.append(f"state {state_def} : {desc}")
                else:
                    plant_lines.append(f"state {state_def}")
                continue
            # 描述: Name: description → Name : description
            m4 = _re3.match(r'(\S+?)\s*:\s*(.+)', s)
            if m4 and "-->" not in s:
                plant_lines.append(f"{m4.group(1)} : {m4.group(2)}")
                continue
            # 其他合法行直接透传
            plant_lines.append(s)
        plant_lines.append("@enduml")

        plant_code = "\n".join(plant_lines)

        # 编码: hex (更可靠，尤其是中文/UTF-8)
        hex_str = plant_code.encode('utf-8').hex()
        url = f"{self.PLANTUML_BASE}/png/~h{hex_str}"

        # 下载 PNG
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"plantuml_{ts}.png"
        filepath = self.charts_dir / filename

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "nano_agent_plus/1.0",
                "Accept": "image/png",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                filepath.write_bytes(resp.read())
        except Exception as e:
            return f"Error: PlantUML render failed — {e}"

        img_url = f"/charts/{filename}"
        return f"![State diagram]({img_url})\n{img_url}"

    def _smart_mermaid_config(self, code: str, theme: str) -> dict:
        """自动检测图表类型，注入布局配置。"""
        import re

        code_stripped = code.strip()

        # 检测图表类型
        first_line = code_stripped.split("\n")[0].strip()
        first_lower = first_line.lower()
        chart_type = "flowchart"
        for key in self._CHART_CONFIGS:
            if first_lower.startswith(key.lower()):
                chart_type = key
                break

        # 检测是否已有方向声明
        has_direction = bool(
            re.match(r'(flowchart|graph)\s+(TB|BT|LR|RL)\b', code_stripped, re.IGNORECASE)
            or re.search(r'\bdirection\s+(TB|BT|LR|RL)\b', code_stripped)
        )

        # 主题 + 类型配置
        mermaid_config = {"theme": theme}
        type_cfg = self._CHART_CONFIGS.get(chart_type, {})
        if type_cfg:
            mermaid_config.update(type_cfg)

        # flowchart 无方向声明时自动加
        if chart_type in ("flowchart", "graph") and not has_direction:
            has_subgraph = bool(re.search(r'\bsubgraph\b', code_stripped))
            if has_subgraph:
                direction = "TB"  # 有 subgraph = 分层架构，必须纵向
            else:
                node_count = len(re.findall(r'[A-Za-z_][A-Za-z0-9_]*\s*[\[\(\{/\\]', code_stripped))
                direction = "LR" if node_count <= 8 else "TB"
            # 在代码前面加方向声明，保留全部原始内容
            code_stripped = f"flowchart {direction}\n" + code_stripped

        return {"code": code_stripped, "mermaid": mermaid_config}

    def mermaid_chart(self, code: str, theme: str = "dark") -> str:
        """用 Mermaid 语法生成图表 PNG，自动优化布局。

        Args:
            code: Mermaid 语法代码。
                  - 架构图: flowchart TB + subgraph 分层，用不同形状区分组件
                  - 流程图: A[标签]→B{判断}→C([结束])
                  - 状态机: stateDiagram-v2 with [*] start/end
            theme: dark (推荐) 或 default
        """
        code = code.strip()
        if not code:
            return "Error: Mermaid code is required"

        # 状态图 → PlantUML 渲染（布局远优于 Mermaid）
        import re as _re2
        if _re2.match(r'stateDiagram', code, _re2.IGNORECASE):
            return self._render_plantuml(code)

        # 智能配置注入
        payload = self._smart_mermaid_config(code, theme)

        # Encode for mermaid.ink (JSON → gzip → base64 URL-safe)
        try:
            j = json.dumps(payload)
            compressed = zlib.compress(j.encode(), level=9)
            encoded = base64.urlsafe_b64encode(compressed).decode().rstrip("=")
            url = f"{MERMAID_INK}/img/pako:{encoded}?type=png"
        except Exception as e:
            return f"Error encoding mermaid: {e}"

        # Download the rendered PNG
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"mermaid_{ts}.png"
        filepath = self.charts_dir / filename

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "nano_agent_plus/1.0",
                "Accept": "image/png",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                filepath.write_bytes(resp.read())
        except urllib.error.URLError as e:
            return f"Error: Mermaid render failed — {e.reason}"
        except Exception as e:
            return f"Error: {e}"

        img_url = f"/charts/{filename}"
        # 转义 [ ] 防止破坏 markdown 图片语法
        alt = code[:50].replace(chr(10), ' ').replace('[', '(').replace(']', ')')
        return f"![Mermaid chart]({img_url})\n> `{alt}...`\n{img_url}"

    # ── Draw.io ──────────────────────────────────────────

    def drawio_diagram(self, description: str = "", diagram_type: str = "flowchart") -> str:
        """从描述生成 Draw.io 图表，返回 diagrams.net 查看/编辑链接。

        Args:
            description: 图表描述。用 '->' 表示连接，冒号后为标签。
                         节点直接写名称即可，有连接的节点自动创建。
                         例: 'Idle -> Loading: start\\nLoading -> Ready: success'
            diagram_type: flowchart | architecture | uml | er

        Returns:
            diagrams.net 查看链接
        """
        title = f"{diagram_type} diagram"
        node_list, edge_list = self._parse_diagram_description(description)

        if not node_list:
            drawio_url = f"https://app.diagrams.net/?lightbox=1#H{title.replace(' ', '%20')}"
            return (
                f"Draw.io link: {drawio_url}\n\n"
                f"Provide a description with nodes and edges. Example:\n"
                f"  'Start -> Process: begin\\nProcess -> End: done'"
            )

        xml = self._build_drawio_xml(title, node_list, edge_list, diagram_type)
        encoded = urllib.parse.quote(xml, safe="")
        # 大图表 (>2000字符URL) → deflate+base64 压缩，避免浏览器拒绝超长 URL
        if len(encoded) > 2000:
            compressed = zlib.compress(xml.encode(), level=9)
            encoded = base64.urlsafe_b64encode(compressed).decode().rstrip("=")
            drawio_url = f"https://app.diagrams.net/?lightbox=1#pako:{encoded}"
        else:
            drawio_url = f"https://app.diagrams.net/?lightbox=1#R{encoded}"
        return (
            f"📊 Draw.io diagram ({len(node_list)} nodes, {len(edge_list)} edges):\n"
            f"{drawio_url}\n\n"
            f"Click to view/edit."
        )

    def _parse_diagram_description(self, description: str) -> tuple[list, list]:
        """将自然语言描述解析为节点和边。

        格式:
          A -> B          → 边 A→B
          A -> B: label   → 带标签的边
          A --> B         → 同 ->
          A               → 独立节点
        """
        import re
        nodes = []   # (id, label, shape, x, y, w, h)
        edges = []   # (src_id, tgt_id, label)
        node_map = {}  # name → id

        def get_node_id(name: str) -> str:
            name = name.strip()
            if name in node_map:
                return node_map[name]
            nid = f"n{len(node_map)}"
            cols = 4
            row = len(node_map) // cols
            col = len(node_map) % cols
            x = 100 + col * 180
            y = 100 + row * 120
            node_map[name] = nid
            nodes.append((nid, name, "rectangle", x, y, 120, 60))
            return nid

        for line in description.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            # 边: A -> B 或 A --> B，可选 : label
            m = re.match(r'(.+?)\s*-+>\s*([^:]+?)(?:\s*:\s*(.+))?$', line)
            if m:
                src_id = get_node_id(m.group(1))
                tgt_id = get_node_id(m.group(2))
                label = m.group(3).strip() if m.group(3) else ""
                edges.append((src_id, tgt_id, label))
            else:
                # 独立节点
                get_node_id(line)

        return nodes, edges

    def _build_drawio_xml(self, title: str, nodes: list, edges: list,
                          diagram_type: str) -> str:
        """构建 Draw.io XML。"""
        cells = []

        # 节点
        shape_map = {
            "rectangle": "",
            "ellipse": "ellipse;whiteSpace=wrap;html=1;",
            "diamond": "rhombus;whiteSpace=wrap;html=1;",
            "parallelogram": "parallelogram;whiteSpace=wrap;html=1;",
            "cylinder": "cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;",
        }

        for (nid, label, ntype, x, y, w, h) in nodes:
            style = shape_map.get(ntype, "")
            cells.append(
                f'<mxCell id="{nid}" value="{self._xml_escape(label)}" '
                f'style="{style}fillColor=#7c3aed;strokeColor=#5b21b6;fontColor=#ffffff;fontSize=14;" '
                f'vertex="1" parent="1">'
                f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
                f'</mxCell>'
            )

        # 边
        for i, (src, tgt, label) in enumerate(edges):
            edge_id = f"e{i+1}"
            cells.append(
                f'<mxCell id="{edge_id}" value="{self._xml_escape(label)}" '
                f'style="edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;'
                f'html=1;strokeColor=#a78bfa;fontColor=#e0e0e0;fontSize=12;" '
                f'edge="1" parent="1" source="{src}" target="{tgt}">'
                f'<mxGeometry relative="1" as="geometry"/>'
                f'</mxCell>'
            )

        xml = (
            '<mxfile host="nano_agent_plus" modified="2026-01-01T00:00:00.000Z" '
            'agent="Mozilla/5.0" version="21.0.0" type="device">'
            f'<diagram name="{self._xml_escape(title)}" id="diagram1">'
            '<mxGraphModel dx="1000" dy="800" grid="1" gridSize="10" guides="1" '
            'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            'pageWidth="827" pageHeight="1169" math="0" shadow="0">'
            '<root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            + "\n".join(cells) +
            '</root></mxGraphModel></diagram></mxfile>'
        )
        return xml

    @staticmethod
    def _xml_escape(text: str) -> str:
        return (text.replace("&", "&amp;").replace('"', "&quot;")
                .replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;"))
