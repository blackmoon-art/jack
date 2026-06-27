"""文档解析工具 — 提取 PDF/DOCX/XLSX/CSV/TXT 的文字和表格内容。"""

import csv
import logging
from pathlib import Path

logger = logging.getLogger("nano_agent.tools.document_parse")


class DocumentParser:
    TOOLS = [
        ("parse_document", "Parse and extract text/data from uploaded documents: "
         "PDF, Word (.docx), Excel (.xlsx), CSV, and plain text. "
         "Returns the full text content for the agent to analyze.",
         "parse_document",
         {"path": {"type": "string", "description": "Path to the document file in workspace"}},
         ["path"]),
    ]

    def __init__(self, work_dir: str):
        self.work_dir = Path(work_dir).resolve()

    def parse_document(self, path: str) -> str:
        """解析文档：自动检测类型，提取全部文字内容。"""
        try:
            filepath = (self.work_dir / path).resolve()
            filepath.relative_to(self.work_dir)
        except (ValueError, OSError):
            return "Error: Access denied"

        if not filepath.exists():
            return f"Error: File not found — {path}"

        ext = filepath.suffix.lower()
        size_mb = filepath.stat().st_size / (1024 * 1024)

        try:
            if ext == ".pdf":
                return self._parse_pdf(filepath)
            elif ext in (".docx", ".doc"):
                return self._parse_docx(filepath)
            elif ext in (".xlsx", ".xls"):
                return self._parse_excel(filepath)
            elif ext == ".csv":
                return self._parse_csv(filepath)
            elif ext in (".txt", ".md", ".py", ".json", ".xml", ".html", ".htm",
                         ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log"):
                return self._parse_text(filepath)
            else:
                # 尝试当纯文本读
                try:
                    return self._parse_text(filepath)
                except Exception:
                    return f"Error: Unsupported format '{ext}'. Supported: PDF, DOCX, XLSX, CSV, TXT"
        except Exception as e:
            logger.error(f"Parse error for {path}: {e}")
            return f"Error parsing document: {e}"

    def _parse_pdf(self, filepath: Path) -> str:
        """解析 PDF，优先 pdfplumber，回退 PyPDF2。"""
        text_parts = []
        try:
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                for i, page in enumerate(pdf.pages):
                    t = page.extract_text()
                    if t:
                        text_parts.append(f"--- Page {i+1} ---\n{t}")
            if text_parts:
                return f"[PDF: {filepath.name}]\n\n" + "\n\n".join(text_parts)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"pdfplumber failed: {e}")

        # 回退 PyPDF2
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(filepath)
            for i, page in enumerate(reader.pages):
                t = page.extract_text()
                if t:
                    text_parts.append(f"--- Page {i+1} ---\n{t}")
            return f"[PDF: {filepath.name}]\n\n" + "\n\n".join(text_parts)
        except Exception as e:
            return f"Error reading PDF: {e}"

    def _parse_docx(self, filepath: Path) -> str:
        """解析 Word 文档。"""
        try:
            from docx import Document
            doc = Document(filepath)
            text_parts = []
            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text)
            # 也提取表格
            for ti, table in enumerate(doc.tables):
                text_parts.append(f"\n[Table {ti+1}]")
                for row in table.rows:
                    cells = [cell.text for cell in row.cells]
                    text_parts.append(" | ".join(cells))
            return f"[DOCX: {filepath.name}]\n\n" + "\n".join(text_parts)
        except ImportError:
            return "Error: python-docx not installed. Run: pip install python-docx"
        except Exception as e:
            return f"Error reading DOCX: {e}"

    def _parse_excel(self, filepath: Path) -> str:
        """解析 Excel 表格，输出为 markdown 表格。"""
        try:
            from openpyxl import load_workbook
            wb = load_workbook(filepath, data_only=True, read_only=True)
            text_parts = [f"[Excel: {filepath.name}]"]

            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows = []
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 100:
                        text_parts.append(f"(Showing first 100 rows, {ws.max_row or '?'} total)")
                        break
                    if any(c is not None for c in row):
                        rows.append(row)

                if not rows:
                    continue

                text_parts.append(f"\n## Sheet: {sheet_name}")

                # 格式化为 markdown table
                max_cols = max(len(r) for r in rows)
                for i, row in enumerate(rows):
                    cells = [str(c) if c is not None else "" for c in row]
                    # Pad to max_cols
                    cells += [""] * (max_cols - len(cells))
                    text_parts.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        text_parts.append("|" + "|".join(["---"] * max_cols) + "|")

            wb.close()
            return "\n".join(text_parts)
        except ImportError:
            return "Error: openpyxl not installed. Run: pip install openpyxl"
        except Exception as e:
            return f"Error reading Excel: {e}"

    def _parse_csv(self, filepath: Path) -> str:
        """解析 CSV 文件。"""
        try:
            with open(filepath, encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                rows = list(reader)

            text_parts = [f"[CSV: {filepath.name}]\n"]
            if len(rows) > 200:
                text_parts.append(f"(Showing first 200 of {len(rows)} rows)")
                rows = rows[:200]

            max_cols = max(len(r) for r in rows) if rows else 0
            for i, row in enumerate(rows):
                cells = [str(c) for c in row]
                cells += [""] * (max_cols - len(cells))
                text_parts.append("| " + " | ".join(cells) + " |")
                if i == 0:
                    text_parts.append("|" + "|".join(["---"] * max_cols) + "|")

            return "\n".join(text_parts)
        except UnicodeDecodeError:
            return "Error: CSV encoding not supported (try UTF-8)"
        except Exception as e:
            return f"Error reading CSV: {e}"

    def _parse_text(self, filepath: Path) -> str:
        """解析纯文本文件。"""
        try:
            content = filepath.read_text(encoding="utf-8")
            if len(content) > 50000:
                content = content[:50000] + "\n...(truncated)"
            return f"[{filepath.suffix.upper().lstrip('.')}: {filepath.name}]\n\n{content}"
        except UnicodeDecodeError:
            try:
                content = filepath.read_text(encoding="gbk")
                return f"[{filepath.name}]\n\n{content}"
            except Exception:
                return f"Error: Cannot decode file as text"
