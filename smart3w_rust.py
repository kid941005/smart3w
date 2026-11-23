"""Rust 加速后端桥接（可选）。

通过子进程调用 smart3w-rs 二进制，在 JSON 信封上交换数据；二进制缺失、
超时或返回失败时统一返回 None，由调用方回退到纯 Python 实现，保证功能不退化。

环境变量：
- SMART3W_RUST: auto（默认，有二进制就用）/ on / off
- SMART3W_RS_BIN: 显式指定二进制路径
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

_DEFAULT_TIMEOUT = 90.0
_DEFAULT_MAX_BYTES = 64 * 1024 * 1024
_BINARY_CACHE: dict[str, str | None] = {}


def _max_bytes() -> int:
    """单次输入/输出上限；非正整数回退 64MB，避免超大文档撑爆子进程管道。"""
    raw = os.environ.get("SMART3W_MAX_DOCUMENT_BYTES")
    if raw is None:
        return _DEFAULT_MAX_BYTES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BYTES
    return value if value > 0 else _DEFAULT_MAX_BYTES


def _meta_int(value: object) -> int:
    """把 Rust meta 中的数字宽容地转成 int；非法值按 0 处理。"""
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return 0


def _mode() -> str:
    return (os.environ.get("SMART3W_RUST") or "auto").strip().lower()


def _candidate_paths() -> list[str]:
    exe = "smart3w-rs.exe" if os.name == "nt" else "smart3w-rs"
    here = Path(__file__).resolve().parent
    candidates: list[str] = []
    env_bin = os.environ.get("SMART3W_RS_BIN")
    if env_bin:
        candidates.append(env_bin)
    candidates.extend(
        str(p)
        for p in (
            here / "smart3w_rs" / exe,
            here / "bin" / exe,
            here / "rust" / "target" / "release" / exe,
            here / "rust" / "target" / "debug" / exe,
        )
    )
    found = shutil.which("smart3w-rs")
    if found:
        candidates.append(found)
    return candidates


def binary_path() -> str | None:
    mode = _mode()
    if mode == "off":
        return None
    env_bin = os.environ.get("SMART3W_RS_BIN") or ""
    cache_key = f"{mode}|{env_bin}"
    if cache_key in _BINARY_CACHE:
        return _BINARY_CACHE[cache_key]
    resolved: str | None = None
    for candidate in _candidate_paths():
        if candidate and Path(candidate).is_file():
            resolved = candidate
            break
    _BINARY_CACHE[cache_key] = resolved
    return resolved


def available() -> bool:
    return binary_path() is not None


def _invoke(command: str, payload: bytes, timeout: float = _DEFAULT_TIMEOUT) -> dict | None:
    exe = binary_path()
    if not exe:
        return None
    limit = _max_bytes()
    if len(payload) > limit:
        return None
    try:
        proc = subprocess.run(
            [exe, command],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    if len(proc.stdout) > limit:
        return None
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    return data


def _markdown(payload: dict | None) -> str | None:
    if not payload:
        return None
    markdown = (payload.get("markdown") or "").strip()
    return markdown or None


def html_to_markdown(html: str) -> str | None:
    if not html or not html.strip():
        return None
    return _markdown(_invoke("html2md", html.encode("utf-8")))


def wechat_to_markdown(html: str) -> str | None:
    if not html or not html.strip():
        return None
    return _markdown(_invoke("wechat2md", html.encode("utf-8")))


def pdf_to_markdown(pdf_bytes: bytes) -> tuple[str, int] | None:
    payload = _invoke("pdf2md", pdf_bytes)
    markdown = _markdown(payload)
    if not markdown or payload is None:
        return None
    meta = payload.get("meta") or {}
    return markdown, _meta_int(meta.get("pages"))


def xlsx_to_markdown(xlsx_bytes: bytes) -> tuple[str, list[str]] | None:
    payload = _invoke("xlsx2md", xlsx_bytes)
    markdown = _markdown(payload)
    if not markdown or payload is None:
        return None
    meta = payload.get("meta") or {}
    sheets = meta.get("sheets") or []
    return markdown, [str(s) for s in sheets]


def csv_to_markdown(csv_bytes: bytes) -> tuple[str, int] | None:
    payload = _invoke("csv2md", csv_bytes)
    markdown = _markdown(payload)
    if not markdown or payload is None:
        return None
    meta = payload.get("meta") or {}
    return markdown, _meta_int(meta.get("rows"))


def doctor() -> dict:
    exe = binary_path()
    if not exe:
        return {"available": False, "path": None, "mode": _mode()}
    payload = _invoke("doctor", b"")
    return {"available": True, "path": exe, "mode": _mode(), "info": payload}
