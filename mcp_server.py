#!/usr/bin/env python3
"""Smart3W MCP Server - 将 smart3w 搜索与网页抓取能力暴露为 MCP 工具"""

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from smart3w_fetch_utils import (
    DEFAULT_RESPECT_ROBOTS,
    DomainRateLimiter,
    RobotsChecker,
    TTLCache,
    apply_window,
    is_rate_limited,
    normalize_url,
)

SERVER_DIR = Path(__file__).parent.absolute()
FETCH_SH = SERVER_DIR / "scripts" / "fetch.sh"
SEARXNG_INSTANCE = os.environ.get("SEARXNG_INSTANCE", "https://searxng.hqgg.top:59826")
DEFAULT_TIMEOUT = int(os.environ.get("SMART3W_TIMEOUT", "30"))

# 与 scripts/fetch.sh 的默认重试次数保持一致（下方会显式传给 fetch.sh）
FETCH_RETRY = 2
_STRATEGY_COUNT = {"get": 1, "fetch": 1, "stealthy": 1, "smart": 3}

# 抓取治理：URL 去重缓存 / robots / 按域名限速
FETCH_CACHE = TTLCache()
ROBOTS_CHECKER = RobotsChecker()
RATE_LIMITER = DomainRateLimiter()

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


def _do_fetch_file(url: str, mode: str, compress: bool, timeout: int) -> dict:
    """真正调用 fetch.sh 抓取一个 URL，返回包含完整正文的结构化结果。"""
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as tmp:
        out_path = tmp.name

    try:
        args = [mode, url, out_path, "--timeout", str(timeout), "--retry", str(FETCH_RETRY)]
        if not compress:
            args.append("--no-compress")

        log = _run_fetch(args, _fetch_subprocess_timeout(timeout, mode))
        content = Path(out_path).read_text(encoding="utf-8", errors="replace")

        method = None
        m = re.search(r"方法:\s*([^\s|]+)", log)
        if m:
            method = m.group(1)

        return {
            "success": True,
            "url": url,
            "mode": mode,
            "method": method,
            "content": content,
            "log": log.strip(),
        }
    finally:
        Path(out_path).unlink(missing_ok=True)


def _run_fetch_file(
    url: str,
    mode: str,
    compress: bool,
    timeout: int,
    max_length: int,
    start_index: int,
    use_cache: bool = True,
    respect_robots: bool | None = None,
) -> dict:
    """抓取一个 URL：先查缓存、再检查 robots.txt、按域名限速，最后返回窗口切片结果。"""
    if respect_robots is None:
        respect_robots = DEFAULT_RESPECT_ROBOTS
    cache_key = f"{normalize_url(url)}|{mode}|{'c' if compress else 'r'}"

    if use_cache:
        cached = FETCH_CACHE.get(cache_key)
        if cached is not None:
            return apply_window({**cached, "url": url, "cached": True}, start_index, max_length)

    if respect_robots and not ROBOTS_CHECKER.allows(url):
        return {
            "success": False,
            "url": url,
            "mode": mode,
            "error": "robots.txt 禁止抓取该 URL",
            "error_code": "ROBOTS_BLOCKED",
        }

    RATE_LIMITER.wait(url)
    try:
        result = _do_fetch_file(url, mode, compress, timeout)
    except RuntimeError as exc:
        return {
            "success": False,
            "url": url,
            "mode": mode,
            "error": str(exc),
            "error_code": "FETCH_FAILED",
        }

    if result.get("success"):
        FETCH_CACHE.put(cache_key, result)
    return apply_window(result, start_index, max_length)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

_SEARCH_TIME_RANGES = ("", "day", "week", "month", "year")


@mcp.tool()
def smart3w_search(
    query: str,
    count: int = 10,
    language: str = "zh-CN",
    time_range: str = "",
    categories: str = "",
    safesearch: int = 0,
    engines: str = "",
) -> str:
    """Search the web via SearXNG (多实例 fallback + 结果去重). Returns JSON.

    Args:
        query: 搜索关键词
        count: 返回结果数量 (1-100)
        language: 语言，如 zh-CN / en-US（留空表示不指定）
        time_range: 时间范围，可选 day/week/month/year（空表示不限）
        categories: 搜索分类，逗号分隔，如 general,news,images（空表示默认）
        safesearch: 安全搜索 0=关闭 1=中等 2=严格
        engines: 指定引擎，逗号分隔，如 google,bing（空表示 SearXNG 默认）

    Returns:
        JSON string: {success, query, engine, instance, params, results,
                      result_count, log}
    """
    if not query or count < 1 or count > 100:
        return json.dumps({
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "query 不能为空，count 必须是 1-100 的整数",
        }, ensure_ascii=False)
    if safesearch not in (0, 1, 2):
        return json.dumps({
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "safesearch 必须是 0/1/2",
        }, ensure_ascii=False)
    if time_range not in _SEARCH_TIME_RANGES:
        return json.dumps({
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "time_range 必须是 day/week/month/year 之一或留空",
        }, ensure_ascii=False)

    args = [
        "search", query, str(count),
        "--language", language,
        "--safesearch", str(safesearch),
    ]
    if time_range:
        args += ["--time-range", time_range]
    if categories:
        args += ["--categories", categories]
    if engines:
        args += ["--engines", engines]
    return _run_fetch(args)


@mcp.tool()
def smart3w_fetch(
    url: str,
    mode: str = "smart",
    compress: bool = True,
    timeout: int = 30,
    max_length: int = 0,
    start_index: int = 0,
    use_cache: bool = True,
    respect_robots: bool | None = None,
) -> str:
    """Fetch and extract content from a webpage (支持 TTL 缓存与 robots 检查).

    Args:
        url: The webpage URL to fetch
        mode: Fetch strategy — 'smart' (auto-degrade curl→scrapling→stealthy),
              'get' (curl only, lightweight), 'fetch' (scrapling + Chrome),
              'stealthy' (scrapling + Chrome + Cloudflare bypass)
        compress: Whether to extract readable content (True) or return raw HTML (False)
        timeout: Per-request timeout in seconds
        max_length: Maximum characters of content to return (0 = unlimited)
        start_index: 0-based offset into the extracted content (for pagination)
        use_cache: Whether to use the in-memory TTL cache (True) or force re-fetch (False)
        respect_robots: Whether to respect robots.txt; None 表示跟随环境变量
                        SMART3W_RESPECT_ROBOTS（默认关闭）

    Returns:
        JSON string: {success, url, mode, method, content, total_length,
                      chars_returned, truncated, cached, log}
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

    result = _run_fetch_file(
        url, mode, compress, timeout, max_length, start_index,
        use_cache=use_cache, respect_robots=respect_robots,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def smart3w_crawl(
    urls: list[str],
    mode: str = "get",
    compress: bool = True,
    timeout: int = 20,
    max_length: int = 5000,
    concurrency: int = 3,
    respect_robots: bool = True,
) -> str:
    """Batch-fetch multiple URLs (URL 去重 + robots + 限速 + 缓存 + 并发).

    Args:
        urls: URL 列表（1-100 个）
        mode: Fetch strategy — smart/get/fetch/stealthy
        compress: Whether to extract readable content (True) or raw HTML (False)
        timeout: Per-request timeout in seconds
        max_length: Maximum characters per result (0 = unlimited)
        concurrency: Parallel workers (1-8)
        respect_robots: Whether to respect robots.txt (默认开启)

    Returns:
        JSON string: {success, requested, unique, duplicates_skipped,
                      succeeded, failed, mode, results: [...]}
    """
    if not urls or len(urls) > 100:
        return json.dumps({"success": False, "error": "urls 必须是 1-100 个 URL"}, ensure_ascii=False)
    if mode not in ("smart", "get", "fetch", "stealthy"):
        return json.dumps({"success": False, "error": f"无效抓取模式: {mode}"}, ensure_ascii=False)
    if timeout < 1 or timeout > 120:
        return json.dumps({"success": False, "error": "timeout 必须是 1-120 的整数（秒）"}, ensure_ascii=False)
    if max_length < 0 or max_length > 200000:
        return json.dumps({"success": False, "error": "max_length 必须是 0-200000 的整数"}, ensure_ascii=False)
    if concurrency < 1 or concurrency > 8:
        return json.dumps({"success": False, "error": "concurrency 必须是 1-8 的整数"}, ensure_ascii=False)

    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if _validate_http_url(url):
            continue
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)

    def _crawl_one(url: str) -> dict:
        result = {}
        for attempt in range(3):
            result = _run_fetch_file(
                url, mode, compress, timeout, max_length=0, start_index=0,
                use_cache=True, respect_robots=respect_robots,
            )
            if result.get("success") or not is_rate_limited(result.get("error", "")):
                break
            time.sleep(1 + attempt)
        return result

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_crawl_one, u) for u in unique]
        for fut in as_completed(futures):
            results.append(fut.result())

    results = [apply_window(r, 0, max_length) for r in results]
    succeeded = sum(1 for r in results if r.get("success"))
    out = {
        "success": True,
        "requested": len(urls),
        "unique": len(unique),
        "duplicates_skipped": len(urls) - len(unique),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "mode": mode,
        "results": results,
    }
    return json.dumps(out, ensure_ascii=False)


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
