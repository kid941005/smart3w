#!/usr/bin/env python3
"""Smart3W CLI 包装层：参数解析 + stdout/stderr 约定。

真正的搜索 / Sitemap / 正文压缩逻辑位于仓库根目录 smart3w_core.py，
MCP 服务与 fetch.sh 共用同一份实现，避免逻辑重复。
"""

import argparse
import sys
from pathlib import Path

# 使脚本无论从哪个 cwd 启动都能引用仓库根目录的公共模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart3w_core import (  # noqa: E402
    SEARCH_TIME_RANGES,
    cmd_search,
    cmd_sitemap,
    compress_html,
    compress_wechat,
)


def _ensure_utf8_stdio():
    """Windows/GBK 环境下强制 stdout/stderr 使用 UTF-8，避免 JSON/日志编码报错。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv=None):
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="smart3w_utils", description="Smart3W 搜索/Sitemap/压缩工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="SearXNG 搜索，输出 JSON")
    p_search.add_argument("query")
    p_search.add_argument("count", type=int, nargs="?", default=10)
    p_search.add_argument("instance", nargs="?", default=None)
    p_search.add_argument("--language", default="zh-CN", help="语言，如 zh-CN / en-US（留空表示不指定）")
    p_search.add_argument("--time-range", dest="time_range", default="",
                          choices=SEARCH_TIME_RANGES, help="时间范围: day/week/month/year（空表示不限）")
    p_search.add_argument("--categories", default="", help="搜索分类，逗号分隔，如 general,news")
    p_search.add_argument("--safesearch", type=int, default=0, choices=[0, 1, 2],
                          help="安全搜索: 0=关闭 1=中等 2=严格")
    p_search.add_argument("--engines", default="", help="指定引擎，逗号分隔，如 google,bing")

    p_sitemap = sub.add_parser("sitemap", help="解析 Sitemap")
    p_sitemap.add_argument("url")
    p_sitemap.add_argument("max_urls", type=int, nargs="?", default=50)
    p_sitemap.add_argument("--recursive", action="store_true", help="递归展开 Sitemap Index（最多 2 层）")

    for name, func in (("compress", compress_html), ("compress-wechat", compress_wechat)):
        p = sub.add_parser(name, help="正文压缩为 Markdown")
        p.add_argument("input")
        p.add_argument("output")
        p.set_defaults(_func=func)

    args = parser.parse_args(argv)

    if args.command == "search":
        ok = cmd_search(args.query, args.count, args.instance, language=args.language,
                        time_range=args.time_range, categories=args.categories,
                        safesearch=args.safesearch, engines=args.engines)
        sys.exit(0 if ok else 1)
    elif args.command == "sitemap":
        cmd_sitemap(args.url, args.max_urls, recursive=args.recursive)
    else:
        ok = args._func(args.input, args.output)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
