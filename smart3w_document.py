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

import csv as _csv
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

_MAX_ZIP_ENTRIES = 10_000
_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")
_DIMENSION_RE = re.compile(r"^([A-Z]+\d+)(?::([A-Z]+\d+))?$")


class NotADocument(Exception):
    """目标不是可识别的文档格式。"""


class DocumentError(RuntimeError):
    """文档已识别但解析失败。"""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _max_document_bytes() -> int:
    """文档解析输入上限；非正整数回退 50MB，避免超大文件读满内存。"""
    return _env_int("SMART3W_MAX_DOCUMENT_BYTES", 50 * 1024 * 1024)


def _max_document_text_bytes() -> int:
    """解析后 Markdown 文本上限；防止小输入放大成超大输出。"""
    return _env_int("SMART3W_MAX_DOCUMENT_TEXT_BYTES", 10 * 1024 * 1024)


def _max_table_cells() -> int:
    return _env_int("SMART3W_MAX_TABLE_CELLS", 2_000_000)


def _max_table_columns() -> int:
    return _env_int("SMART3W_MAX_TABLE_COLUMNS", 10_000)


def _max_table_rows() -> int:
    return _env_int("SMART3W_MAX_TABLE_ROWS", 500_000)


def _max_uncompressed_document_bytes() -> int:
    """Office ZIP 文档解压后总大小上限；非正整数回退 200MB。"""
    return _env_int("SMART3W_MAX_UNCOMPRESSED_DOCUMENT_BYTES", 200 * 1024 * 1024)


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """按 UTF-8 字节数截断，避免截断在多字节字符中间。"""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True


def _content_type_base(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _url_suffix(url: str) -> str:
    path = (url or "").split("#", 1)[0].split("?", 1)[0]
    return Path(path).suffix.lower()


def _check_zip_infos(infos: list[zipfile.ZipInfo]) -> None:
    """检查条目数与解压后总量；超限抛 DocumentError。"""
    if len(infos) > _MAX_ZIP_ENTRIES:
        raise DocumentError(f"Office 文档条目过多（{len(infos)} > {_MAX_ZIP_ENTRIES}）")
    total = sum(info.file_size for info in infos)
    limit = _max_uncompressed_document_bytes()
    if total > limit:
        raise DocumentError(f"文档解压后过大（{total} 字节，上限 {limit} 字节）")


def _check_zip_safety(path: Path) -> None:
    """在解析 Office ZIP 文档前检查条目数与解压后总量，防止 ZIP 炸弹。"""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentError(f"无法读取 Office 文档: {exc}") from exc
    _check_zip_infos(infos)


def _zip_kind(path: Path) -> str | None:
    # 安全检查必须在 namelist() 之前：否则超大 ZIP 会先被完整枚举。
    # 仅文件头像 ZIP 但实际损坏（BadZipFile）时回退到 Content-Type/扩展名判断。
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _check_zip_infos(infos)
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


def _cell_position(ref: str) -> tuple[int, int] | None:
    match = _CELL_RE.match(ref.strip().upper())
    if not match:
        return None
    letters, digits = match.groups()
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return int(digits), col


def _dimension_size(reference: str) -> tuple[int, int] | None:
    match = _DIMENSION_RE.match(reference.strip().upper())
    if not match:
        return None
    start, end = match.groups()
    first = _cell_position(start)
    last = _cell_position(end or start)
    if not first or not last:
        return None
    return max(first[0], last[0]), max(first[1], last[1])


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
            if "heading" in lowered or "\u6807\u9898" in style:
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


def _pdf_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    max_text = _max_document_text_bytes()
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
            failed_pages = 0
            total = 0
            truncated = False
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
                    page_text = (page.extract_text() or "").strip()
                except Exception:
                    page_text = ""
                    failed_pages += 1
                pages.append(page_text)
                total += len(page_text.encode("utf-8", errors="replace"))
                if total > max_text:
                    truncated = True
                    break
            if failed_pages:
                # 任一本页抛异常都不静默跳过，交给 pdfplumber 完整重试
                raise RuntimeError(f"pypdf 有 {failed_pages} 页提取失败，改用 pdfplumber")
            text = "\n\n".join(page for page in pages if page)
            meta: dict[str, Any] = {"pages": len(reader.pages)}
            text, cut = _truncate_text(text, max_text)
            if truncated or cut:
                meta["truncated"] = True
            return text, meta
        except DocumentError:
            raise
        except Exception as exc:
            pypdf_error = exc

    try:
        import pdfplumber
    except ImportError as exc:
        if pypdf_error is not None:
            raise DocumentError(f"PDF 解析失败: {pypdf_error}") from pypdf_error
        raise DocumentError("解析 PDF 需要 pypdf 或 pdfplumber，请安装 pypdf") from exc
    try:
        with pdfplumber.open(str(path)) as pdf:
            pages = []
            total = 0
            truncated = False
            for page in pdf.pages:
                page_text = (page.extract_text() or "").strip()
                pages.append(page_text)
                total += len(page_text.encode("utf-8", errors="replace"))
                if total > max_text:
                    truncated = True
                    break
            text = "\n\n".join(page for page in pages if page)
            meta = {"pages": len(pdf.pages)}
            text, cut = _truncate_text(text, max_text)
            if truncated or cut:
                meta["truncated"] = True
    except Exception as exc:
        raise DocumentError(f"PDF 解析失败: {exc}") from exc
    return text, meta


def _xlsx_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise DocumentError("解析 XLSX 需要 openpyxl，请安装 openpyxl") from exc

    blocks: list[str] = []
    sheets: list[str] = []
    max_text = _max_document_text_bytes()
    max_rows = _max_table_rows()
    max_cols = _max_table_columns()
    max_cells = _max_table_cells()
    total_bytes = 0
    truncated = False
    try:
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise DocumentError(f"XLSX 解析失败: {exc}") from exc
    try:
        for worksheet in workbook.worksheets:
            try:
                dimension = worksheet.calculate_dimension()
            except Exception:
                dimension = ""
            size = _dimension_size(dimension) if dimension else None
            if size and (
                size[0] > max_rows
                or size[1] > max_cols
                or size[0] * size[1] > max_cells
            ):
                raise DocumentError(
                    f"XLSX 工作表过大（{worksheet.title}: {size[0]} 行 x {size[1]} 列）"
                )
            rows: list[list[str]] = []
            cell_count = 0
            for row in worksheet.iter_rows(values_only=True):
                if len(rows) >= max_rows:
                    truncated = True
                    break
                values = ["" if value is None else str(value) for value in row]
                while values and not values[-1]:
                    values.pop()
                if any(values):
                    if len(values) > max_cols:
                        raise DocumentError(
                            f"XLSX 工作表列数过多（{worksheet.title}: {len(values)} > {max_cols}）"
                        )
                    cell_count += len(values)
                    if cell_count > max_cells:
                        raise DocumentError(
                            f"XLSX 工作表单元格过多（{worksheet.title}: 超过 {max_cells}）"
                        )
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
            block = f"## {worksheet.title}\n\n" + "\n".join(lines)
            total_bytes += len(block.encode("utf-8", errors="replace"))
            if total_bytes > max_text:
                truncated = True
                break
            blocks.append(block)
            sheets.append(worksheet.title)
    finally:
        workbook.close()
    markdown = "\n\n".join(blocks)
    meta: dict[str, Any] = {"sheets": sheets}
    if truncated:
        meta["truncated"] = True
    return markdown, meta


def _pptx_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover
        raise DocumentError("解析 PPTX 需要 python-pptx，请安装 python-pptx") from exc

    try:
        presentation = Presentation(str(path))
    except Exception as exc:
        raise DocumentError(f"PPTX 解析失败: {exc}") from exc

    max_text = _max_document_text_bytes()
    slides: list[str] = []
    total_bytes = 0
    truncated = False
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
        block = f"## Slide {index}\n\n{body}" if body else f"## Slide {index}"
        total_bytes += len(block.encode("utf-8", errors="replace"))
        if total_bytes > max_text:
            truncated = True
            break
        slides.append(block)
    meta: dict[str, Any] = {"slides": len(slides)}
    if truncated:
        meta["truncated"] = True
    return "\n\n".join(slides), meta


_CSV_ENCODINGS = ("utf-8-sig", "gb18030", "big5", "shift_jis", "cp1252")


def _decode_csv(raw: bytes) -> str:
    # BOM 明确的编码优先，避免探测被短样本误导。
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    # 优先用 charset-normalizer 做编码探测；限定候选集，防止把 GBK 误判成
    # CP949/EUC-JP 等同样能解码但语义错误的编码。未安装时回退常见编码。
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw, cp_isolation=list(_CSV_ENCODINGS)).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    for encoding in _CSV_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8-sig", errors="replace")


def _csv_to_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    max_text = _max_document_text_bytes()
    max_rows = _max_table_rows()
    max_cols = _max_table_columns()
    max_cells = _max_table_cells()
    try:
        raw = path.read_bytes()
    except OSError as exc:  # pragma: no cover
        raise DocumentError(f"CSV 解析失败: {exc}") from exc
    text = _decode_csv(raw)
    rows: list[list[str]] = []
    cell_count = 0
    truncated = False
    for record in _csv.reader(io.StringIO(text)):
        if len(rows) >= max_rows:
            truncated = True
            break
        if len(record) > max_cols:
            raise DocumentError(f"CSV 列数过多（{len(record)} > {max_cols}）")
        values = [value.replace("|", "\\|").replace("\n", "<br>") for value in record]
        while values and not values[-1]:
            values.pop()
        if any(values):
            cell_count += len(values)
            if cell_count > max_cells:
                raise DocumentError(f"CSV 单元格过多（超过 {max_cells}）")
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
    markdown, cut = _truncate_text("\n".join(lines), max_text)
    meta: dict[str, Any] = {"rows": len(rows) - 1}
    if truncated or cut:
        meta["truncated"] = True
    return markdown, meta


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
    if kind in {"docx", "xlsx", "pptx"}:
        _check_zip_safety(source)

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
    markdown, cut = _truncate_text(markdown, _max_document_text_bytes())
    if cut:
        meta = dict(meta)
        meta["truncated"] = True
    return {"kind": kind, "markdown": markdown, **meta}
