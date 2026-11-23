"""浏览器交互能力：在独立会话中执行点击、填写、登录、分页等动作。

设计原则：
- 只接受声明式动作白名单，不执行任意 JavaScript；
- 浏览器发出的每个 http/https 请求都经过现有 SSRF/allowlist 策略校验；
- 登录状态通过 profile 保存为 Playwright storage_state（含 Cookie/localStorage），
  默认目录为 ~/.smart3w/profiles，可用 SMART3W_PROFILE_DIR 修改；
- 依赖 Playwright（由 scrapling[all] 间接安装）与本机 Chrome/Chromium。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from smart3w_url_utils import env_flag, proxy_url, ssrf_blocked, url_policy_error, url_shape_error

MAX_ACTIONS = 50
MAX_ACTION_WAIT_MS = 10_000
MAX_SELECTOR_LENGTH = 2_048
MAX_VALUE_LENGTH = 20_000
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PROFILE_DIR_ENV = "SMART3W_PROFILE_DIR"
CHROME_PATH_ENV = "SMART3W_CHROME_PATH"
INTERACT_MAX_SECONDS_ENV = "SMART3W_INTERACT_MAX_SECONDS"
DEFAULT_INTERACT_MAX_SECONDS = 300

ACTION_TYPES = frozenset({
    "goto",
    "click",
    "click_text",
    "fill",
    "press",
    "select",
    "check",
    "uncheck",
    "wait",
    "wait_for_load",
    "scroll",
})

CONTENT_FORMATS = frozenset({"markdown", "html", "text"})


class InteractionError(Exception):
    """带稳定错误码的交互异常，供 MCP 层转成结构化响应。"""

    def __init__(self, message: str, code: str = "INTERACT_FAILED"):
        super().__init__(message)
        self.code = code


def _require_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InteractionError(f"{name} 必须是整数", "INVALID_ARGUMENT")
    if value < low or value > high:
        raise InteractionError(f"{name} 必须在 {low}-{high} 之间", "INVALID_ARGUMENT")
    return value


def _require_text(value: object, name: str, *, allow_empty: bool = False, max_length: int = MAX_VALUE_LENGTH) -> str:
    if not isinstance(value, str):
        raise InteractionError(f"{name} 必须是字符串", "INVALID_ARGUMENT")
    text = value.strip()
    if not text and not allow_empty:
        raise InteractionError(f"{name} 不能为空", "INVALID_ARGUMENT")
    if len(text) > max_length:
        raise InteractionError(f"{name} 长度不能超过 {max_length}", "INVALID_ARGUMENT")
    return text


def normalize_actions(actions: object) -> list[dict[str, object]]:
    """校验并规范化动作列表；返回可直接执行的副本。"""
    if actions is None:
        return []
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except json.JSONDecodeError as exc:
            raise InteractionError(f"actions 不是合法 JSON: {exc}", "INVALID_ARGUMENT") from exc
    if not isinstance(actions, list):
        raise InteractionError("actions 必须是数组", "INVALID_ARGUMENT")
    if len(actions) > MAX_ACTIONS:
        raise InteractionError(f"actions 最多 {MAX_ACTIONS} 个", "INVALID_ARGUMENT")

    allowed_keys = {
        "type", "action", "selector", "value", "text", "key", "url", "ms", "state",
        "to", "y", "wait_ms", "timeout_ms", "exact", "option",
    }
    normalized: list[dict[str, object]] = []
    for index, raw in enumerate(actions):
        prefix = f"actions[{index}]"
        if not isinstance(raw, dict):
            raise InteractionError(f"{prefix} 必须是对象", "INVALID_ARGUMENT")
        unknown = set(raw) - allowed_keys
        if unknown:
            raise InteractionError(f"{prefix} 包含未知字段: {', '.join(sorted(unknown))}", "INVALID_ARGUMENT")

        kind = raw.get("type", raw.get("action"))
        kind = kind.strip().lower() if isinstance(kind, str) else ""
        if kind not in ACTION_TYPES:
            raise InteractionError(f"{prefix}.type 不支持: {kind or '(空)'}", "INVALID_ARGUMENT")
        item: dict[str, object] = {"type": kind}

        if kind in {"click", "fill", "select", "check", "uncheck"} or (
            kind == "press" and isinstance(raw.get("selector"), str) and raw.get("selector", "").strip()
        ):
            item["selector"] = _require_text(raw.get("selector"), f"{prefix}.selector", max_length=MAX_SELECTOR_LENGTH)
        if kind == "click_text":
            item["text"] = _require_text(raw.get("text"), f"{prefix}.text")
        if kind == "fill":
            item["value"] = _require_text(raw.get("value"), f"{prefix}.value", allow_empty=True)
        if kind == "select":
            value = raw.get("value", raw.get("option"))
            if isinstance(value, list):
                if not value or any(not isinstance(v, str) or not v for v in value):
                    raise InteractionError(f"{prefix}.value 必须是非空字符串数组", "INVALID_ARGUMENT")
                item["value"] = value
            else:
                item["value"] = _require_text(value, f"{prefix}.value")
        if kind == "press":
            item["key"] = _require_text(raw.get("key"), f"{prefix}.key", max_length=100)
            if "selector" not in item:
                item.pop("selector", None)
        if kind == "goto":
            url = _require_text(raw.get("url"), f"{prefix}.url", max_length=8192)
            shape_error = url_shape_error(url)
            if shape_error:
                raise InteractionError(f"{prefix}.url 格式无效: {shape_error}", "INVALID_ARGUMENT")
            blocked = ssrf_blocked(url)
            if blocked:
                raise InteractionError(f"{prefix}.url 被安全策略拒绝: {blocked}", "SSRF_BLOCKED")
            item["url"] = url
        if kind == "wait":
            has_ms = "ms" in raw
            has_selector = isinstance(raw.get("selector"), str) and raw.get("selector", "").strip()
            if not has_ms and not has_selector:
                raise InteractionError(f"{prefix} 需要 ms 或 selector", "INVALID_ARGUMENT")
            if has_ms:
                item["ms"] = _require_int(raw.get("ms"), f"{prefix}.ms", 0, 60_000)
            if has_selector:
                item["selector"] = _require_text(raw.get("selector"), f"{prefix}.selector", max_length=MAX_SELECTOR_LENGTH)
        if kind == "wait_for_load":
            state = raw.get("state", "load")
            if state not in {"load", "domcontentloaded", "networkidle"}:
                raise InteractionError(f"{prefix}.state 必须是 load/domcontentloaded/networkidle", "INVALID_ARGUMENT")
            item["state"] = state
        if kind == "scroll":
            to = raw.get("to")
            if to is not None:
                if to not in {"top", "bottom"}:
                    raise InteractionError(f"{prefix}.to 必须是 top 或 bottom", "INVALID_ARGUMENT")
                item["to"] = to
            else:
                item["y"] = _require_int(raw.get("y"), f"{prefix}.y", -100_000, 100_000)
        if "wait_ms" in raw:
            item["wait_ms"] = _require_int(raw.get("wait_ms"), f"{prefix}.wait_ms", 0, MAX_ACTION_WAIT_MS)
        if "timeout_ms" in raw:
            item["timeout_ms"] = _require_int(raw.get("timeout_ms"), f"{prefix}.timeout_ms", 100, 120_000)
        if kind == "click_text" and "exact" in raw:
            if not isinstance(raw.get("exact"), bool):
                raise InteractionError(f"{prefix}.exact 必须是布尔值", "INVALID_ARGUMENT")
            item["exact"] = raw["exact"]
        normalized.append(item)
    return normalized


def validate_profile(profile: object) -> str:
    """校验 profile 名称；空字符串表示一次性会话，不落盘。"""
    if profile is None:
        return ""
    if not isinstance(profile, str):
        raise InteractionError("profile 必须是字符串", "INVALID_ARGUMENT")
    value = profile.strip()
    if not value:
        return ""
    if not PROFILE_RE.match(value):
        raise InteractionError("profile 只能包含字母、数字、下划线、点和短横线，长度 1-64", "INVALID_ARGUMENT")
    return value


def profile_path(profile: str) -> Path | None:
    if not profile:
        return None
    override = (os.environ.get(PROFILE_DIR_ENV) or "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".smart3w" / "profiles"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base / f"{profile}.json"


def _load_state(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InteractionError(f"profile 文件无法读取: {exc}", "PROFILE_INVALID") from exc
    if not isinstance(data, dict):
        raise InteractionError("profile 文件格式无效", "PROFILE_INVALID")
    return data


def _save_state(path: Path | None, state: dict[str, object]) -> None:
    if path is None:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def normalize_cookies(cookies: object) -> list[dict[str, object]]:
    """校验调用方传入的 Cookie，允许 url 或 domain+path 两种写法。"""
    if cookies is None:
        return []
    if not isinstance(cookies, list):
        raise InteractionError("cookies 必须是数组", "INVALID_ARGUMENT")
    if len(cookies) > 300:
        raise InteractionError("cookies 最多 300 条", "INVALID_ARGUMENT")
    allowed = {"name", "value", "url", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
    result: list[dict[str, object]] = []
    for index, raw in enumerate(cookies):
        prefix = f"cookies[{index}]"
        if not isinstance(raw, dict):
            raise InteractionError(f"{prefix} 必须是对象", "INVALID_ARGUMENT")
        unknown = set(raw) - allowed
        if unknown:
            raise InteractionError(f"{prefix} 包含未知字段: {', '.join(sorted(unknown))}", "INVALID_ARGUMENT")
        name = _require_text(raw.get("name"), f"{prefix}.name", max_length=256)
        value = _require_text(raw.get("value"), f"{prefix}.value", allow_empty=True, max_length=8192)
        item: dict[str, object] = {"name": name, "value": value}
        if isinstance(raw.get("url"), str) and raw["url"].strip():
            item["url"] = _require_text(raw["url"], f"{prefix}.url", max_length=8192)
        elif isinstance(raw.get("domain"), str) and raw["domain"].strip():
            item["domain"] = _require_text(raw["domain"], f"{prefix}.domain", max_length=256)
            item["path"] = _require_text(raw.get("path", "/"), f"{prefix}.path", max_length=1024)
        else:
            raise InteractionError(f"{prefix} 需要 url 或 domain", "INVALID_ARGUMENT")
        if "expires" in raw:
            expires = raw["expires"]
            if isinstance(expires, bool) or not isinstance(expires, (int, float)):
                raise InteractionError(f"{prefix}.expires 必须是数字", "INVALID_ARGUMENT")
            item["expires"] = float(expires)
        for key in ("httpOnly", "secure"):
            if key in raw:
                if not isinstance(raw[key], bool):
                    raise InteractionError(f"{prefix}.{key} 必须是布尔值", "INVALID_ARGUMENT")
                item[key] = raw[key]
        if "sameSite" in raw:
            same_site = raw["sameSite"]
            if same_site not in {"Strict", "Lax", "None"}:
                raise InteractionError(f"{prefix}.sameSite 必须是 Strict/Lax/None", "INVALID_ARGUMENT")
            item["sameSite"] = same_site
        result.append(item)
    return result


def _chrome_candidates() -> list[str]:
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        return [
            str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
            for root in roots if root
        ]
    if sys.platform == "darwin":
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return []


def find_chrome() -> str | None:
    override = (os.environ.get(CHROME_PATH_ENV) or "").strip()
    if override:
        if Path(override).is_file():
            return override
        raise InteractionError(f"{CHROME_PATH_ENV} 指向的文件不存在: {override}", "BROWSER_UNAVAILABLE")
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _chrome_candidates():
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _proxy_config():
    raw = proxy_url()
    if not raw:
        return None
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    if not parsed.hostname:
        raise InteractionError("SMART3W_PROXY_URL 格式无效", "INVALID_ARGUMENT")
    scheme = parsed.scheme.lower()
    if scheme in {"socks", "socks5h"}:
        scheme = "socks5"
    server = f"{scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    config = {"server": server}
    if parsed.username:
        config["username"] = parsed.username
    pwd = parsed.password
    if pwd:
        config["password"] = pwd
    return config

def _launch_browser(playwright):
    args = ["--disable-dev-shm-usage", "--disable-gpu", "--disable-blink-features=AutomationControlled"]
    if env_flag("SMART3W_BROWSER_NO_SANDBOX", False) or (
        os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0
    ):
        args.append("--no-sandbox")
    kwargs: dict[str, object] = {
        "headless": env_flag("SMART3W_BROWSER_HEADLESS", True),
        "args": args,
    }
    proxy = _proxy_config()
    if proxy:
        kwargs["proxy"] = proxy
    chrome = find_chrome()
    if chrome:
        kwargs["executable_path"] = chrome
        return playwright.chromium.launch(**kwargs)
    kwargs["channel"] = "chrome"
    try:
        return playwright.chromium.launch(**kwargs)
    except Exception:
        kwargs.pop("channel", None)
        try:
            return playwright.chromium.launch(**kwargs)
        except Exception as exc:
            raise InteractionError(
                f"无法启动 Chrome: {exc}。请安装 Chrome、设置 {CHROME_PATH_ENV}，或执行 playwright install chromium",
                "BROWSER_UNAVAILABLE",
            ) from exc


def _max_interact_seconds() -> int:
    """浏览器交互整体墙钟上限，防止多动作/慢站点拖死调用方。"""
    raw = os.environ.get(INTERACT_MAX_SECONDS_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERACT_MAX_SECONDS
    return max(10, value)


def _install_request_guard(target, blocked_errors: list[str]):
    """所有浏览器请求都过 SSRF/allowlist；非 http(s) 资源放行。

    target 可以是 Page，也可以是 BrowserContext（推荐：context.route 能同时
    覆盖点击打开的新页面与弹窗），二者都提供 route(pattern, handler)。
    """
    policy_cache: dict[tuple[str, str], str | None] = {}

    def guard(route):
        request = route.request
        url = request.url
        if not url.lower().startswith(("http://", "https://")):
            return route.continue_()
        # 每个请求都做形状校验：长度、控制字符、端口、userinfo 不能随 origin 缓存
        shape_error = url_shape_error(url)
        if shape_error:
            if len(blocked_errors) < 20:
                blocked_errors.append(f"{url}: {shape_error}")
            return route.abort("blockedbyclient")
        parsed = urlsplit(url)
        # 不能只按 hostname 缓存：同 host 不同端口/凭据的请求策略可能不同
        key = (parsed.scheme.lower(), parsed.netloc.lower())
        if key not in policy_cache:
            policy_cache[key] = url_policy_error(url)
        blocked = policy_cache[key]
        if blocked:
            if len(blocked_errors) < 20:
                blocked_errors.append(f"{url}: {blocked}")
            return route.abort("blockedbyclient")
        return route.continue_()

    target.route("**/*", guard)


def _execute_actions(
    page,
    actions: list[dict[str, object]],
    default_wait_ms: int,
    deadline: float | None = None,
) -> list[str]:
    """按动作列表执行；仅使用 Playwright 公开 API，不执行页面 JavaScript。

    deadline 为 time.monotonic() 绝对时刻；提供时每个动作与等待都受剩余时间约束，
    避免单个长动作把整体交互拖过调用方超时。
    """
    completed: list[str] = []
    for index, action in enumerate(actions):
        kind = str(action["type"])
        timeout = action.get("timeout_ms")
        if deadline is not None:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise InteractionError("浏览器交互总时长超限", "INTERACTION_TIMEOUT")
            timeout = remaining_ms if timeout is None else min(int(cast(Any, timeout)), remaining_ms)
        try:
            if kind == "goto":
                page.goto(str(action["url"]), wait_until="domcontentloaded", timeout=timeout)
            elif kind == "click":
                page.locator(str(action["selector"])).first.click(timeout=timeout)
            elif kind == "click_text":
                page.get_by_text(str(action["text"]), exact=bool(action.get("exact", False))).first.click(timeout=timeout)
            elif kind == "fill":
                page.locator(str(action["selector"])).first.fill(str(action.get("value", "")), timeout=timeout)
            elif kind == "press":
                locator = action.get("selector")
                if locator:
                    page.locator(str(locator)).first.press(str(action["key"]), timeout=timeout)
                else:
                    page.keyboard.press(str(action["key"]))
            elif kind == "select":
                page.locator(str(action["selector"])).first.select_option(action["value"], timeout=timeout)
            elif kind == "check":
                page.locator(str(action["selector"])).first.check(timeout=timeout)
            elif kind == "uncheck":
                page.locator(str(action["selector"])).first.uncheck(timeout=timeout)
            elif kind == "wait":
                if "ms" in action:
                    page.wait_for_timeout(int(cast(Any, action["ms"])))
                else:
                    page.locator(str(action["selector"])).first.wait_for(state="visible", timeout=timeout)
            elif kind == "wait_for_load":
                page.wait_for_load_state(str(action.get("state", "load")), timeout=timeout)
            elif kind == "scroll":
                target = action.get("to")
                home_key = "Meta+ArrowUp" if sys.platform == "darwin" else "Control+Home"
                end_key = "Meta+ArrowDown" if sys.platform == "darwin" else "Control+End"
                if target == "top":
                    page.keyboard.press(home_key)
                elif target == "bottom":
                    page.keyboard.press(end_key)
                else:
                    page.mouse.wheel(0, int(cast(Any, action.get("y", 0))))
        except Exception as exc:
            raise InteractionError(f"actions[{index}] ({kind}) 执行失败: {exc}", "ACTION_FAILED") from exc

        completed.append(kind)
        extra_wait = int(cast(Any, action.get("wait_ms", default_wait_ms)))
        if deadline is not None:
            extra_wait = min(extra_wait, max(0, int((deadline - time.monotonic()) * 1000)))
        if extra_wait > 0:
            page.wait_for_timeout(extra_wait)
    return completed


def _extract_content(page, content_format: str) -> str:
    if content_format == "html":
        return page.content()
    if content_format == "text":
        return page.inner_text("body")
    html = page.content()
    try:
        from markdownify import markdownify
        from readability import Document

        summary = Document(html).summary(html_partial=True)
        return markdownify(summary, heading_style="ATX")
    except Exception:
        try:
            from markdownify import markdownify

            return markdownify(html, heading_style="ATX")
        except Exception:
            return page.inner_text("body")


def run_interaction(
    url: str,
    actions: object = None,
    *,
    timeout: int = 30,
    wait_ms: int = 500,
    content_format: str = "markdown",
    max_content: int = 200_000,
    profile: str = "",
    cookies: object = None,
    return_cookies: bool = False,
) -> dict[str, object]:
    """启动浏览器、执行动作并返回最终页面内容。同步 API，由 MCP 线程池调用。"""
    normalized = normalize_actions(actions)
    profile = validate_profile(profile)
    normalized_cookies = normalize_cookies(cookies)
    timeout = _require_int(timeout, "timeout", 1, 120)
    wait_ms = _require_int(wait_ms, "wait_ms", 0, MAX_ACTION_WAIT_MS)
    max_content = _require_int(max_content, "max_content", 0, 1_000_000)
    if content_format not in CONTENT_FORMATS:
        raise InteractionError("content_format 必须是 markdown/html/text", "INVALID_ARGUMENT")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise InteractionError(
            "浏览器自动化依赖未安装（需要 playwright）；请安装 smart3w 的完整依赖", "BROWSER_UNAVAILABLE"
        ) from exc

    state_path = profile_path(profile)
    state = _load_state(state_path)
    blocked_errors: list[str] = []
    started = time.monotonic()
    deadline = started + _max_interact_seconds()
    context = None
    result: dict[str, object] = {}

    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            try:
                context = browser.new_context(
                    storage_state=state,
                    viewport={"width": 1366, "height": 900},
                    ignore_https_errors=not env_flag("SMART3W_SSL_VERIFY", True),
                    # service worker 的请求不经过 page.route，默认阻断以保持 SSRF 防线完整
                    service_workers="allow" if env_flag("SMART3W_ALLOW_SERVICE_WORKERS", False) else "block",
                )
                # context 级路由覆盖初始页面、点击打开的新页面和弹窗
                _install_request_guard(context, blocked_errors)

                def _configure_new_page(new_page):
                    new_page.set_default_timeout(timeout * 1000)
                    new_page.set_default_navigation_timeout(timeout * 1000)

                context.on("page", _configure_new_page)
                if normalized_cookies:
                    context.add_cookies(normalized_cookies)
                page = context.new_page()
                page.set_default_timeout(timeout * 1000)
                page.set_default_navigation_timeout(timeout * 1000)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                except Exception as exc:
                    if blocked_errors:
                        raise InteractionError(f"页面导航被安全策略拒绝: {blocked_errors[0]}", "SSRF_BLOCKED") from exc
                    raise
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                completed = _execute_actions(page, normalized, wait_ms, deadline=deadline)
                final_url = page.url
                title = page.title()
                content = _extract_content(page, content_format)
                total_length = len(content)
                truncated = bool(max_content and total_length > max_content)
                if truncated:
                    content = content[:max_content]
                result = {
                    "url": url,
                    "final_url": final_url,
                    "title": title,
                    "content": content,
                    "content_format": content_format,
                    "content_total_length": total_length,
                    "content_truncated": truncated,
                    "actions": completed,
                    "action_count": len(completed),
                    "profile": profile,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
                if normalized_cookies:
                    result["cookies_sent"] = len(normalized_cookies)
                if return_cookies:
                    result["cookies"] = context.cookies()
            finally:
                if context is not None and state_path is not None:
                    try:
                        _save_state(state_path, context.storage_state())
                    except Exception:
                        pass
                browser.close()
    except InteractionError:
        raise
    except Exception as exc:
        if blocked_errors:
            raise InteractionError(f"浏览器请求被安全策略拒绝: {blocked_errors[0]}", "SSRF_BLOCKED") from exc
        raise InteractionError(f"浏览器交互失败: {exc}", "INTERACT_FAILED") from exc

    if blocked_errors:
        result["blocked_requests"] = blocked_errors[:10]
    return result
