#!/usr/bin/env python3
"""document_extract.py — 将 PDF/Office 文档解析为 Markdown 的命令行入口。

退出码：0=成功，3=不是文档（调用方按普通网页处理），1=文档解析失败。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart3w_document import DocumentError, NotADocument, extract_document_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract PDF/Office documents to Markdown")
    parser.add_argument("input")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--content-type", default="")
    parser.add_argument("--url", default="")
    args = parser.parse_args(argv)

    try:
        result = extract_document_file(args.input, args.content_type, args.url)
    except NotADocument as exc:
        print(f"not a document: {exc}", file=sys.stderr)
        return 3
    except DocumentError as exc:
        print(f"document parse failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"document parse failed: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output) if args.output else Path(args.input).with_suffix(".md")
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_name(output.name + ".part")
    try:
        part.write_text(result["markdown"], encoding="utf-8")
        os.replace(part, output)
    finally:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass

    summary = {key: value for key, value in result.items() if key != "markdown"}
    summary["chars"] = len(result["markdown"])
    print("__SMART3W_DOCUMENT__ " + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
