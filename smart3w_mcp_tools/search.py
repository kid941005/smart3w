# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
from smart3w_mcp_tools._server import srv


def _attach_search_content(
    data: dict,
    read_top: int,
    content_max_chars: int,
) -> dict:
    """给搜索结果前 read_top 条补充正文；单条抓取失败不影响整体搜索。"""
    results = data.get("results") or []
    enriched: list[dict] = []
    succeeded = 0
    failed = 0
    for index, item in enumerate(results):
        row = dict(item) if isinstance(item, dict) else {"title": "", "url": "", "snippet": ""}
        if index >= read_top:
            enriched.append(row)
            continue
        url = row.get("url")
        if not isinstance(url, str) or srv._validate_http_url(url):
            row.update(
                {
                    "content": "",
                    "content_truncated": False,
                    "read_error": "搜索结果 URL 非法或不在 allowlist",
                    "read_error_code": "INVALID_ARGUMENT",
                }
            )
            failed += 1
            enriched.append(row)
            continue
        fetched = srv._run_fetch_file(
            url,
            "get",
            True,
            20,
            content_max_chars,
            0,
            use_cache=True,
            respect_robots=None,
        )
        if fetched.get("success"):
            row.update(
                {
                    "content": fetched.get("content", ""),
                    "content_truncated": bool(fetched.get("truncated")),
                    "content_total_length": fetched.get("total_length", 0),
                }
            )
            if fetched.get("effective_url"):
                row["effective_url"] = fetched["effective_url"]
            succeeded += 1
        else:
            row.update(
                {
                    "content": "",
                    "content_truncated": False,
                    "read_error": fetched.get("error") or "正文抓取失败",
                    "read_error_code": fetched.get("error_code", "FETCH_FAILED"),
                }
            )
            failed += 1
        enriched.append(row)
    out = dict(data)
    out["results"] = enriched
    out["read_top"] = read_top
    out["read_succeeded"] = succeeded
    out["read_failed"] = failed
    return out


def smart3w_search(
    query: str,
    count: int = 10,
    language: str = "zh-CN",
    time_range: str = "",
    categories: str = "",
    safesearch: int = 0,
    engines: str = "",
    read_top: int = 0,
    content_max_chars: int = 5000,
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
        read_top: 是否顺带读取前 N 条结果正文（0-5；0 表示不读，默认 0）
        content_max_chars: 每条正文最大字符数（0 表示不截断，默认 5000）

    Returns:
        dict: {success, query, engine, instance, params, results,
               result_count, read_top?, read_succeeded?, read_failed?,
               answers?, corrections?, suggestions?, infoboxes?, cached?, log}
    """
    query = (query or "").strip() if isinstance(query, str) else ""
    language = language.strip() if isinstance(language, str) else ""
    categories = categories.strip() if isinstance(categories, str) else ""
    engines = engines.strip() if isinstance(engines, str) else ""
    if not query:
        return {
            "success": False,
            "query": query,
            "results": [],
            "result_count": 0,
            "error": "query 不能为空",
            "error_code": "INVALID_ARGUMENT",
        }
    if not srv._is_int(count) or count < 1 or count > 100:
        return {
            "success": False,
            "query": query,
            "results": [],
            "result_count": 0,
            "error": "count 必须是 1-100 的整数",
            "error_code": "INVALID_ARGUMENT",
        }
    if not srv._is_int(safesearch) or safesearch not in (0, 1, 2):
        return {
            "success": False,
            "query": query,
            "results": [],
            "result_count": 0,
            "error": "safesearch 必须是 0/1/2",
            "error_code": "INVALID_ARGUMENT",
        }
    if time_range not in srv.SEARCH_TIME_RANGES:
        return {
            "success": False,
            "query": query,
            "results": [],
            "result_count": 0,
            "error": "time_range 必须是 day/week/month/year 之一或留空",
            "error_code": "INVALID_ARGUMENT",
        }
    if not srv._is_int(read_top) or read_top < 0 or read_top > 5:
        return {
            "success": False,
            "query": query,
            "results": [],
            "result_count": 0,
            "error": "read_top 必须是 0-5 的整数",
            "error_code": "INVALID_ARGUMENT",
        }
    if not srv._is_int(content_max_chars) or content_max_chars < 0 or content_max_chars > 200000:
        return {
            "success": False,
            "query": query,
            "results": [],
            "result_count": 0,
            "error": "content_max_chars 必须是 0-200000 的整数（0 表示不截断）",
            "error_code": "INVALID_ARGUMENT",
        }

    cache_key = (query, count, language, time_range, categories, safesearch, engines)
    cached = srv.SEARCH_CACHE.get(cache_key)
    if cached is not None:
        data = {**cached, "cached": True}
        return srv._attach_search_content(data, read_top, content_max_chars) if read_top else data

        # 直接调用核心模块：不再经过 fetch.sh / 子进程，减少一次进程与 JSON 往返
    try:
        data = srv.search_web(
            query,
            count,
            srv.SEARXNG_INSTANCE,
            language=language,
            time_range=time_range,
            categories=categories,
            safesearch=safesearch,
            engines=engines,
        )
    except Exception as exc:
        return {
            "success": False,
            "query": query,
            "results": [],
            "result_count": 0,
            "error": f"搜索失败: {exc}",
            "error_code": "SEARCH_FAILED",
        }
    if not isinstance(data, dict):
        return {
            "success": False,
            "query": query,
            "results": [],
            "result_count": 0,
            "error": "搜索服务返回了非对象 JSON",
            "error_code": "SEARCH_FAILED",
        }
    if data.get("success"):
        srv.SEARCH_CACHE.put(cache_key, data)
    return srv._attach_search_content(data, read_top, content_max_chars) if read_top else data
