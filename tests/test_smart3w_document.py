"""smart3w_document 文档解析测试（无网络依赖）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart3w_document import (  # noqa: E402
    DocumentError,
    NotADocument,
    detect_kind,
    extract_document_file,
)

FIXTURE_PDF = Path(__file__).resolve().parent / "fixtures" / "sample.pdf"


def test_detect_pdf_by_magic():
    assert detect_kind(FIXTURE_PDF) == "pdf"


def test_detect_kind_by_content_type_and_extension(tmp_path):
    path = tmp_path / "unknown.bin"
    path.write_bytes(b"PK\x03\x04not-a-real-zip")
    assert detect_kind(path, content_type="application/pdf") == "pdf"
    assert detect_kind(path, url="https://example.com/a.PPTX?x=1") == "pptx"
    assert detect_kind(path) is None


def test_not_a_document_raises(tmp_path):
    path = tmp_path / "page.html"
    path.write_text("<html><body>hi</body></html>", encoding="utf-8")
    with pytest.raises(NotADocument):
        extract_document_file(path)


def test_legacy_office_rejected(tmp_path):
    path = tmp_path / "old.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0legacy")
    with pytest.raises(DocumentError, match="旧版 Office"):
        extract_document_file(path, content_type="application/msword")


def test_extract_pdf():
    result = extract_document_file(FIXTURE_PDF)
    assert result["kind"] == "pdf"
    assert "Hello PDF" in result["markdown"]
    assert result["pages"] == 1


def test_extract_docx(tmp_path):
    from docx import Document

    path = tmp_path / "sample.docx"
    document = Document()
    document.add_heading("Sample Title", level=1)
    document.add_paragraph("Hello DOCX paragraph.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "alpha"
    table.cell(1, 1).text = "42"
    document.save(str(path))

    result = extract_document_file(path)
    assert result["kind"] == "docx"
    assert "# Sample Title" in result["markdown"]
    assert "Hello DOCX paragraph." in result["markdown"]
    assert "| Name | Value |" in result["markdown"]
    assert result["paragraphs"] == 2
    assert result["tables"] == 1


def test_extract_xlsx(tmp_path):
    import openpyxl

    path = tmp_path / "sample.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["Name", "Value"])
    sheet.append(["alpha", 42])
    workbook.save(str(path))

    result = extract_document_file(path)
    assert result["kind"] == "xlsx"
    assert "## Data" in result["markdown"]
    assert "| Name | Value |" in result["markdown"]
    assert result["sheets"] == ["Data"]


def test_extract_pptx(tmp_path):
    from pptx import Presentation

    path = tmp_path / "sample.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    box = slide.shapes.add_textbox(0, 0, 4000000, 1000000)
    box.text_frame.text = "Hello PPTX"
    presentation.save(str(path))

    result = extract_document_file(path)
    assert result["kind"] == "pptx"
    assert "## Slide 1" in result["markdown"]
    assert "Hello PPTX" in result["markdown"]
    assert result["slides"] == 1


def test_empty_pdf_reports_ocr_hint(tmp_path):
    from pypdf import PdfWriter

    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(DocumentError, match="OCR"):
        extract_document_file(path)
