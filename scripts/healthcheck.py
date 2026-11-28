#!/usr/bin/env python3
"""容器健康检查：按 mcp_server 相同的规则归一化 SMART3W_PORT 后探测端口。

SMART3W_PORT 非整数时回退 50826，越界时夹取到 1..65535；避免健康检查
因一个非法环境变量报 ValueError 或探测错误端口。
"""

from __future__ import annotations

import os
import socket
import sys

DEFAULT_PORT = 50826


def _normalize_port(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return max(1, min(65535, value))


def main() -> int:
    host = (os.environ.get("SMART3W_HOST") or "0.0.0.0").strip()
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = _normalize_port(os.environ.get("SMART3W_PORT", str(DEFAULT_PORT)))
    try:
        socket.create_connection((host, port), 3).close()
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
