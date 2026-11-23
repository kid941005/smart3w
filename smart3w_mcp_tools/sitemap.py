# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
from smart3w_mcp_tools._server import srv


def smart3w_sitemap(url: str, max_urls: int = 50, recursive: bool = False) -> dict[str, object]:
    """Parse a sitemap (supports index and URL-set formats). Returns discovered URLs.

    Args:
        url: Sitemap URL
        max_urls: 最多返回/展开的 URL 数（1-10000）
        recursive: 是否递归展开 Sitemap Index（最多 2 层）

    Returns:
        dict: {success, output}（output 为格式化文本）或 {success: False, error, error_code}
    """
    if not srv._is_int(max_urls) or max_urls < 1 or max_urls > 10000:
        return {"success": False, "error": "max_urls 必须是 1-10000 的整数", "error_code": "INVALID_ARGUMENT"}
    if not isinstance(recursive, bool):
        return {"success": False, "error": "recursive 必须是布尔值", "error_code": "INVALID_ARGUMENT"}
    url = url.strip() if isinstance(url, str) else ""
    url_error = srv._validate_http_url(url)
    if url_error:
        return {"success": False, "error": url_error, "error_code": "INVALID_ARGUMENT"}
    try:
        output = srv.parse_sitemap(url, max_urls, recursive=recursive)
    except srv.SitemapError as exc:
        return {"success": False, "error": str(exc), "error_code": "SITEMAP_FAILED"}
    except Exception as exc:
        return {"success": False, "error": f"解析失败: {exc}", "error_code": "SITEMAP_FAILED"}
    return {"success": True, "output": output.strip()}
