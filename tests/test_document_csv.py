"""CSV 文档识别与解析测试（Rust 可用或回退纯 Python 均应通过）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smart3w_document import DocumentError, detect_kind, extract_document_file  # noqa: E402


def test_detect_csv_by_extension(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    assert detect_kind(path) == "csv"


def test_detect_csv_by_content_type(tmp_path):
    path = tmp_path / "download.bin"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    assert detect_kind(path, content_type="text/csv; charset=utf-8") == "csv"


def test_extract_csv_document(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("Name,Value\nalpha,42\n", encoding="utf-8")
    result = extract_document_file(path)
    assert result["kind"] == "csv"
    assert "| Name | Value |" in result["markdown"]
    assert "| alpha | 42 |" in result["markdown"]
    assert result["rows"] == 1


def test_extract_csv_empty_raises(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("\n\n", encoding="utf-8")
    with pytest.raises(DocumentError):
        extract_document_file(path)
