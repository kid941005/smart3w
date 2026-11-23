#!/usr/bin/env python3
"""PDF / Office 文档解析，统一输出 Markdown。

支持的格式：
- PDF（优先 Rust 后端，回退 pypdf/pdfplumber）
- DOCX（python-docx）
- XLSX（优先 Rust 后端，回退 openpyxl）
- PPTX（python-pptx）
- CSV（优先 Rust 后端，回退标准库 csv）

旧版二进制 .doc/.xls/.ppt 不在支持范围内，会给出明确错误，而不是把二进制当网页压缩。
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path
from typing import Any

__all__ = [
    "DocumentError",
    "NotADocument",
    "detect_kind",
    "extract_document_file",
]

_CONTENT_TYPE_KIND = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "text/csv": "csv",
    "application/csv": "csv",
}

_EXTENSION_KIND = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".doc": "doc",
    ".xls": "xls",
    ".ppt": "ppt",
    ".csv": "csv",
}


class NotADocument(Exception):
    """目标不是可识别的文档格式。"""


class DocumentError(RuntimeError):
    """文档已识别但解析失败。"""


def _content_type_base(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _url_suffix(url: str) -> str:
    path = (url or "").split("#", 1)[0].split("?", 1)[0]
    return Path(path).suffix.lower()


def _zip_kind(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return None
    if "word/document.xml" in names or any(name.startswith("word/") for name in names):
        return "docx"
    if "xl/workbook.xml" in names or any(name.startswith("xl/") for name in names):
        return "xlsx"
    if "ppt/presentation.xml" in names or any(name.startswith("ppt/") for name in names):
        return "pptx"
    return None


def detect_kind(path: str | Path, content_type: str = "", url: str = "") -> str | None:
    """按文件头、Content-Type、URL 扩展名识别文档类型；非文档返回 None。"""
    source = Path(path)
    try:
        with source.open("rb") as handle:
            head = handle.read(512)
    except OSError:
        return None

    # HTML 不能被 Content-Type/扩展名误判为 PDF 等文档；先排除明显 HTML 文档
    probe = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if probe.startswith(b"<!doctype html") or probe.startswith(b"<html"):
        return None

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        kind = _zip_kind(source)
        if kind:
            return kind

    declared = _CONTENT_TYPE_KIND.get(_content_type_base(content_type))
    if declared:
        return declared
    by_path = _EXTENSION_KIND.get(source.suffix.lower())
    if by_path:
        return by_path
    return _EXTENSION_KIND.get(_url_suffix(url))


def _table_to_markdown(table: Any) -> str:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            text = (cell.text or "").strip().replace("|", "\\|").replace("\n", "<br>")
            cells.append(text)
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    for row in rows:
        row.extend([""] * (width - len(row)))
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def _docx_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - 环境缺失时提示明确
        raise DocumentError("解析 DOCX 需要 python-docx，请安装 python-docx") from exc

    document = Document(str(path))
    blocks: list[str] = []
    paragraphs = 0
    tables = 0
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = (paragraph.text or "").strip()
            if not text:
                continue
            paragraphs += 1
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            lowered = style.lower()
            if "heading" in lowered or "标题" in style:
                match = re.search(r"(\d+)", style)
                level = min(max(int(match.group(1)) if match else 1, 1), 6)
                blocks.append("#" * level + " " + text)
            else:
                blocks.append(text)
        elif tag == "tbl":
            tables += 1
            markdown = _table_to_markdown(Table(child, document))
            if markdown:
                blocks.append(markdown)
    return "\n\n".join(blocks), {"paragraphs": paragraphs, "tables": tables}


def _max_document_bytes() -> int:
    """文档解析输入上限；非正整数回退 50MB，避免超大文件读满内存。"""
    raw = os.environ.get("SMART3W_MAX_DOCUMENT_BYTES")
    if raw is None:
        return 50 * 1024 * 1024
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 50 * 1024 * 1024
    return value if value > 0 else 50 * 1024 * 1024


def _pdf_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    pypdf_error: Exception | None = None
    PdfReader: Any = None
    try:
        from pypdf import PdfReader as _ImportedPdfReader
        PdfReader = _ImportedPdfReader
    except ImportError:
        pass

    if PdfReader is not None:
        try:
            pages: list[str] = []
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    decrypted = reader.decrypt("")
                except Exception as exc:
                    raise DocumentError("PDF 已加密，无法解析") from exc
                if not decrypted:
                    raise DocumentError("PDF 已加密，无法解析")
            for page in reader.pages:
                try:
                    pages.append((page.extract_text() or "").strip())
                except Exception:
                    pages.append("")
            text = "\n\n".join(page for page in pages if page)
            return text, {"pages": len(pages)}
        except DocumentError:
            raise
        except Exception as exc:
            # pypdf 对部分损坏/加密变体较脆弱，继续尝试 pdfplumber
            pypdf_error = exc

    try:
        import pdfplumber
    except ImportError as exc:
        if pypdf_error is not None:
            raise DocumentError(f"PDF 解析失败: {pypdf_error}") from pypdf_error
        raise DocumentError("解析 PDF 需要 pypdf 或 pdfplumber，请安装 pypdf") from exc
    try:
        with pdfplumber.open(str(path)) as pdf:
            pages = [((page.extract_text() or "").strip()) for page in pdf.pages]
    except Exception as exc:
        raise DocumentError(f"PDF 解析失败: {exc}") from exc
    text = "\n\n".join(page for page in pages if page)
    return text, {"pages": len(pages)}


def _xlsx_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise DocumentError("解析 XLSX 需要 openpyxl，请安装 openpyxl") from exc

    blocks: list[str] = []
    sheets: list[str] = []
    try:
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise DocumentError(f"XLSX 解析失败: {exc}") from exc
    try:
        for worksheet in workbook.worksheets:
            rows: list[list[str]] = []
            for row in worksheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                while values and not values[-1]:
                    values.pop()
                if any(values):
                    rows.append([value.replace("|", "\\|").replace("\n", "<br>") for value in values])
            if not rows:
                continue
            width = max(len(row) for row in rows)
            for row in rows:
                row.extend([""] * (width - len(row)))
            lines = [
                "| " + " | ".join(rows[0]) + " |",
                "| " + " | ".join(["---"] * width) + " |",
            ]
            lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
            blocks.append(f"## {worksheet.title}\n\n" + "\n".join(lines))
            sheets.append(worksheet.title)
    finally:
        workbook.close()
    return "\n\n".join(blocks), {"sheets": sheets}


def _pptx_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover
        raise DocumentError("解析 PPTX 需要 python-pptx，请安装 python-pptx") from exc

    try:
        presentation = Presentation(str(path))
    except Exception as exc:
        raise DocumentError(f"PPTX 解析失败: {exc}") from exc

    slides: list[str] = []
    for index, slide in enumerate(presentation.slides, 1):
        parts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = "\n".join(
                    paragraph.text
                    for paragraph in shape.text_frame.paragraphs
                    if (paragraph.text or "").strip()
                ).strip()
                if text:
                    parts.append(text)
            if getattr(shape, "has_table", False):
                markdown = _table_to_markdown(shape.table)
                if markdown:
                    parts.append(markdown)
        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
            if notes:
                parts.append("Notes:\n" + notes)
        body = "\n\n".join(parts)
        slides.append(f"## Slide {index}\n\n{body}" if body else f"## Slide {index}")
    return "\n\n".join(slides), {"slides": len(slides)}


def _csv_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    import csv as _csv

    rows: list[list[str]] = []
    try:
        raw = path.read_bytes()
    except OSError as exc:  # pragma: no cover
        raise DocumentError(f"CSV 解析失败: {exc}") from exc
    text = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8-sig", errors="replace")
    for record in _csv.reader(io.StringIO(text)):
        values = [value.replace("|", "\\|").replace("\n", "<br>") for value in record]
        while values and not values[-1]:
            values.pop()
        if any(values):
            rows.append(values)
    if not rows:
        raise DocumentError("CSV 未提取到内容")
    width = max(len(row) for row in rows)
    for row in rows:
        row.extend([""] * (width - len(row)))
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines), {"rows": len(rows) - 1}


def _extract_with_rust(kind: str, path: Path) -> tuple[str, dict[str, Any]] | None:
    """尝试用 Rust 后端解析文档；不可用或失败时返回 None，由调用方回退纯 Python。"""
    try:
        import smart3w_rust
    except Exception:
        return None
    if not smart3w_rust.available():
        return None
    if kind not in ("pdf", "xlsx", "csv"):
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if kind == "pdf":
        pdf_result = smart3w_rust.pdf_to_markdown(data)
        if pdf_result:
            markdown, pages = pdf_result
            return markdown, {"pages": pages}
    elif kind == "xlsx":
        xlsx_result = smart3w_rust.xlsx_to_markdown(data)
        if xlsx_result:
            markdown, sheets = xlsx_result
            return markdown, {"sheets": sheets}
    elif kind == "csv":
        csv_result = smart3w_rust.csv_to_markdown(data)
        if csv_result:
            markdown, rows = csv_result
            return markdown, {"rows": rows}
    return None


_EXTRACTORS = {
    "docx": _docx_to_markdown,
    "xlsx": _xlsx_to_markdown,
    "pptx": _pptx_to_markdown,
    "csv": _csv_to_markdown,
}


def extract_document_file(
    path: str | Path,
    content_type: str = "",
    url: str = "",
) -> dict[str, Any]:
    """解析一个文档文件，返回 {kind, markdown, pages?/sheets?/slides?}。

    非文档抛 NotADocument；识别为旧版 Office 或解析失败抛 DocumentError。
    """
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise DocumentError(f"无法读取文档: {exc}") from exc
    limit = _max_document_bytes()
    if size > limit:
        raise DocumentError(f"文档过大（{size} 字节，上限 {limit} 字节）")
    kind = detect_kind(source, content_type=content_type, url=url)
    if kind is None:
        raise NotADocument("不是支持的文档格式")
    if kind in {"doc", "xls", "ppt"}:
        raise DocumentError(f"暂不支持旧版 Office .{kind} 文件，请另存为 .{kind}x 后重试")

    extracted = _extract_with_rust(kind, source)
    if extracted is not None:
        markdown, meta = extracted
    elif kind == "pdf":
        markdown, meta = _pdf_to_markdown(source)
    else:
        extractor = _EXTRACTORS.get(kind)
        if extractor is None:
            raise NotADocument(f"不支持的文档类型: {kind}")
        markdown, meta = extractor(source)

    markdown = (markdown or "").strip()
    if not markdown:
        raise DocumentError("文档未提取到文本（可能是扫描件/纯图片，需要 OCR）")
    return {"kind": kind, "markdown": markdown, **meta}
