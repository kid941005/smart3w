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


def test_extract_gbk_csv_document(tmp_path):
    path = tmp_path / "gbk.csv"
    path.write_bytes("名称,数量\n苹果,3\n".encode("gbk"))
    result = extract_document_file(path)
    assert result["kind"] == "csv"
    assert "| 名称 | 数量 |" in result["markdown"]
    assert "| 苹果 | 3 |" in result["markdown"]


def test_extract_big5_csv_document(tmp_path):
    path = tmp_path / "big5.csv"
    path.write_bytes("名稱,數量\n蘋果,3\n".encode("big5"))
    result = extract_document_file(path)
    assert result["kind"] == "csv"
    assert "名稱" in result["markdown"]
    assert "蘋果" in result["markdown"]


def test_csv_column_limit_rejects_wide_table(tmp_path):
    path = tmp_path / "wide.csv"
    header = ",".join(f"c{i}" for i in range(10001))
    path.write_text(header + "\n", encoding="utf-8")
    with pytest.raises(DocumentError, match="列数"):
        extract_document_file(path)


def test_csv_output_truncates_at_text_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART3W_MAX_DOCUMENT_TEXT_BYTES", "20")
    path = tmp_path / "long.csv"
    path.write_text("name,value\nalpha," + "x" * 200 + "\n", encoding="utf-8")
    result = extract_document_file(path)
    assert result["truncated"] is True
    assert len(result["markdown"].encode("utf-8")) <= 20
