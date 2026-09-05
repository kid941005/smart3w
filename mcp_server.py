#!/usr/bin/env python3
"""Smart3W MCP Server - 将 smart3w 搜索与网页抓取能力暴露为 MCP 工具"""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

SERVER_DIR = Path(__file__).parent.absolute()
FETCH_SH = SERVER_DIR / "scripts" / "fetch.sh"
SEARXNG_INSTANCE = os.environ.get("SEARXNG_INSTANCE", "https://searxng.hqgg.top:59826")
DEFAULT_TIMEOUT = int(os.environ.get("SMART3W_TIMEOUT", "30"))

# 与 scripts/fetch.sh 的默认重试次数保持一致（下方会显式传给 fetch.sh）
FETCH_RETRY = 2
_STRATEGY_COUNT = {"get": 1, "fetch": 1, "stealthy": 1, "smart": 3}

mcp = FastMCP(
    "smart3w",
    host="0.0.0.0",
    port=int(os.environ.get("SMART3W_PORT", "50826")),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
)


def _fetch_subprocess_timeout(timeout: int, mode: str) -> int:
    """计算 fetch.sh 子进程超时，覆盖每个策略的重试次数与 smart 降级链路总时长。"""
    strategies = _STRATEGY_COUNT.get(mode, 1)
    # 每次尝试按 timeout 计（curl --max-time / scrapling timeout），另加建连与进程开销；
    # 乘以重试次数与策略数后，再留固定余量。
    return (timeout + 15) * FETCH_RETRY * strategies + 15


def _validate_http_url(url: str) -> str | None:
    """校验 URL：仅允许 http/https 且带主机名；非法时返回错误信息，否则返回 None。"""
    if not url or len(url) > 8192:
        return "URL 不能为空且长度不能超过 8192"
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return f"URL 格式无效: {exc}"
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "仅支持 http/https URL"
    return None


def _run_fetch(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run fetch.sh and return stdout.

    fetch.sh 约定：数据走 stdout，日志走 stderr，因此成功时只返回 stdout，
    失败时把 stderr（或 stdout）作为异常信息抛出，避免日志污染数据通道。
    """
    env = {**os.environ, "SEARXNG_INSTANCE": SEARXNG_INSTANCE}
    result = subprocess.run(
        ["bash", str(FETCH_SH)] + args,
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"Exit code {result.returncode}"
        raise RuntimeError(detail)
    return result.stdout


def _run_fetch_file(
    url: str,
    mode: str,
    compress: bool,
    timeout: int,
    max_length: int,
    start_index: int,
) -> dict:
    """Run a fetch variant that writes to a file, return structured result."""
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as tmp:
        out_path = tmp.name

    try:
        args = [mode, url, out_path, "--timeout", str(timeout), "--retry", str(FETCH_RETRY)]
        if not compress:
            args.append("--no-compress")

        log = _run_fetch(args, _fetch_subprocess_timeout(timeout, mode))
        content = Path(out_path).read_text(encoding="utf-8", errors="replace")
        total_length = len(content)

        method = None
        m = re.search(r"方法:\s*([^\s|]+)", log)
        if m:
            method = m.group(1)

        content = content[start_index:] if start_index else content
        truncated = bool(max_length and len(content) > max_length)
        if truncated:
            content = content[:max_length]

        return {
            "success": True,
            "url": url,
            "mode": mode,
            "method": method,
            "content": content,
            "total_length": total_length,
            "chars_returned": len(content),
            "truncated": truncated,
            "log": log.strip(),
        }
    finally:
        Path(out_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def smart3w_search(query: str, count: int = 10) -> str:
    """Search the web via SearXNG. Returns JSON with title/url/snippet per result."""
    if not query or count < 1 or count > 100:
        return "❌ query 不能为空，count 必须是 1-100 的整数"
    return _run_fetch(["search", query, str(count)])


@mcp.tool()
def smart3w_fetch(
    url: str,
    mode: str = "smart",
    compress: bool = True,
    timeout: int = 30,
    max_length: int = 0,
    start_index: int = 0,
) -> str:
    """Fetch and extract content from a webpage.

    Args:
        url: The webpage URL to fetch
        mode: Fetch strategy — 'smart' (auto-degrade curl→scrapling→stealthy),
              'get' (curl only, lightweight), 'fetch' (scrapling + Chrome),
              'stealthy' (scrapling + Chrome + Cloudflare bypass)
        compress: Whether to extract readable content (True) or return raw HTML (False)
        timeout: Per-request timeout in seconds
        max_length: Maximum characters of content to return (0 = unlimited)
        start_index: 0-based offset into the extracted content (for pagination)

    Returns:
        JSON string: {success, url, mode, method, content, total_length,
                      chars_returned, truncated, log}
    """
    error = None
    if mode not in ("smart", "get", "fetch", "stealthy"):
        error = f"无效抓取模式: {mode}。可选: smart, get, fetch, stealthy"
    elif timeout < 1 or timeout > 120:
        error = "timeout 必须是 1-120 的整数（秒）"
    elif max_length < 0 or max_length > 200000:
        error = "max_length 必须是 0-200000 的整数（0 表示不截断）"
    elif start_index < 0:
        error = "start_index 必须是非负整数"
    else:
        error = _validate_http_url(url)
    if error:
        return json.dumps({"success": False, "url": url, "error": error}, ensure_ascii=False)

    try:
        result = _run_fetch_file(url, mode, compress, timeout, max_length, start_index)
    except RuntimeError as exc:
        result = {"success": False, "url": url, "error": str(exc)}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def smart3w_sitemap(url: str, max_urls: int = 50) -> str:
    """Parse a sitemap (supports index and URL-set formats). Returns discovered URLs."""
    if max_urls < 1 or max_urls > 10000:
        return "❌ max_urls 必须是 1-10000 的整数"
    url_error = _validate_http_url(url)
    if url_error:
        return f"❌ {url_error}"
    return _run_fetch(["sitemap", url, str(max_urls)])


@mcp.tool()
def smart3w_doctor(check_search: bool = False) -> str:
    """Check if smart3w dependencies are installed and functional."""
    args = ["doctor"]
    if check_search:
        args.append("--check-search")
    return _run_fetch(args)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.getenv("MCP_TRANSPORT", "streamable-http"),
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)
