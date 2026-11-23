"""延迟代理到 mcp_server，避免工具子模块与入口模块的循环导入。

拆分后的工具实现需要访问 mcp_server 的常量、缓存、任务表和抓取管线；
测试也依赖 monkeypatch mcp_server 上的属性。这里通过 __getattr__ 在真正
访问时才导入 mcp_server，既能保持动态可替换，也不会在 import 期形成环。
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any


def _target() -> Any:
    """优先返回当前入口模块，兼容 `python mcp_server.py` 直接启动。"""
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    if main_file and os.path.basename(str(main_file)) == "mcp_server.py":
        return main
    return importlib.import_module("mcp_server")


class _ServerProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(_target(), name)


srv = _ServerProxy()
