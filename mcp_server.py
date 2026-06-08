#!/usr/bin/env python3
"""Smart3W MCP Server - 将 smart3w 搜索与网页抓取能力暴露为 MCP 工具"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

SERVER_DIR = Path(__file__).parent.absolute()
FETCH_SH = SERVER_DIR / "scripts" / "fetch.sh"
SEARXNG_INSTANCE = os.environ.get("SEARXNG_INSTANCE", "https://searxng.hqgg.top:59826")
DEFAULT_TIMEOUT = int(os.environ.get("SMART3W_TIMEOUT", "30"))

mcp = FastMCP(
    "smart3w",
    host="0.0.0.0",
    port=int(os.environ.get("SMART3W_PORT", "50826")),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
)


def _run_fetch(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run fetch.sh and return stdout."""
    env = {**os.environ, "SEARXNG_INSTANCE": SEARXNG_INSTANCE}
    result = subprocess.run(
        ["bash", str(FETCH_SH)] + args,
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Exit code {result.returncode}")
    return result.stdout + result.stderr


def _run_fetch_file(url: str, mode: str, compress: bool, timeout: int) -> str:
    """Run a fetch variant that writes to a file, return file content."""
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as tmp:
        out_path = tmp.name

    try:
        args = [mode, url, out_path, "--timeout", str(timeout)]
        if not compress:
            args.append("--no-compress")

        log = _run_fetch(args, timeout + 5)

        Path(out_path).is_file() or Path(out_path).exists()
        content = Path(out_path).read_text(encoding="utf-8", errors="replace")
        return f"{content}\n\n---\n{log.strip()}"
    finally:
        Path(out_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def smart3w_search(query: str, count: int = 10) -> str:
    """Search the web via SearXNG. Returns JSON with title/url/snippet per result."""
    return _run_fetch(["search", query, str(count)])


@mcp.tool()
def smart3w_fetch(url: str, mode: str = "smart", compress: bool = True, timeout: int = 30) -> str:
    """Fetch and extract content from a webpage.

    Args:
        url: The webpage URL to fetch
        mode: Fetch strategy — 'smart' (auto-degrade curl→scrapling→stealthy),
              'get' (curl only, lightweight), 'fetch' (scrapling + Chrome),
              'stealthy' (scrapling + Chrome + Cloudflare bypass)
        compress: Whether to extract readable content (True) or return raw HTML (False)
        timeout: Per-request timeout in seconds
    """
    if mode not in ("smart", "get", "fetch", "stealthy"):
        return f"❌ 无效抓取模式: {mode}。可选: smart, get, fetch, stealthy"
    return _run_fetch_file(url, mode, compress, timeout)


@mcp.tool()
def smart3w_sitemap(url: str, max_urls: int = 50) -> str:
    """Parse a sitemap (supports index and URL-set formats). Returns discovered URLs."""
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
