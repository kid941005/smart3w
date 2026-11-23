"""Rust 加速后端测试。

二进制未构建（rust/target/release/smart3w-rs）时整组跳过；构建后覆盖
html2md / pdf2md / xlsx2md / csv2md / doctor 以及 off 模式回退。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import smart3w_rust  # noqa: E402

FIXTURE_PDF = Path(__file__).resolve().parent / "fixtures" / "sample.pdf"

pytestmark = pytest.mark.skipif(
    not smart3w_rust.available(), reason="smart3w-rs 二进制未构建（cargo build --release）"
)


def test_doctor_reports_ok():
    info = smart3w_rust.doctor()
    assert info["available"] is True
    assert info["path"]
    assert info["info"] and info["info"].get("ok") is True


def test_html_to_markdown_basic():
    html = (
        "<html><head><title>Demo</title></head><body><article>"
        "<h1>Hello</h1><p>World <a href=\"https://example.com\">link</a></p>"
        "<ul><li>a</li><li>b</li></ul></article></body></html>"
    )
    md = smart3w_rust.html_to_markdown(html)
    assert md
    assert "Hello" in md
    assert "World" in md
    assert "https://example.com" in md


def test_html_empty_returns_none():
    assert smart3w_rust.html_to_markdown("   ") is None


def test_pdf_to_markdown_fixture():
    result = smart3w_rust.pdf_to_markdown(FIXTURE_PDF.read_bytes())
    assert result is not None
    markdown, pages = result
    assert "Hello PDF" in markdown
    assert pages == 1


def test_xlsx_to_markdown(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "sample.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["Name", "Value"])
    sheet.append(["alpha", 42])
    workbook.save(str(path))
    result = smart3w_rust.xlsx_to_markdown(path.read_bytes())
    assert result is not None
    markdown, sheets = result
    assert sheets == ["Data"]
    assert "## Data" in markdown
    assert "| Name | Value |" in markdown
    assert "| alpha | 42 |" in markdown


def test_csv_to_markdown():
    result = smart3w_rust.csv_to_markdown(b"Name,Value\nalpha,42\n")
    assert result is not None
    markdown, rows = result
    assert "| Name | Value |" in markdown
    assert "| alpha | 42 |" in markdown
    assert rows == 1


def test_off_mode_disables_backend(monkeypatch):
    monkeypatch.setenv("SMART3W_RUST", "off")
    smart3w_rust._BINARY_CACHE.clear()
    try:
        assert smart3w_rust.available() is False
        assert smart3w_rust.html_to_markdown("<p>x</p>") is None
    finally:
        monkeypatch.delenv("SMART3W_RUST", raising=False)
        smart3w_rust._BINARY_CACHE.clear()
