# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from smart3w_mcp_tools._server import srv


def smart3w_extract(
    urls: list[str],
    schema: dict[str, object],
    mode: str = "css",
    timeout: int | None = None,
    concurrency: int = 3,
    respect_robots: bool = True,
) -> dict[str, object]:
    """Fetch URLs and extract structured fields with CSS or XPath selectors.

    Args:
        urls: URL 列表（1-20 个）
        schema: 字段定义对象，如 {"title": "h1", "links": {"selector": "a", "attr": "href", "all": true}}
        mode: 选择器类型 css 或 xpath（不执行 LLM 抽取）
        timeout: 每个 URL 的抓取超时（秒）
        concurrency: 并发抓取数（1-8）
        respect_robots: 是否遵守 robots.txt（默认开启）

    Returns:
        dict: {success, requested, unique, succeeded, failed,
               results: [{url, success, data?, missing?, error?}]}
    """
    if timeout is None:
        timeout = srv.DEFAULT_TIMEOUT
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except json.JSONDecodeError:
            return {"success": False, "error": "schema 不是合法 JSON", "error_code": "INVALID_ARGUMENT"}
    if not isinstance(schema, dict) or not schema:
        return {"success": False, "error": "schema 必须是非空对象", "error_code": "INVALID_ARGUMENT"}
    if mode not in ("css", "xpath"):
        return {"success": False, "error": "mode 必须是 css 或 xpath", "error_code": "INVALID_ARGUMENT"}
    if not isinstance(urls, (list, tuple)) or not urls or len(urls) > 20:
        return {"success": False, "error": "urls 必须是 1-20 个 URL", "error_code": "INVALID_ARGUMENT"}
    if not srv._is_int(timeout) or timeout < 1 or timeout > 120:
        return {"success": False, "error": "timeout 必须是 1-120 的整数（秒）", "error_code": "INVALID_ARGUMENT"}
    if not srv._is_int(concurrency) or concurrency < 1 or concurrency > 8:
        return {"success": False, "error": "concurrency 必须是 1-8 的整数", "error_code": "INVALID_ARGUMENT"}
    if not isinstance(respect_robots, bool):
        return {"success": False, "error": "respect_robots 必须是布尔值", "error_code": "INVALID_ARGUMENT"}

    invalid: list[str] = []
    valid: list[str] = []
    unique: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        if not isinstance(raw_url, str):
            invalid.append(repr(raw_url))
            continue
        url = raw_url.strip()
        if srv._validate_http_url(url):
            invalid.append(raw_url)
            continue
        valid.append(url)
        key = srv.normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)
    if not unique:
        return {
            "success": False,
            "requested": len(urls),
            "unique": 0,
            "invalid_count": len(invalid),
            "invalid": invalid,
            "succeeded": 0,
            "failed": 0,
            "results": [],
            "error": "没有可抽取的合法 URL",
            "error_code": "INVALID_ARGUMENT",
        }

    def _extract_one(url: str) -> dict:
        try:
            raw = srv._run_fetch_file(
                url,
                "get",
                False,
                timeout,
                max_length=0,
                start_index=0,
                use_cache=True,
                respect_robots=respect_robots,
            )
        except srv.FetchError as exc:
            return {"url": url, "success": False, "error": str(exc), "error_code": exc.error_code}
        except Exception as exc:
            return {"url": url, "success": False, "error": str(exc), "error_code": "INTERNAL_ERROR"}
        if not raw.get("success"):
            return {
                "url": url,
                "success": False,
                "error": raw.get("error") or "抓取失败",
                "error_code": raw.get("error_code", "FETCH_FAILED"),
            }
        if raw.get("format"):
            return {
                "url": url,
                "success": False,
                "error": f"文档格式 {raw.get('format')} 不支持 CSS/XPath 抽取",
                "error_code": "UNSUPPORTED_FORMAT",
            }
        try:
            payload = srv.extract_fields(raw.get("content", ""), schema, mode)
        except Exception as exc:
            return {"url": url, "success": False, "error": str(exc), "error_code": "EXTRACT_FAILED"}
        row = {"url": url, "success": True, **payload}
        if raw.get("effective_url"):
            row["effective_url"] = raw["effective_url"]
        return row

    results_by_index: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_map = {pool.submit(_extract_one, url): idx for idx, url in enumerate(unique)}
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                results_by_index[idx] = future.result()
            except Exception as exc:
                results_by_index[idx] = {
                    "url": unique[idx],
                    "success": False,
                    "error": str(exc),
                    "error_code": "INTERNAL_ERROR",
                }
    results = [results_by_index[idx] for idx in range(len(unique))]
    succeeded = sum(1 for row in results if row.get("success"))
    out: dict[str, object] = {
        "success": succeeded > 0,
        "requested": len(urls),
        "unique": len(unique),
        "duplicates_skipped": max(0, len(valid) - len(unique)),
        "invalid_count": len(invalid),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
    if invalid:
        out["invalid"] = invalid
    if succeeded == 0:
        out["error"] = "所有 URL 抽取失败"
        out["error_code"] = "EXTRACT_FAILED"
    return out
