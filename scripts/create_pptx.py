#!/usr/bin/env python3
"""Generate a 5-slide PPTX: 2026年AI技术趋势展望"""

import zipfile
import os

# ============================================================
# XML templates
# ============================================================

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slides/slide3.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slides/slide4.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slides/slide5.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>
  <Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>
  <Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>
</Types>'''

RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>'''

PRESENTATION = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                slideSize="9144000 6858000">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/>
    <p:sldId id="258" r:id="rId4"/><p:sldId id="259" r:id="rId5"/>
    <p:sldId id="260" r:id="rId6"/>
  </p:sldIdLst>
  <p:sldSz cx="9144000" cy="6858000"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>'''

PRES_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
  <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide3.xml"/>
  <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide4.xml"/>
  <Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide5.xml"/>
</Relationships>'''

SLIDE_MASTER = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
    <p:grpSpPr/>
  </p:spTree></p:cSld>
  <p:typeLst/>
  <p:clrMstr><a:clrScheme name="default"/></p:clrMstr>
</p:sldMaster>'''

MASTER_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>'''

THEME = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Default">
  <a:themeElements>
    <a:clrScheme name="Default">
      <a:dk1><a:srgbClr val="000000"/></a:dk1>
      <a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
      <a:dk2><a:srgbClr val="44546A"/></a:dk2>
      <a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>
      <a:accent1><a:srgbClr val="4472C4"/></a:accent1>
      <a:accent2><a:srgbClr val="ED7D31"/></a:accent2>
      <a:accent3><a:srgbClr val="70AD47"/></a:accent3>
      <a:accent4><a:srgbClr val="FFC000"/></a:accent4>
      <a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>
      <a:accent6><a:srgbClr val="70AD47"/></a:accent6>
      <a:hlink><a:srgbClr val="0563C1"/></a:hlink>
      <a:folHlink><a:srgbClr val="954F72"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="Default">
      <a:majorFont><a:latin typeface="Microsoft YaHei"/><a:ea typeface="Microsoft YaHei"/></a:majorFont>
      <a:minorFont><a:latin typeface="Microsoft YaHei"/><a:ea typeface="Microsoft YaHei"/></a:minorFont>
    </a:fontScheme>
    <a:fmtScheme name="Default"/>
  </a:themeElements>
</a:theme>'''

PRES_PROPS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presProps xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'''

VIEW_PROPS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:viewProps xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" showGuides="0"/>'''

TABLE_STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>'''


def slide_xml(title_text, body_items, bg_color="1B2A4A", title_color="FFFFFF", body_color="D0D8E8"):
    items_xml = ""
    for item in body_items:
        if item.startswith("##"):
            text = item.strip("# ")
            items_xml += f'''
      <p:sp>
        <p:nvSpPr><p:cNvPr id="0" name="Subtitle"/><p:cNvSpPr><a:spAutoFit/></p:cNvSpPr><p:nvPr/></p:nvSpPr>
        <p:spPr><a:solidFill><a:srgbClr val="2A5298"/></a:solidFill><a:ln w="0"/><a:round/></p:spPr>
        <p:txBody><a:bodyPr l="114300" r="114300" t="45720" b="45720"/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="1800" b="1" latin="Microsoft YaHei" ea="Microsoft YaHei"><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:rPr><a:t>{text}</a:t></a:r></a:p></p:txBody>
      </p:sp>'''
        elif item.startswith(">"):
            text = item.strip("> ")
            items_xml += f'''
      <p:sp>
        <p:nvSpPr><p:cNvPr id="0" name="Quote"/><p:cNvSpPr><a:spAutoFit/></p:cNvSpPr><p:nvPr/></p:nvSpPr>
        <p:spPr><a:solidFill><a:srgbClr val="1A3366"/></a:solidFill><a:ln w="0"/><a:round/></p:spPr>
        <p:txBody><a:bodyPr l="114300" r="114300" t="45720" b="45720"/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="1600" i="1" latin="Microsoft YaHei" ea="Microsoft YaHei"><a:solidFill><a:srgbClr val="88BBEE"/></a:solidFill></a:rPr><a:t>{text}</a:t></a:r></a:p></p:txBody>
      </p:sp>'''
        else:
            items_xml += f'''
      <p:sp>
        <p:nvSpPr><p:cNvPr id="0" name="Bullet"/><p:cNvSpPr><a:spAutoFit/></p:cNvSpPr><p:nvPr/></p:nvSpPr>
        <p:spPr><a:solidFill><a:srgbClr val="1A3A6A"/></a:solidFill><a:ln w="0"/><a:round/></p:spPr>
        <p:txBody><a:bodyPr l="114300" r="114300" t="45720" b="45720"/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="1600" latin="Microsoft YaHei" ea="Microsoft YaHei"><a:solidFill><a:srgbClr val="{body_color}"/></a:solidFill></a:rPr><a:t>{text}</a:t></a:r></a:p></p:txBody>
      </p:sp>'''

    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr/>
      <!-- Background -->
      <p:sp>
        <p:nvSpPr><p:cNvPr id="0" name="Bg"/><p:cNvSpPr><a:noFill/></p:cNvSpPr><p:nvPr/></p:nvSpPr>
        <p:spPr><a:solidFill><a:srgbClr val="{bg_color}"/></a:solidFill><a:ln w="0"/><a:round/></p:spPr>
        <p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t> </a:t></a:r></a:p></p:txBody>
      </p:sp>
      <!-- Title bar -->
      <p:sp>
        <p:nvSpPr><p:cNvPr id="0" name="TitleBar"/><p:cNvSpPr><a:spAutoFit/></p:cNvSpPr><p:nvPr/></p:nvSpPr>
        <p:spPr><a:solidFill><a:srgbClr val="1A3A6E"/></a:solidFill><a:ln w="0"/><a:rect l="0" t="0" r="9144000" b="914400"/></p:spPr>
        <p:txBody><a:bodyPr l="228600" r="228600" t="91440" b="91440"/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="2800" b="1" latin="Microsoft YaHei" ea="Microsoft YaHei"><a:solidFill><a:srgbClr val="{title_color}"/></a:solidFill></a:rPr><a:t>{title_text}</a:t></a:r></a:p></p:txBody>
      </p:sp>
      <!-- Decorative line -->
      <p:sp>
        <p:nvSpPr><p:cNvPr id="0" name="Line"/><p:cNvSpPr><a:noFill/></p:cNvSpPr><p:nvPr/></p:nvSpPr>
        <p:spPr><a:solidFill><a:srgbClr val="4A90D9"/></a:solidFill><a:ln w="0"/><a:rect l="0" t="914400" r="9144000" b="990600"/></p:spPr>
        <p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t> </a:t></a:r></a:p></p:txBody>
      </p:sp>
{items_xml}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>'''


slides = [
    {
        "title": "2026年AI技术趋势展望",
        "body": [
            "## 全球AI发展全景",
            "2026年，人工智能进入\"落地深化年\"",
            "全球AI市场规模预计突破 3,000亿美元",
            "中国AI产业规模有望达到 800亿美元",
            "> \"从技术突破走向产业重塑——AI不再是概念，而是核心生产力\"",
            "## 五大核心趋势",
            "🤖 多模态AGI · 📊 AI Agent · ⚡ 端侧AI",
            "🔬 科学智能(AI4S) · 🌐 AI基础设施"
        ]
    },
    {
        "title": "趋势一：多模态AGI走向成熟",
        "body": [
            "## 从单模态到全模态融合",
            "✅ 文本+图像+视频+音频+3D 统一理解与生成",
            "✅ 大模型上下文窗口扩展至 100万Token+",
            "✅ 推理能力大幅提升（思维链 + 自学习）",
            "## 代表性进展",
            "GPT-5 / Gemini 3.0 / Claude 4 · 文心一言 5.0 / 通义千问 3.0",
            "多模态Agent自主完成复杂任务（代码编写、数据分析、内容创作）",
            "> \"2026年，多模态能力从'能看懂'进化到'能理解、能推理、能执行'\""
        ]
    },
    {
        "title": "趋势二：AI Agent 元年",
        "body": [
            "## Agent 从概念走向生产",
            "🤖 AI Agent 框架爆发：LangGraph, CrewAI, AutoGen, MetaGPT",
            "🔄 多Agent协作系统：规划→执行→验证 全链路自动化",
            "🏢 企业级Agent落地：智能客服、自动化运营、代码审查",
            "## 关键能力",
            "工具调用（Function Calling）· 长期记忆 · 自主决策 · 自我纠错",
            "> \"2026年被称为'AI Agent元年'，每个企业都将拥有自己的AI员工\""
        ]
    },
    {
        "title": "趋势三：端侧AI与AI基础设施",
        "body": [
            "## 端侧AI爆发",
            "📱 手机端大模型推理成为标配（高通/联发科/苹果芯片支持）",
            "💻 AI PC 渗透率超 40%，本地运行 70亿+ 参数模型",
            "🏭 边缘AI芯片出货量突破 10亿颗",
            "## AI基础设施升级",
            "⚡ 算力集群规模突破 10万卡（GPU/NPU混合架构）",
            "🌐 新型AI网络架构（超以太网/InfiniBand）",
            "☁️ AI云服务成本下降 50%+，普惠化加速",
            "> \"端侧AI让智能无处不在，基础设施让算力触手可及\""
        ]
    },
    {
        "title": "趋势四：AI4S 与 AI治理",
        "body": [
            "## AI for Science 科学智能",
            "🔬 蛋白质设计、材料发现、药物研发 效率提升 10倍+",
            "🧬 AlphaFold 3 + AI驱动的新药进入临床试验",
            "🌡️ 气候预测、能源优化 等科学问题取得重大突破",
            "## AI治理与安全",
            "📜 全球AI监管框架加速落地（EU AI Act、中国AI治理方案）",
            "🛡️ AI安全对齐技术（RLHF + Constitutional AI + 红队测试）",
            "🔐 数据隐私与模型安全成为企业核心合规要求",
            "> \"技术越强大，治理越重要——负责任的AI是可持续发展的基石\""
        ]
    }
]

def create_pptx(path):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("ppt/presentation.xml", PRESENTATION)
        zf.writestr("ppt/_rels/presentation.xml.rels", PRES_RELS)
        zf.writestr("ppt/presProps.xml", PRES_PROPS)
        zf.writestr("ppt/viewProps.xml", VIEW_PROPS)
        zf.writestr("ppt/tableStyles.xml", TABLE_STYLES)
        zf.writestr("ppt/slideMasters/slideMaster1.xml", SLIDE_MASTER)
        zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", MASTER_RELS)
        zf.writestr("ppt/theme/theme1.xml", THEME)

        bg_colors = ["0F1F3D", "0D1F3F", "0F1F3D", "0D1F3F", "0F1F3D"]
        for i, s in enumerate(slides):
            xml = slide_xml(s["title"], s["body"], bg_color=bg_colors[i])
            zf.writestr(f"ppt/slides/slide{i+1}.xml", xml)
            slide_rel = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
            zf.writestr(f"ppt/slides/_rels/slide{i+1}.xml.rels", slide_rel)

    print(f"✅ PPTX created: {path}")
    print(f"   Size: {os.path.getsize(path) / 1024:.1f} KB")


if __name__ == "__main__":
    output = "2026年AI技术趋势展望.pptx"
    create_pptx(output)
