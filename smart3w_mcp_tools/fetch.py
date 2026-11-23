# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
from smart3w_mcp_tools._server import srv


def smart3w_fetch(
    url: str,
    mode: str = "smart",
    compress: bool = True,
    timeout: int = 30,
    max_length: int = 0,
    start_index: int = 0,
    use_cache: bool = True,
    respect_robots: bool | None = None,
    max_age_seconds: int = -1,
    query: str = "",
    max_tokens: int = 0,
) -> dict[str, object]:
    """Fetch and extract content from a webpage (支持 TTL 缓存、robots 与内容筛选).

    Args:
        url: The webpage URL to fetch
        mode: Fetch strategy — 'smart' (auto-degrade Python HTTP→curl→scrapling→stealthy),
              'get' (Python HTTP, fallback curl), 'fetch' (scrapling + Chrome),
              'stealthy' (scrapling + Chrome + Cloudflare bypass)
        compress: Whether to extract readable content (True) or return raw HTML (False)
        timeout: Per-request timeout in seconds
        max_length: Maximum characters of content to return (0 = unlimited)
        start_index: 0-based offset into the extracted content (for pagination)
        use_cache: Whether to use the in-memory TTL cache (True) or force re-fetch (False)。
                    False 只跳过缓存读取，新抓取结果仍会写入缓存。
        respect_robots: Whether to respect robots.txt; None 表示跟随环境变量
                        SMART3W_RESPECT_ROBOTS（默认关闭）
        max_age_seconds: 缓存最大可接受年龄（秒）；-1 使用全局 TTL，0 强制跳过缓存读取，
                         新抓取结果仍会写入缓存
        query: 可选查询词，只保留正文中最相关的段落（不改变缓存原文）
        max_tokens: 可选上下文预算，按 4 字符≈1 token 粗估；0 表示不限制

    Returns:
        dict: {success, url, mode, method, content, total_length,
               chars_returned, truncated, cached, cache_age?, log,
               original_length?, filter?, format?, pages?/sheets?/slides?, document?}
    """
    if not isinstance(url, str):
        url = ""
    url = url.strip()
    query = query.strip() if isinstance(query, str) else ""
    error = None
    if mode not in srv.FETCH_MODES:
        error = f"无效抓取模式: {mode}。可选: {', '.join(srv.FETCH_MODES)}"
    elif not srv._is_int(timeout) or timeout < 1 or timeout > 120:
        error = "timeout 必须是 1-120 的整数（秒）"
    elif not srv._is_int(max_length) or max_length < 0 or max_length > 200000:
        error = "max_length 必须是 0-200000 的整数（0 表示不截断）"
    elif not srv._is_int(start_index) or start_index < 0:
        error = "start_index 必须是非负整数"
    elif not srv._is_int(max_age_seconds) or max_age_seconds < -1 or max_age_seconds > 604800:
        error = "max_age_seconds 必须是 -1 或 0-604800 的整数"
    elif not srv._is_int(max_tokens) or max_tokens < 0 or max_tokens > 200000:
        error = "max_tokens 必须是 0-200000 的整数"
    elif bool_error := srv._bool_error(compress, "compress"):
        error = bool_error
    elif bool_error := srv._bool_error(use_cache, "use_cache"):
        error = bool_error
    elif bool_error := srv._optional_bool_error(respect_robots, "respect_robots"):
        error = bool_error
    elif len(query) > 500:
        error = "query 长度不能超过 500 字符"
    else:
        error = srv._validate_http_url(url)
    if error:
        return {
            "success": False,
            "url": url,
            "mode": mode,
            "error": error,
            "error_code": "INVALID_ARGUMENT",
        }

    return srv._run_fetch_file(
        url,
        mode,
        compress,
        timeout,
        max_length,
        start_index,
        use_cache=use_cache,
        respect_robots=respect_robots,
        max_age_seconds=max_age_seconds,
        query=query,
        max_tokens=max_tokens,
    )
