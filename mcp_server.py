#!/usr/bin/env python3
"""Smart3W MCP Server - 将 smart3w 搜索与网页抓取能力暴露为 MCP 工具。"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from smart3w_core import (
    DEFAULT_INSTANCE,
    FETCH_MODES,
    SEARCH_TIME_RANGES,
    SitemapError,
    parse_sitemap,
    search_web,
)
from smart3w_fetch_utils import (
    DEFAULT_RESPECT_ROBOTS,
    DomainRateLimiter,
    RobotsChecker,
    TTLCache,
    apply_window,
    is_rate_limited,
)
from smart3w_url_utils import normalize_url, ssrf_blocked

__version__ = "2.3.0"

SERVER_DIR = Path(__file__).resolve().parent
FETCH_SH = SERVER_DIR / "scripts" / "fetch.sh"
SEARXNG_INSTANCE = os.environ.get("SEARXNG_INSTANCE", DEFAULT_INSTANCE)

# 与 scripts/fetch.sh 的默认尝试次数保持一致（下方会显式传给 fetch.sh）
FETCH_RETRY = 2
_STRATEGY_COUNT = {"get": 1, "fetch": 1, "stealthy": 1, "smart": 4}


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量；非法值回退默认值，避免 import 阶段崩溃。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


DEFAULT_TIMEOUT = max(1, _env_int("SMART3W_TIMEOUT", 30))
DEFAULT_PORT = min(65535, max(1, _env_int("SMART3W_PORT", 50826)))
DEFAULT_HOST = (os.environ.get("SMART3W_HOST") or "0.0.0.0").strip() or "0.0.0.0"
MAX_CACHE_CONTENT_BYTES = 2_000_000

# 抓取治理：URL 去重缓存 / robots / 按域名限速
FETCH_CACHE = TTLCache()
SEARCH_CACHE = TTLCache()
ROBOTS_CHECKER = RobotsChecker()
RATE_LIMITER = DomainRateLimiter()

mcp = FastMCP(
    "smart3w",
    host=DEFAULT_HOST,
    port=DEFAULT_PORT,
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
)


class FetchError(RuntimeError):
    """fetch.sh 执行失败；子类通过 error_code 区分具体原因。"""

    error_code = "FETCH_FAILED"


class FetchTimeoutError(FetchError):
    error_code = "FETCH_TIMEOUT"


class FetchUnavailableError(FetchError):
    error_code = "FETCH_UNAVAILABLE"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _fetch_subprocess_timeout(timeout: int, mode: str) -> int:
    """计算 fetch.sh 子进程超时，覆盖每个策略的尝试次数与 smart 降级链路总时长。"""
    strategies = _STRATEGY_COUNT.get(mode, 1)
    # 每次尝试按 timeout 计（curl --max-time / scrapling timeout），另加建连与进程开销；
    # 乘以尝试次数与策略数后，再留固定余量。
    return (timeout + 15) * FETCH_RETRY * strategies + 15


def _validate_http_url(url: str) -> str | None:
    """校验 URL：仅允许 http/https 且带主机名；非法时返回错误信息，否则返回 None。"""
    if not isinstance(url, str):
        return "URL 必须是字符串"
    url = url.strip()
    if not url or len(url) > 8192:
        return "URL 不能为空且长度不能超过 8192"
    if any(ord(ch) < 0x20 for ch in url):
        return "URL 包含非法控制字符"
    try:
        parsed = urlparse(url)
        # 访问 port 会触发校验，提前捕获非法端口
        parsed.port
    except ValueError as exc:
        return f"URL 格式无效: {exc}"
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "仅支持 http/https URL"
    if parsed.username or parsed.password:
        return "URL 不允许包含用户名/密码"
    return ssrf_blocked(url)


def _run_fetch(
    args: list[str],
    timeout: int = DEFAULT_TIMEOUT,
    respect_robots: bool | None = None,
) -> str:
    """Run fetch.sh and return stdout.

    fetch.sh 约定：数据走 stdout，日志走 stderr，因此成功时只返回 stdout，
    失败时把 stderr（或 stdout）作为异常信息抛出，避免日志污染数据通道。
    """
    if not FETCH_SH.is_file():
        raise FetchUnavailableError(f"找不到抓取脚本: {FETCH_SH}")
    env = {
        **os.environ,
        "SEARXNG_INSTANCE": SEARXNG_INSTANCE,
        # 让 fetch.sh 使用当前 Python 解释器，避免 Windows/venv 下找不到 python3
        "SMART3W_PYTHON": sys.executable,
        "PYTHONIOENCODING": "utf-8",
    }
    # 让 Python HTTP 抓取器在每个 redirect hop 上检查 robots.txt
    if respect_robots is not None:
        env["SMART3W_RESPECT_ROBOTS"] = "1" if respect_robots else "0"
    try:
        result = subprocess.run(
            ["bash", str(FETCH_SH), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise FetchTimeoutError(f"fetch.sh 执行超时（>{timeout}s）") from exc
    except OSError as exc:
        raise FetchUnavailableError(f"无法执行 fetch.sh（需要 bash）: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"Exit code {result.returncode}"
        raise FetchError(detail)
    return result.stdout


def _read_effective_url(path: str) -> str | None:
    """读取 curl 最终 URL 侧车文件（由 fetch.sh --effective-url 写入）；缺失或为空返回 None。"""
    try:
        url = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return url or None


def _do_fetch_file(
    url: str,
    mode: str,
    compress: bool,
    timeout: int,
    respect_robots: bool = False,
) -> dict:
    """真正调用 fetch.sh 抓取一个 URL，返回包含完整正文的结构化结果。"""
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as tmp:
        out_path = tmp.name
    effective_path = f"{out_path}.effective"

    try:
        args = [
            mode, url, out_path,
            "--timeout", str(timeout), "--retry", str(FETCH_RETRY),
            "--effective-url", effective_path,
        ]
        if not compress:
            args.append("--no-compress")

        log = _run_fetch(
            args,
            _fetch_subprocess_timeout(timeout, mode),
            respect_robots=respect_robots,
        )
        try:
            content = Path(out_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise FetchError(f"fetch.sh 未生成输出文件: {exc}") from exc

        method = None
        match = re.search(r"方法:\s*([^\s|]+)", log)
        if match:
            method = match.group(1)

        effective_url = _read_effective_url(effective_path)
        if effective_url:
            blocked = ssrf_blocked(effective_url)
            if blocked:
                return {
                    "success": False,
                    "url": url,
                    "mode": mode,
                    "method": method,
                    "effective_url": effective_url,
                    "error": f"重定向目标被拦截: {blocked}",
                    "error_code": "SSRF_BLOCKED",
                }

        result = {
            "success": True,
            "url": url,
            "mode": mode,
            "method": method,
            "content": content,
            "log": log.strip(),
        }
        if effective_url:
            result["effective_url"] = effective_url
        return result
    finally:
        Path(out_path).unlink(missing_ok=True)
        Path(effective_path).unlink(missing_ok=True)


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
    """抓取一个 URL：检查 robots.txt、按域名限速、查缓存，最后返回窗口切片结果。"""
    if respect_robots is None:
        respect_robots = DEFAULT_RESPECT_ROBOTS

    # robots 检查必须先于缓存：否则 robots 关闭时写入的缓存会绕过后续的 robots 开启请求
    if respect_robots and not ROBOTS_CHECKER.allows(url):
        return {
            "success": False,
            "url": url,
            "mode": mode,
            "error": "robots.txt 禁止抓取该 URL",
            "error_code": "ROBOTS_BLOCKED",
        }

    cache_key = (normalize_url(url), mode, compress)
    if use_cache:
        cached = FETCH_CACHE.get(cache_key)
        if cached is not None:
            return apply_window({**cached, "url": url, "cached": True}, start_index, max_length)

    RATE_LIMITER.wait(url)
    try:
        result = _do_fetch_file(url, mode, compress, timeout, respect_robots=respect_robots)
    except FetchError as exc:
        return {
            "success": False,
            "url": url,
            "mode": mode,
            "error": str(exc),
            "error_code": exc.error_code,
        }

    # 单条目过大时不进缓存，避免 64 条大页面把内存撑爆
    if result.get("success") and len(result.get("content", "")) <= MAX_CACHE_CONTENT_BYTES:
        FETCH_CACHE.put(cache_key, result)
    return apply_window(result, start_index, max_length)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def smart3w_search(
    query: str,
    count: int = 10,
    language: str = "zh-CN",
    time_range: str = "",
    categories: str = "",
    safesearch: int = 0,
    engines: str = "",
) -> dict[str, object]:
    """Search the web via SearXNG (多实例 fallback + 结果去重 + TTL 缓存).

    Args:
        query: 搜索关键词
        count: 返回结果数量 (1-100)
        language: 语言，如 zh-CN / en-US（留空表示不指定）
        time_range: 时间范围，可选 day/week/month/year（空表示不限）
        categories: 搜索分类，逗号分隔，如 general,news,images（空表示默认）
        safesearch: 安全搜索 0=关闭 1=中等 2=严格
        engines: 指定引擎，逗号分隔，如 google,bing（空表示 SearXNG 默认）

    Returns:
        dict: {success, query, engine, instance, params, results,
               result_count, answers?, corrections?, suggestions?,
               infoboxes?, cached?, log}
    """
    query = (query or "").strip() if isinstance(query, str) else ""
    language = language.strip() if isinstance(language, str) else ""
    categories = categories.strip() if isinstance(categories, str) else ""
    engines = engines.strip() if isinstance(engines, str) else ""
    if not query:
        return {
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "query 不能为空", "error_code": "INVALID_ARGUMENT",
        }
    if not _is_int(count) or count < 1 or count > 100:
        return {
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "count 必须是 1-100 的整数", "error_code": "INVALID_ARGUMENT",
        }
    if not _is_int(safesearch) or safesearch not in (0, 1, 2):
        return {
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "safesearch 必须是 0/1/2", "error_code": "INVALID_ARGUMENT",
        }
    if time_range not in SEARCH_TIME_RANGES:
        return {
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "time_range 必须是 day/week/month/year 之一或留空",
            "error_code": "INVALID_ARGUMENT",
        }

    cache_key = (query, count, language, time_range, categories, safesearch, engines)
    cached = SEARCH_CACHE.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    # 直接调用核心模块：不再经过 fetch.sh / 子进程，减少一次进程与 JSON 往返
    try:
        data = search_web(
            query, count, SEARXNG_INSTANCE,
            language=language, time_range=time_range,
            categories=categories, safesearch=safesearch, engines=engines,
        )
    except Exception as exc:
        return {
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": f"搜索失败: {exc}", "error_code": "SEARCH_FAILED",
        }
    if not isinstance(data, dict):
        return {
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "搜索服务返回了非对象 JSON",
        }
    if data.get("success"):
        SEARCH_CACHE.put(cache_key, data)
    return data


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
) -> dict[str, object]:
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
        dict: {success, url, mode, method, content, total_length,
               chars_returned, truncated, cached, log}
    """
    if not isinstance(url, str):
        url = ""
    url = url.strip()
    error = None
    if mode not in FETCH_MODES:
        error = f"无效抓取模式: {mode}。可选: {', '.join(FETCH_MODES)}"
    elif not _is_int(timeout) or timeout < 1 or timeout > 120:
        error = "timeout 必须是 1-120 的整数（秒）"
    elif not _is_int(max_length) or max_length < 0 or max_length > 200000:
        error = "max_length 必须是 0-200000 的整数（0 表示不截断）"
    elif not _is_int(start_index) or start_index < 0:
        error = "start_index 必须是非负整数"
    else:
        error = _validate_http_url(url)
    if error:
        return {
            "success": False, "url": url, "mode": mode,
            "error": error, "error_code": "INVALID_ARGUMENT",
        }

    return _run_fetch_file(
        url, mode, compress, timeout, max_length, start_index,
        use_cache=use_cache, respect_robots=respect_robots,
    )


@mcp.tool()
def smart3w_crawl(
    urls: list[str],
    mode: str = "get",
    compress: bool = True,
    timeout: int = 20,
    max_length: int = 5000,
    concurrency: int = 3,
    respect_robots: bool = True,
) -> dict[str, object]:
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
        dict: {success, requested, unique, duplicates_skipped, invalid?,
               invalid_count, succeeded, failed, mode, results: [...]}
        success 为 True 表示至少一个 URL 抓取成功；全部失败时为 False 并附带 error。
    """
    if not isinstance(urls, (list, tuple)) or not urls or len(urls) > 100:
        return {"success": False, "error": "urls 必须是 1-100 个 URL"}
    if mode not in FETCH_MODES:
        return {"success": False, "error": f"无效抓取模式: {mode}"}
    if not _is_int(timeout) or timeout < 1 or timeout > 120:
        return {"success": False, "error": "timeout 必须是 1-120 的整数（秒）"}
    if not _is_int(max_length) or max_length < 0 or max_length > 200000:
        return {"success": False, "error": "max_length 必须是 0-200000 的整数"}
    if not _is_int(concurrency) or concurrency < 1 or concurrency > 8:
        return {"success": False, "error": "concurrency 必须是 1-8 的整数"}

    invalid: list[str] = []
    unique: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        if not isinstance(raw_url, str) or _validate_http_url(raw_url):
            invalid.append(raw_url if isinstance(raw_url, str) else repr(raw_url))
            continue
        url = raw_url.strip()
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)

    valid_count = len(urls) - len(invalid)
    base = {
        "requested": len(urls),
        "unique": len(unique),
        "duplicates_skipped": max(0, valid_count - len(unique)),
        "invalid_count": len(invalid),
        "mode": mode,
    }
    if invalid:
        base["invalid"] = invalid
    if not unique:
        return {
            **base, "success": False, "succeeded": 0, "failed": 0, "results": [],
            "error": "没有可抓取的合法 URL",
        }

    def _crawl_one(url: str) -> dict:
        result: dict = {}
        for attempt in range(3):
            try:
                result = _run_fetch_file(
                    url, mode, compress, timeout, max_length=0, start_index=0,
                    use_cache=True, respect_robots=respect_robots,
                )
            except Exception as exc:  # 单个 URL 的意外错误不应中断整批
                result = {
                    "success": False, "url": url, "mode": mode,
                    "error": str(exc), "error_code": "INTERNAL_ERROR",
                }
            if result.get("success") or not is_rate_limited(result.get("error", "")):
                break
            time.sleep(1 + attempt)
        return result

    results_by_index: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_map = {pool.submit(_crawl_one, url): idx for idx, url in enumerate(unique)}
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                results_by_index[idx] = future.result()
            except Exception as exc:
                results_by_index[idx] = {
                    "success": False, "url": unique[idx], "mode": mode,
                    "error": str(exc), "error_code": "INTERNAL_ERROR",
                }

    results = [
        apply_window(results_by_index[idx], 0, max_length)
        for idx in range(len(unique))
    ]
    succeeded = sum(1 for result in results if result.get("success"))
    out = {
        **base,
        "success": succeeded > 0,
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
    if succeeded == 0:
        out["error"] = "所有 URL 抓取失败"
    return out


@mcp.tool()
def smart3w_sitemap(url: str, max_urls: int = 50, recursive: bool = False) -> dict[str, object]:
    """Parse a sitemap (supports index and URL-set formats). Returns discovered URLs.

    Args:
        url: Sitemap URL
        max_urls: 最多返回/展开的 URL 数（1-10000）
        recursive: 是否递归展开 Sitemap Index（最多 2 层）

    Returns:
        dict: {success, output} 或 {success: False, error, error_code}
    """
    if not _is_int(max_urls) or max_urls < 1 or max_urls > 10000:
        return {"success": False, "error": "max_urls 必须是 1-10000 的整数"}
    url = url.strip() if isinstance(url, str) else ""
    url_error = _validate_http_url(url)
    if url_error:
        return {"success": False, "error": url_error, "error_code": "INVALID_ARGUMENT"}
    try:
        output = parse_sitemap(url, max_urls, recursive=recursive)
    except SitemapError as exc:
        return {"success": False, "error": str(exc), "error_code": "SITEMAP_FAILED"}
    except Exception as exc:
        return {"success": False, "error": f"解析失败: {exc}", "error_code": "SITEMAP_FAILED"}
    return {"success": True, "output": output.strip()}


@mcp.tool()
def smart3w_doctor(check_search: bool = False) -> dict[str, object]:
    """Check if smart3w dependencies are installed and functional.

    Returns:
        dict: {success, output} 或 {success: False, error}
    """
    args = ["doctor"]
    if check_search:
        args.append("--check-search")
    try:
        output = _run_fetch(args)
    except FetchError as exc:
        return {"success": False, "error": str(exc), "error_code": exc.error_code}
    return {"success": True, "output": output.strip()}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="smart3w-mcp", description="Smart3W MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.getenv("MCP_TRANSPORT", "streamable-http"),
    )
    parser.add_argument("--version", action="version", version=f"smart3w {__version__}")
    args = parser.parse_args(argv)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
