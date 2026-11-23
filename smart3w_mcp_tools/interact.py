# 由 mcp_server.py 拆分出的 MCP 工具实现；通过 srv 动态访问 mcp_server 的全局对象，保持可测性。
import asyncio

from smart3w_mcp_tools._server import srv


async def smart3w_interact(
    url: str,
    actions: list[dict[str, object]] | None = None,
    timeout: int = 30,
    wait_ms: int = 500,
    content_format: str = "markdown",
    max_content: int = 200000,
    profile: str = "",
    cookies: list[dict[str, object]] | None = None,
    return_cookies: bool = False,
    respect_robots: bool = True,
) -> dict[str, object]:
    """在真实浏览器中执行点击、填写、登录、分页等动作，并返回最终页面内容。

    动作使用声明式白名单，不执行任意 JavaScript。每个浏览器请求都会经过
    SSRF/主机白名单校验。profile 用于保存登录状态（Cookie/localStorage）。

    Args:
        url: 起始 URL（http/https）
        actions: 动作数组；支持 goto/click/click_text/fill/press/select/check/
            uncheck/wait/wait_for_load/scroll，最多 50 个
        timeout: 单步导航/动作超时秒数（1-120）
        wait_ms: 每个动作后的等待毫秒数（0-10000）
        content_format: 最终内容格式 markdown/html/text
        max_content: 返回内容最大字符数（0 表示不截断，最大 1000000）
        profile: 登录状态名称（仅字母数字下划线点和短横线，1-64）；空表示不保存
        cookies: 初始 Cookie 数组，元素需包含 name/value 与 url 或 domain
        return_cookies: 是否在结果中返回当前所有 Cookie
        respect_robots: 是否检查起始 URL 的 robots.txt

    Returns:
        dict: {success, url, final_url, title, content, content_format,
               content_total_length, content_truncated, actions, action_count,
               profile, duration_ms, cookies?, blocked_requests?}
    """
    if not isinstance(url, str):
        return {"success": False, "url": str(url), "error": "url 必须是字符串", "error_code": "INVALID_ARGUMENT"}
    url = url.strip()
    # URL 策略包含 DNS 解析，放到线程里避免阻塞事件循环
    url_error = await asyncio.to_thread(srv._validate_http_url, url)
    if url_error:
        return {"success": False, "url": url, "error": url_error, "error_code": "INVALID_ARGUMENT"}
    if not srv._is_int(timeout) or timeout < 1 or timeout > 120:
        return {"success": False, "url": url, "error": "timeout 必须是 1-120 的整数", "error_code": "INVALID_ARGUMENT"}
    if not srv._is_int(wait_ms) or wait_ms < 0 or wait_ms > 10000:
        return {
            "success": False,
            "url": url,
            "error": "wait_ms 必须是 0-10000 的整数",
            "error_code": "INVALID_ARGUMENT",
        }
    if not srv._is_int(max_content) or max_content < 0 or max_content > 1000000:
        return {
            "success": False,
            "url": url,
            "error": "max_content 必须是 0-1000000 的整数",
            "error_code": "INVALID_ARGUMENT",
        }
    if content_format not in {"markdown", "html", "text"}:
        return {
            "success": False,
            "url": url,
            "error": "content_format 必须是 markdown/html/text",
            "error_code": "INVALID_ARGUMENT",
        }
    for name, value in (("return_cookies", return_cookies), ("respect_robots", respect_robots)):
        bool_error = srv._bool_error(value, name)
        if bool_error:
            return {"success": False, "url": url, "error": bool_error, "error_code": "INVALID_ARGUMENT"}
    try:
        normalized_actions = srv.normalize_actions(actions or [])
        profile = srv.validate_profile(profile)
        normalized_cookies = srv.normalize_cookies(cookies or [])
    except srv.InteractionError as exc:
        return {"success": False, "url": url, "error": str(exc), "error_code": exc.code}
    if respect_robots and not await asyncio.to_thread(srv.ROBOTS_CHECKER.allows, url):
        return {"success": False, "url": url, "error": "robots.txt 禁止抓取该 URL", "error_code": "ROBOTS_BLOCKED"}
    try:
        result = await asyncio.to_thread(
            srv.run_interaction,
            url,
            normalized_actions,
            timeout=timeout,
            wait_ms=wait_ms,
            content_format=content_format,
            max_content=max_content,
            profile=profile,
            cookies=normalized_cookies,
            return_cookies=return_cookies,
        )
    except srv.InteractionError as exc:
        return {"success": False, "url": url, "error": str(exc), "error_code": exc.code}
    except Exception as exc:
        return {"success": False, "url": url, "error": f"浏览器交互失败: {exc}", "error_code": "INTERACT_FAILED"}
        # run_interaction 正常返回视为成功；若内部已标记失败则保留其结论
    result.setdefault("success", True)
    return result
