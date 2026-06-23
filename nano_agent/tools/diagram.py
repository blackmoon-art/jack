"""图表工具：mermaid_chart, drawio_diagram。

Mermaid: 通过 mermaid.ink API 生成 PNG，免费无需 Key。
Draw.io: 生成 XML 并提供 diagrams.net 编辑/查看链接。
"""

import base64
import json as _json
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
        ("mermaid_chart", "Generate a diagram/flowchart from Mermaid syntax (PNG via mermaid.ink). Supports graph, flowchart, sequenceDiagram, pie, gantt, etc.", "mermaid_chart",
         {"code": {"type": "string", "description": "Mermaid syntax code"},
          "theme": {"type": "string", "description": "Theme: dark or default (default: dark)"}},
         ["code"]),
        ("drawio_diagram", "Generate a Draw.io diagram (flowchart/architecture/UML). Returns a diagrams.net link for viewing/editing.", "drawio_diagram",
         {"diagram_type": {"type": "string", "description": "Diagram type: flowchart, architecture, uml, er (default: flowchart)"},
          "description": {"type": "string", "description": "Natural language description of the diagram"}},
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

    def mermaid_chart(self, code: str, theme: str = "dark") -> str:
        """用 Mermaid 语法生成图表 PNG。

        Args:
            code: Mermaid 语法代码。例:
                  graph TD\n  A[Start] --> B[End]
                  pie title Pets\n  "Dogs": 40\n  "Cats": 30
            theme: dark (推荐) 或 default
        """
        code = code.strip()
        if not code:
            return "Error: Mermaid code is required"

        mermaid = {"code": code}
        if theme == "dark":
            mermaid["mermaid"] = {"theme": "dark"}

        # Encode for mermaid.ink (JSON → gzip → base64 URL-safe)
        try:
            j = _json.dumps(mermaid)
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
        return f"Mermaid chart: {img_url}\n![{code[:50].replace(chr(10), ' ')}]({img_url})"

    # ── Draw.io ──────────────────────────────────────────

    def drawio_diagram(self, title: str = "Diagram",
                       diagram_type: str = "flowchart",
                       nodes: str = "",
                       edges: str = "") -> str:
        """
        生成 Draw.io 图表 XML，返回 diagrams.net 查看/编辑链接。

        Args:
            title: 图表标题
            diagram_type: flowchart | architecture | timeline | uml
            nodes: 节点描述，每行一个: "id:label:type:x:y:width:height"
                   type: rectangle, ellipse, diamond, parallelogram, cylinder
                   例: "A:Start:ellipse:200:100:120:60"
            edges: 边描述，每行一个: "source:target:label"
                   例: "A:B:yes"

        Returns:
            diagrams.net 查看链接
        """
        # 解析节点
        node_list = []
        for line in nodes.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                nid = parts[0].strip()
                label = parts[1].strip()
                ntype = parts[2].strip() if len(parts) > 2 else "rectangle"
                x = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 200 + len(node_list) * 160
                y = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 100 + (len(node_list) % 3) * 120
                w = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 120
                h = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 60
                node_list.append((nid, label, ntype, x, y, w, h))

        # 解析边
        edge_list = []
        for line in edges.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                edge_list.append((parts[0].strip(), parts[1].strip(),
                                  parts[2].strip() if len(parts) > 2 else ""))

        # 生成 Draw.io XML
        xml = self._build_drawio_xml(title, node_list, edge_list, diagram_type)
        encoded = urllib.parse.quote(xml, safe="")

        # 如果节点为空，返回空模板链接
        if not node_list:
            drawio_url = f"https://app.diagrams.net/?lightbox=1#H{title.replace(' ', '%20')}"
            return (
                f"Draw.io link: {drawio_url}\n\n"
                f"Provide nodes and edges to auto-build a diagram. "
                f"Format:\n"
                f"  nodes = 'A:Start:ellipse\\nB:Process:rectangle\\nC:End:ellipse'\n"
                f"  edges = 'A:B:yes\\nB:C'\n\n"
                f"Alternatively, open the link and draw manually."
            )

        drawio_url = f"https://app.diagrams.net/?lightbox=1#R{encoded}"
        return (
            f"📊 Draw.io diagram ({len(node_list)} nodes, {len(edge_list)} edges):\n"
            f"{drawio_url}\n\n"
            f"Click to view/edit. The diagram will auto-open with your nodes and edges."
        )

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
