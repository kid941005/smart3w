# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
from urllib.parse import urlsplit

from smart3w_mcp_tools._server import srv


def _origin_of(url: str) -> str:
    """返回 URL 的 scheme://netloc；解析失败返回空字符串。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def smart3w_map(
    url: str,
    limit: int = 200,
    search: str = "",
    include_sitemap: bool = True,
    include_homepage: bool = True,
    same_domain: bool = True,
    path_include: str = "",
    path_exclude: str = "",
    respect_robots: bool = True,
) -> dict[str, object]:
    """Discover a site's URLs from sitemap, homepage links and optional search results.

    Args:
        url: 站点入口 URL（仅用于确定域名与首页）
        limit: 最多返回 URL 数（1-5000）
        search: 可选搜索词，用搜索结果的 URL 补充发现（会按同站过滤）
        include_sitemap: 是否读取 robots.txt / sitemap.xml
        include_homepage: 是否抓取首页并提取链接
        same_domain: 是否只保留同站（含子域名）URL
        path_include: 逗号分隔的路径包含模式，支持 * 通配符
        path_exclude: 逗号分隔的路径排除模式，支持 * 通配符
        respect_robots: 抓取首页时是否遵守 robots.txt

    Returns:
        dict: {success, url, count, limit, urls: [{url, source}], sources,
               complete, partial, sitemap_errors?, homepage_error?,
               search_error?, truncated?, error?, error_code?}

        success 语义固定为「至少发现 1 个 URL」：count > 0 才是 True。
        只有 success=True 且所有来源无错误、未截断时 complete=True；
        有结果但来源报错或截断时 success=True、partial=True、complete=False；
        一个 URL 都没发现时 success=False、complete=False、error_code=MAP_FAILED。
    """
    seed = url.strip() if isinstance(url, str) else ""
    url_error = srv._validate_http_url(seed)
    if url_error:
        return {
            "success": False,
            "url": seed,
            "count": 0,
            "urls": [],
            "error": url_error,
            "error_code": "INVALID_ARGUMENT",
        }
    if not srv._is_int(limit) or limit < 1 or limit > 5000:
        return {
            "success": False,
            "url": seed,
            "count": 0,
            "urls": [],
            "error": "limit 必须是 1-5000 的整数",
            "error_code": "INVALID_ARGUMENT",
        }
    search = search.strip() if isinstance(search, str) else ""
    for name, value in (
        ("include_sitemap", include_sitemap),
        ("include_homepage", include_homepage),
        ("same_domain", same_domain),
        ("respect_robots", respect_robots),
    ):
        bool_error = srv._bool_error(value, name)
        if bool_error:
            return {
                "success": False,
                "url": seed,
                "count": 0,
                "urls": [],
                "error": bool_error,
                "error_code": "INVALID_ARGUMENT",
            }
    if not include_sitemap and not include_homepage and not search:
        return {
            "success": False,
            "url": seed,
            "count": 0,
            "urls": [],
            "error": "至少需要启用 include_sitemap、include_homepage 或提供 search",
            "error_code": "INVALID_ARGUMENT",
        }
    origin = srv._origin_of(seed)
    if not origin:
        return {
            "success": False,
            "url": seed,
            "count": 0,
            "urls": [],
            "error": "无法解析站点 origin",
            "error_code": "INVALID_ARGUMENT",
        }

    urls: list[dict[str, str]] = []
    seen: set[str] = set()
    source_counts: dict[str, int] = {}
    sitemap_errors: list[str] = []
    search_error = ""
    policy_cache: dict[tuple[str, str], str | None] = {}
    truncated = False

    def _policy_error(candidate: str) -> str | None:
        """按 (scheme, netloc) 缓存 DNS/SSRF 结果，但每个 URL 都先做形状校验。

        不能只按 hostname 缓存：同一域名下不同端口、userinfo 或 scheme
        的候选 URL 可能违反策略，复用一个 None 会绕过逐 URL 校验；
        长度和控制字符校验也必须逐 URL 执行，不能随 origin 缓存。
        """
        shape_error = srv.url_shape_error(candidate)
        if shape_error:
            return shape_error
        parsed = urlsplit(candidate)
        key = (parsed.scheme.lower(), parsed.netloc.lower())
        if key not in policy_cache:
            policy_cache[key] = srv.ssrf_blocked(candidate)
        return policy_cache[key]

    def _add(candidate: str, source: str) -> None:
        nonlocal truncated
        if not isinstance(candidate, str) or not candidate.strip():
            return
        candidate = candidate.strip()
        # 先做廉价的同站/路径过滤，避免为必然丢弃的链接做 DNS/SSRF 查询
        if same_domain and not srv.same_site(candidate, seed):
            return
        try:
            path = urlsplit(candidate).path or "/"
        except ValueError:
            return
        if not srv.path_allowed(path, path_include, path_exclude):
            return
        if _policy_error(candidate):
            return
        key = srv.normalize_url(candidate)
        if not key or key in seen:
            return
        if len(urls) >= limit:
            truncated = True
            return
        seen.add(key)
        urls.append({"url": candidate, "source": source})
        source_counts[source] = source_counts.get(source, 0) + 1

    sitemap_roots: list[str] = []
    if include_sitemap:
        robots_url = origin.rstrip("/") + "/robots.txt"
        robots = srv._run_fetch_file(
            robots_url,
            "get",
            False,
            10,
            0,
            0,
            use_cache=True,
            respect_robots=False,
        )
        if robots.get("success"):
            sitemap_roots.extend(srv.parse_robots_sitemaps(robots.get("content", ""), origin))
        default_sitemap = origin.rstrip("/") + "/sitemap.xml"
        if srv.normalize_url(default_sitemap) not in {srv.normalize_url(item) for item in sitemap_roots}:
            sitemap_roots.append(default_sitemap)
        for sitemap_url in sitemap_roots[:10]:
            if _policy_error(sitemap_url):
                continue
            try:
                entries, errors, _truncated, _fetched = srv.collect_sitemap_entries(
                    sitemap_url, max_urls=max(limit, 50), recursive=True
                )
            except srv.SitemapError as exc:
                sitemap_errors.append(f"{sitemap_url}: {exc}")
                continue
            except Exception as exc:
                sitemap_errors.append(f"{sitemap_url}: {exc}")
                continue
            sitemap_errors.extend(errors)
            for _, kind, loc in entries:
                if kind == "page":
                    _add(loc, "sitemap")

    homepage_error = ""
    if include_homepage:
        fetched = srv._run_fetch_file(
            seed,
            "get",
            False,
            20,
            0,
            0,
            use_cache=True,
            respect_robots=respect_robots,
        )
        if fetched.get("success"):
            # 首页抓取成功时站点根 URL 本身也计入，避免没有 sitemap/站内链接的站点返回空结果
            _add(seed, "homepage")
            base = fetched.get("effective_url") or seed
            for link in srv.extract_links(
                fetched.get("content", ""),
                base,
                same_domain=same_domain,
                path_include=path_include,
                path_exclude=path_exclude,
                max_links=max(limit * 3, 100),
            ):
                _add(link["url"], "homepage")
        else:
            homepage_error = fetched.get("error") or "首页抓取失败"

    if search:
        try:
            search_data = srv.search_web(search, min(20, max(1, limit)), srv.SEARXNG_INSTANCE)
        except Exception as exc:
            search_data = {"success": False, "error": str(exc)}
        if search_data.get("success"):
            for row in search_data.get("results") or []:
                if isinstance(row, dict) and isinstance(row.get("url"), str):
                    _add(row["url"], "search")
        else:
            search_error = str(search_data.get("error") or "搜索失败")

    source_errors = bool(sitemap_errors or homepage_error or search_error)
    out: dict[str, object] = {
        "success": bool(urls),
        "url": seed,
        "count": len(urls),
        "limit": limit,
        "urls": urls,
        "sources": source_counts,
        "complete": bool(urls) and not source_errors and not truncated,
        "partial": bool(urls) and (source_errors or truncated),
    }
    if sitemap_errors:
        out["sitemap_errors"] = sitemap_errors[:20]
    if homepage_error:
        out["homepage_error"] = homepage_error
    if search_error:
        out["search_error"] = search_error
    if truncated:
        out["truncated"] = True
    if not urls:
        out["error"] = "没有发现可用 URL"
        out["error_code"] = "MAP_FAILED"
    return out
