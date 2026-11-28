# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from smart3w_mcp_tools._server import srv


def _crawl_site(
    seed_url: str,
    mode: str,
    compress: bool,
    timeout: int,
    max_length: int,
    respect_robots: bool,
    max_depth: int,
    max_pages: int,
    same_domain: bool,
    path_include: str,
    path_exclude: str,
    job: dict | None = None,
) -> dict[str, object]:
    """从 seed_url 开始 BFS 递归抓取；每页先取原始 HTML 以发现链接，再压缩正文。"""
    cancel_event = job.get("cancel_event") if job is not None else None

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
    queued: set[str] = {srv.normalize_url(seed_url)}
    visited: set[str] = set()
    results: list[dict] = []
    invalid: list[str] = []
    # 队列上限按 max_pages 放大但仍封顶，防止宽站/站群把 URL 与原始 HTML 无界堆积
    queue_limit = max(100, min(5000, max_pages * 10))
    truncated = False
    deadline = time.monotonic() + srv.MAX_CRAWL_SECONDS
    deadline_exceeded = False
    while queue and len(results) < max_pages:
        if _cancelled():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            deadline_exceeded = True
            break
        url, depth = queue.popleft()
        key = srv.normalize_url(url)
        if key in visited:
            continue
        if srv._validate_http_url(url):
            # 非法 URL 不进入 visited 统计，避免 unique 与 succeeded+failed 不一致
            if url not in invalid:
                invalid.append(url)
            continue
        visited.add(key)
        per_fetch_timeout = max(1, min(timeout, int(deadline - time.monotonic())))
        try:
            raw = srv._run_fetch_file(
                url,
                mode,
                False,
                per_fetch_timeout,
                max_length=0,
                start_index=0,
                use_cache=True,
                respect_robots=respect_robots,
                cancel_event=cancel_event,
            )
        except srv.FetchCancelledError:
            break
        if _cancelled():
            break
        if not raw.get("success"):
            item = dict(raw)
            item["depth"] = depth
            results.append(srv.apply_window(item, 0, max_length))
            continue
        html = raw.get("content", "")
        content = html
        if compress and not raw.get("format"):
            if _cancelled():
                break
            content = srv._compress_html_text(html) or html
        item = dict(raw)
        item["content"] = content
        item["depth"] = depth
        item["crawled_url"] = url
        results.append(srv.apply_window(item, 0, max_length))

        if depth >= max_depth or raw.get("format"):
            continue
        base = raw.get("effective_url") or url
        for link in srv.extract_links(
            html,
            base,
            same_domain=same_domain,
            path_include=path_include,
            path_exclude=path_exclude,
            max_links=max(max_pages * 5, 100),
        ):
            link_key = srv.normalize_url(link["url"])
            if link_key and link_key not in visited and link_key not in queued:
                if len(queue) >= queue_limit:
                    truncated = True
                    continue
                queued.add(link_key)
                queue.append((link["url"], depth + 1))

    succeeded = sum(1 for item in results if item.get("success"))
    out: dict[str, object] = {
        "success": succeeded > 0,
        "seed_url": seed_url,
        "mode": "site",
        "max_depth": max_depth,
        "max_pages": max_pages,
        "unique": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "truncated": bool(queue) or truncated or deadline_exceeded,
        "results": results,
    }
    if deadline_exceeded:
        out["deadline_exceeded"] = True
    if _cancelled():
        out["cancelled"] = True
    if invalid:
        out["invalid"] = invalid
    if succeeded == 0:
        if _cancelled():
            out["cancelled"] = True
        else:
            out["error"] = "递归抓取未取得任何页面"
            out["error_code"] = "CRAWL_FAILED"
    return out


def smart3w_crawl(
    urls: list[str] | None = None,
    mode: str = "get",
    compress: bool = True,
    timeout: int | None = None,
    max_length: int = 5000,
    concurrency: int = 3,
    respect_robots: bool = True,
    seed_url: str = "",
    max_depth: int = 0,
    max_pages: int = 20,
    same_domain: bool = True,
    path_include: str = "",
    path_exclude: str = "",
    run_async: bool = False,
) -> dict[str, object]:
    """Batch-fetch URL 列表，或从 seed_url 递归抓取站点（BFS，可选后台任务）。

    Args:
        urls: 显式 URL 列表（1-100 个）；与 seed_url 二选一
        mode: Fetch strategy — smart/get/fetch/stealthy
        compress: Whether to extract readable content (True) or raw HTML (False)
        timeout: Per-request timeout in seconds
        max_length: Maximum characters per result (0 = unlimited)
        concurrency: Parallel workers (1-8，仅显式列表模式)
        respect_robots: Whether to respect robots.txt (默认开启)
        seed_url: 递归抓取的种子 URL；提供时进入站点递归模式
        max_depth: 递归深度（1-5；seed_url 模式下 0 视为 1）
        max_pages: 递归抓取最多页面数（1-500）
        same_domain: 递归时是否只跟随同站（含子域名）链接
        path_include: 逗号分隔的路径包含模式，支持 * 通配符
        path_exclude: 逗号分隔的路径排除模式，支持 * 通配符
        run_async: 是否把递归抓取提交为后台任务（仅 seed_url 模式）

    Returns:
        dict: 显式列表模式返回 {success, requested, unique, duplicates_skipped,
               invalid?, succeeded, failed, mode, results: [...]}；
              递归模式返回 {success, seed_url, mode: site, unique, succeeded,
               failed, truncated, results: [...]}；
              run_async=true 时返回 {success, job_id, status}。
    """
    if timeout is None:
        timeout = srv.DEFAULT_TIMEOUT
    url_list = list(urls) if isinstance(urls, (list, tuple)) else []
    seed = seed_url.strip() if isinstance(seed_url, str) else ""
    if not srv._is_int(max_depth) or max_depth < 0 or max_depth > 5:
        return {"success": False, "error": "max_depth 必须是 0-5 的整数", "error_code": "INVALID_ARGUMENT"}
    if not srv._is_int(max_pages) or max_pages < 1 or max_pages > 500:
        return {"success": False, "error": "max_pages 必须是 1-500 的整数", "error_code": "INVALID_ARGUMENT"}
    if not seed and max_depth > 0:
        if len(url_list) == 1:
            seed = url_list[0].strip() if isinstance(url_list[0], str) else ""
            url_list = []
        elif url_list:
            return {
                "success": False,
                "error": "递归抓取请提供 seed_url，或只传一个 URL",
                "error_code": "INVALID_ARGUMENT",
            }

    if mode not in srv.FETCH_MODES:
        return {"success": False, "error": f"无效抓取模式: {mode}", "error_code": "INVALID_ARGUMENT"}
    if not srv._is_int(timeout) or timeout < 1 or timeout > 120:
        return {"success": False, "error": "timeout 必须是 1-120 的整数（秒）", "error_code": "INVALID_ARGUMENT"}
    if not srv._is_int(max_length) or max_length < 0 or max_length > 200000:
        return {"success": False, "error": "max_length 必须是 0-200000 的整数", "error_code": "INVALID_ARGUMENT"}
    if not srv._is_int(concurrency) or concurrency < 1 or concurrency > 8:
        return {"success": False, "error": "concurrency 必须是 1-8 的整数", "error_code": "INVALID_ARGUMENT"}
    for name, value in (
        ("compress", compress),
        ("respect_robots", respect_robots),
        ("same_domain", same_domain),
        ("run_async", run_async),
    ):
        bool_error = srv._bool_error(value, name)
        if bool_error:
            return {"success": False, "error": bool_error, "error_code": "INVALID_ARGUMENT"}

    if seed:
        if url_list:
            return {"success": False, "error": "seed_url 与 urls 不能同时使用", "error_code": "INVALID_ARGUMENT"}
        seed_error = srv._validate_http_url(seed)
        if seed_error:
            return {"success": False, "seed_url": seed, "error": seed_error, "error_code": "INVALID_ARGUMENT"}
        depth = 1 if max_depth == 0 else max_depth
        if run_async:
            try:
                job = srv._new_job()
            except srv.JobLimitError as exc:
                return {"success": False, "error": str(exc), "error_code": "JOB_LIMIT"}
            thread = threading.Thread(
                target=srv._run_job,
                args=(
                    job,
                    srv._crawl_site,
                    seed,
                    mode,
                    compress,
                    timeout,
                    max_length,
                    respect_robots,
                    depth,
                    max_pages,
                    same_domain,
                    path_include,
                    path_exclude,
                    job,
                ),
                daemon=True,
                name=f"smart3w-crawl-{job['job_id'][:8]}",
            )
            try:
                thread.start()
            except Exception as exc:
                with srv.JOBS_LOCK:
                    job["status"] = "failed"
                    job["error"] = f"后台任务启动失败: {exc}"
                    job["finished_at"] = time.time()
                return {
                    "success": False,
                    "job_id": job["job_id"],
                    "error": f"后台任务启动失败: {exc}",
                    "error_code": "INTERNAL_ERROR",
                }
            return {
                "success": True,
                "job_id": job["job_id"],
                "status": "queued",
                "seed_url": seed,
                "message": "任务已提交，请用 smart3w_job 查询进度和结果",
            }
        return srv._crawl_site(
            seed,
            mode,
            compress,
            timeout,
            max_length,
            respect_robots,
            depth,
            max_pages,
            same_domain,
            path_include,
            path_exclude,
        )

    if not url_list or len(url_list) > 100:
        return {
            "success": False,
            "error": "urls 必须是 1-100 个 URL，或提供 seed_url",
            "error_code": "INVALID_ARGUMENT",
        }

    invalid: list[str] = []
    valid: list[str] = []
    unique: list[str] = []
    seen: set[str] = set()
    for raw_url in url_list:
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

    base = {
        "requested": len(url_list),
        "unique": len(unique),
        "duplicates_skipped": max(0, len(valid) - len(unique)),
        "invalid_count": len(invalid),
        "mode": mode,
    }
    if invalid:
        base["invalid"] = invalid
    if not unique:
        return {
            **base,
            "success": False,
            "succeeded": 0,
            "failed": 0,
            "results": [],
            "error": "没有可抓取的合法 URL",
            "error_code": "INVALID_ARGUMENT",
        }

    def _crawl_one(url: str) -> dict:
        result: dict = {}
        for attempt in range(3):
            try:
                result = srv._run_fetch_file(
                    url,
                    mode,
                    compress,
                    timeout,
                    max_length=0,
                    start_index=0,
                    use_cache=True,
                    respect_robots=respect_robots,
                )
            except Exception as exc:  # 单个 URL 的意外错误不应中断整批
                result = {
                    "success": False,
                    "url": url,
                    "mode": mode,
                    "error": str(exc),
                    "error_code": "INTERNAL_ERROR",
                }
            if result.get("success") or not srv.is_rate_limited(result.get("error", "")):
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
                    "success": False,
                    "url": unique[idx],
                    "mode": mode,
                    "error": str(exc),
                    "error_code": "INTERNAL_ERROR",
                }

    results = [srv.apply_window(results_by_index[idx], 0, max_length) for idx in range(len(unique))]
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
        out["error_code"] = "CRAWL_FAILED"
    return out
