#!/usr/bin/env python3
"""Smart3W MCP Server - 将 smart3w 搜索与网页抓取能力暴露为 MCP 工具。"""

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server import MCPServer

from smart3w_content import filter_content
from smart3w_core import (
    DEFAULT_INSTANCE,
    FETCH_MODES,
    SEARCH_TIME_RANGES,
    SitemapError,
    collect_sitemap_entries,
    parse_sitemap,
    search_web,
)
from smart3w_discovery import extract_fields, extract_links, parse_robots_sitemaps, path_allowed, same_site
from smart3w_fetch_utils import (
    DEFAULT_RESPECT_ROBOTS,
    DomainRateLimiter,
    RobotsChecker,
    TTLCache,
    apply_window,
    is_rate_limited,
)
from smart3w_interact import (
    InteractionError,
    normalize_actions,
    normalize_cookies,
    run_interaction,
    validate_profile,
)
from smart3w_url_utils import normalize_url, ssrf_blocked, url_policy_error, url_shape_error

__version__ = "2.9.0"

SERVER_DIR = Path(__file__).resolve().parent
FETCH_SH = SERVER_DIR / "scripts" / "fetch.sh"
SEARXNG_INSTANCE = os.environ.get("SEARXNG_INSTANCE", DEFAULT_INSTANCE)

# 与 scripts/fetch.sh 的默认尝试次数保持一致（下方会显式传给 fetch.sh）
FETCH_RETRY = 2
_STRATEGY_COUNT = {"get": 2, "fetch": 1, "stealthy": 1, "smart": 4}


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

mcp = MCPServer("smart3w", version=__version__)


class FetchError(RuntimeError):
    """fetch.sh 执行失败；子类通过 error_code 区分具体原因。"""

    error_code = "FETCH_FAILED"


class FetchTimeoutError(FetchError):
    error_code = "FETCH_TIMEOUT"


class FetchUnavailableError(FetchError):
    error_code = "FETCH_UNAVAILABLE"


class FetchCancelledError(FetchError):
    """后台任务取消导致抓取中止。"""

    error_code = "FETCH_CANCELLED"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bool_error(value: object, name: str) -> str | None:
    """校验必填布尔参数；返回错误信息或 None。"""
    if not isinstance(value, bool):
        return f"{name} 必须是布尔值"
    return None


def _optional_bool_error(value: object, name: str) -> str | None:
    """校验可空布尔参数；None 表示跟随默认值。"""
    if value is not None and not isinstance(value, bool):
        return f"{name} 必须是布尔值或 null"
    return None


def _fetch_subprocess_timeout(timeout: int, mode: str) -> int:
    """计算 fetch.sh 子进程超时，覆盖每个策略的尝试次数与 smart 降级链路总时长。"""
    strategies = _STRATEGY_COUNT.get(mode, 1)
    # 每次尝试按 timeout 计（curl --max-time / scrapling timeout），另加建连与进程开销；
    # 乘以尝试次数与策略数后，再留固定余量。
    return (timeout + 15) * FETCH_RETRY * strategies + 15


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """尽力终止 fetch.sh 及其子进程，避免取消后留下后台抓取。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
            )
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        try:
            proc.kill()
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _validate_http_url(url: str) -> str | None:
    """校验 URL：仅允许 http/https 且带主机名；非法时返回错误信息，否则返回 None。"""
    return url_policy_error(url)


def _resolve_bash() -> str:
    """定位可用的 bash：优先 SMART3W_BASH，其次非 WSL 的 bash。

    Windows 的 CreateProcess 可能先命中 C:\\Windows\\System32\\bash.exe（WSL），
    导致 fetch.sh 报 WSL 未安装；这里显式跳过并回退到 Git Bash/MSYS2。
    """
    override = (os.environ.get("SMART3W_BASH") or "").strip()
    if override:
        if Path(override).is_file():
            return override
        raise FetchUnavailableError(f"SMART3W_BASH 指向的 bash 不存在: {override}")

    candidates: list[str] = []
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    if os.name == "nt":
        for path in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
            r"C:\msys64\usr\bin\bash.exe",
        ):
            candidates.append(path)

    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        if candidate.lower().replace("/", "\\").endswith("\\windows\\system32\\bash.exe"):
            continue
        return candidate

    raise FetchUnavailableError(
        "找不到可用的 bash；请安装 Git Bash/MSYS2，或用 SMART3W_BASH 指定 bash 路径"
    )


def _run_fetch(
    args: list[str],
    timeout: int = DEFAULT_TIMEOUT,
    respect_robots: bool | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Run fetch.sh and return stdout.

    fetch.sh 约定：数据走 stdout，日志走 stderr，因此成功时只返回 stdout，
    失败时把 stderr（或 stdout）作为异常信息抛出，避免日志污染数据通道。

    统一使用 Popen 轮询：可在超时或后台任务取消时终止 fetch.sh 及其子进程；
    不传 cancel_event 时内部创建一个永不触发的事件，行为与普通调用一致。
    """
    if not FETCH_SH.is_file():
        raise FetchUnavailableError(f"找不到抓取脚本: {FETCH_SH}")
    if cancel_event is not None and cancel_event.is_set():
        raise FetchCancelledError("抓取已取消")

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

    command = [_resolve_bash(), str(FETCH_SH), *args]

    # 统一走 Popen 轮询：即使没有取消事件，超时也必须终止整个进程树，
    # 避免 subprocess.run 只杀 bash、把 fetch.sh 拉起的 python/curl 留成孤儿进程。
    if cancel_event is None:
        cancel_event = threading.Event()

    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **popen_kwargs,
        )
    except OSError as exc:
        raise FetchUnavailableError(f"无法执行 fetch.sh（需要 bash）: {exc}") from exc

    deadline = time.monotonic() + timeout
    stdout = ""
    stderr = ""
    try:
        while True:
            if cancel_event.is_set():
                _terminate_process_tree(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    pass
                raise FetchCancelledError("抓取已取消")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    pass
                raise FetchTimeoutError(f"fetch.sh 执行超时（>{timeout}s）")

            try:
                stdout, stderr = proc.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if proc.poll() is None:
            _terminate_process_tree(proc)

    if proc.returncode != 0:
        detail = (stderr or "").strip() or (stdout or "").strip() or f"Exit code {proc.returncode}"
        raise FetchError(detail)
    return stdout or ""


def _read_effective_url(path: str) -> str | None:
    """读取抓取侧车文件中的最终 URL（由 fetch.sh --effective-url 写入）；缺失或为空返回 None。"""
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
    cancel_event: threading.Event | None = None,
    accept_html: bool = False,
) -> dict:
    """真正调用 fetch.sh 抓取一个 URL，返回包含完整正文的结构化结果。"""
    if cancel_event is not None and cancel_event.is_set():
        raise FetchCancelledError("抓取已取消")
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
        if accept_html:
            args.append("--accept-html")

        run_kwargs: dict[str, object] = {"respect_robots": respect_robots}
        if cancel_event is not None:
            run_kwargs["cancel_event"] = cancel_event
        try:
            log = _run_fetch(
                args,
                _fetch_subprocess_timeout(timeout, mode),
                **run_kwargs,
            )
        except FetchTimeoutError as exc:
            # 对外报告用户设置的超时，而不是内部放大的子进程超时
            raise FetchTimeoutError(f"抓取超时（>{timeout}s）") from exc
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
        document_match = re.search(r"^__SMART3W_DOCUMENT__\s+(\{.*\})\s*$", log, re.MULTILINE)
        if document_match:
            try:
                document_meta = json.loads(document_match.group(1))
            except json.JSONDecodeError:
                document_meta = None
            if isinstance(document_meta, dict):
                result["format"] = document_meta.get("kind")
                for key in ("pages", "sheets", "slides", "paragraphs", "tables", "chars"):
                    if key in document_meta:
                        result[key] = document_meta[key]
                result["document"] = document_meta
        return result
    finally:
        Path(out_path).unlink(missing_ok=True)
        Path(effective_path).unlink(missing_ok=True)


def _finalize_fetch_result(
    result: dict,
    query: str,
    max_tokens: int,
    start_index: int,
    max_length: int,
) -> dict:
    """按查询词筛选正文（可选），再按窗口参数切片。"""
    out = dict(result)
    content = out.get("content", "") or ""
    effective_limit = max_length
    if max_tokens > 0:
        # 粗估 4 字符 ≈ 1 token；两个上限同时给定时取更严格的那个
        token_limit = max_tokens * 4
        effective_limit = min(max_length, token_limit) if max_length > 0 else token_limit
    if query and out.get("success"):
        filtered, stats = filter_content(content, query, max_chars=effective_limit)
        out["content"] = filtered
        out["original_length"] = len(content)
        out["filter"] = {"query": query, **stats}
    return apply_window(out, start_index, effective_limit if effective_limit else max_length)


def _run_fetch_file(
    url: str,
    mode: str,
    compress: bool,
    timeout: int,
    max_length: int,
    start_index: int,
    use_cache: bool = True,
    respect_robots: bool | None = None,
    max_age_seconds: int = -1,
    query: str = "",
    max_tokens: int = 0,
    cancel_event: threading.Event | None = None,
    accept_html: bool | None = None,
) -> dict:
    """抓取一个 URL：检查 robots.txt、按域名限速、查缓存，最后返回窗口切片结果。

    accept_html=None 时按 compress 推导：不压缩（原始内容）需要 HTML，
    压缩为 Markdown 时允许服务端内容协商返回 Markdown。
    """
    if cancel_event is not None and cancel_event.is_set():
        raise FetchCancelledError("抓取已取消")
    if respect_robots is None:
        respect_robots = DEFAULT_RESPECT_ROBOTS
    if accept_html is None:
        accept_html = not compress

    # robots 检查必须先于缓存：否则 robots 关闭时写入的缓存会绕过后续的 robots 开启请求
    if respect_robots and not ROBOTS_CHECKER.allows(url):
        return {
            "success": False,
            "url": url,
            "mode": mode,
            "error": "robots.txt 禁止抓取该 URL",
            "error_code": "ROBOTS_BLOCKED",
        }

    if cancel_event is not None and cancel_event.is_set():
        raise FetchCancelledError("抓取已取消")

    cache_key = (normalize_url(url), mode, compress, accept_html)
    result = None
    if use_cache and max_age_seconds != 0:
        cached_entry = FETCH_CACHE.get_with_age(cache_key)
        if cached_entry is not None:
            cached, age = cached_entry
            if max_age_seconds < 0 or age <= max_age_seconds:
                result = {
                    **cached,
                    "url": url,
                    "cached": True,
                    "cache_age": round(age, 3),
                }

    if result is None:
        RATE_LIMITER.wait(url)
        if cancel_event is not None and cancel_event.is_set():
            raise FetchCancelledError("抓取已取消")
        try:
            result = _do_fetch_file(
                url, mode, compress, timeout,
                respect_robots=respect_robots,
                cancel_event=cancel_event,
                accept_html=accept_html,
            )
        except FetchCancelledError:
            raise
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

    return _finalize_fetch_result(result, query, max_tokens, start_index, max_length)


def _compress_html_text(html: str) -> str | None:
    """把原始 HTML 压缩为 Markdown；失败返回 None，调用方回退原始内容。"""
    if not isinstance(html, str) or not html.strip():
        return None
    in_path = out_path = None
    try:
        from smart3w_core import compress_html

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8", errors="replace"
        ) as handle:
            handle.write(html)
            in_path = handle.name
        fd, out_path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        if compress_html(in_path, out_path):
            return Path(out_path).read_text(encoding="utf-8", errors="replace")
        return None
    except Exception:
        return None
    finally:
        if in_path:
            Path(in_path).unlink(missing_ok=True)
        if out_path:
            Path(out_path).unlink(missing_ok=True)


def _origin_of(url: str) -> str:
    """返回 URL 的 scheme://netloc；解析失败返回空字符串。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


# 异步任务注册表：仅保存在当前 MCP 进程内，重启后丢失。
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOB_MAX_KEEP = 50


class JobLimitError(RuntimeError):
    """后台任务数量达到上限；调用方应提示稍后重试。"""


def _job_view(job: dict) -> dict:
    """返回可序列化的任务快照，不暴露内部锁/线程对象。"""
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "cancel_requested": bool(job.get("cancel_requested")),
        "result": job.get("result"),
        "error": job.get("error"),
    }


def _new_job() -> dict:
    """创建并登记一个后台任务。"""
    job = {
        "job_id": uuid.uuid4().hex,
        "status": "queued",
        "created_at": time.time(),
        "cancel_requested": False,
        "cancel_event": threading.Event(),
    }
    with JOBS_LOCK:
        # 控制内存：先按登记顺序淘汰已完成任务，再判断是否仍达到上限
        finished = [
            key for key, value in JOBS.items()
            if value.get("status") in ("completed", "failed", "cancelled")
        ]
        while len(JOBS) >= JOB_MAX_KEEP and finished:
            JOBS.pop(finished.pop(0), None)
        if len(JOBS) >= JOB_MAX_KEEP:
            raise JobLimitError(f"后台任务数量已达上限 {JOB_MAX_KEEP}，请稍后重试")
        JOBS[job["job_id"]] = job
    return job


def _run_job(job: dict, target, *args, **kwargs) -> None:
    """后台线程入口：执行目标函数并记录结果/异常。"""
    with JOBS_LOCK:
        job["status"] = "running"
        job["started_at"] = time.time()
    try:
        result = target(*args, **kwargs)
        with JOBS_LOCK:
            job["status"] = "cancelled" if job.get("cancel_requested") else "completed"
            job["result"] = result
            job["finished_at"] = time.time()
    except FetchCancelledError as exc:
        with JOBS_LOCK:
            job["status"] = "cancelled"
            job["error"] = str(exc)
            job["finished_at"] = time.time()
    except BaseException as exc:  # noqa: BLE001 - 后台线程必须记录所有异常并落终态
        with JOBS_LOCK:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = time.time()


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


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
        if not isinstance(url, str) or _validate_http_url(url):
            row.update({
                "content": "",
                "content_truncated": False,
                "read_error": "搜索结果 URL 非法或不在 allowlist",
                "read_error_code": "INVALID_ARGUMENT",
            })
            failed += 1
            enriched.append(row)
            continue
        fetched = _run_fetch_file(
            url, "get", True, 20, content_max_chars, 0,
            use_cache=True, respect_robots=None,
        )
        if fetched.get("success"):
            row.update({
                "content": fetched.get("content", ""),
                "content_truncated": bool(fetched.get("truncated")),
                "content_total_length": fetched.get("total_length", 0),
            })
            if fetched.get("effective_url"):
                row["effective_url"] = fetched["effective_url"]
            succeeded += 1
        else:
            row.update({
                "content": "",
                "content_truncated": False,
                "read_error": fetched.get("error") or "正文抓取失败",
                "read_error_code": fetched.get("error_code", "FETCH_FAILED"),
            })
            failed += 1
        enriched.append(row)
    out = dict(data)
    out["results"] = enriched
    out["read_top"] = read_top
    out["read_succeeded"] = succeeded
    out["read_failed"] = failed
    return out


@mcp.tool()
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
    if not _is_int(read_top) or read_top < 0 or read_top > 5:
        return {
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "read_top 必须是 0-5 的整数", "error_code": "INVALID_ARGUMENT",
        }
    if not _is_int(content_max_chars) or content_max_chars < 0 or content_max_chars > 200000:
        return {
            "success": False, "query": query, "results": [], "result_count": 0,
            "error": "content_max_chars 必须是 0-200000 的整数（0 表示不截断）",
            "error_code": "INVALID_ARGUMENT",
        }

    cache_key = (query, count, language, time_range, categories, safesearch, engines)
    cached = SEARCH_CACHE.get(cache_key)
    if cached is not None:
        data = {**cached, "cached": True}
        return _attach_search_content(data, read_top, content_max_chars) if read_top else data

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
            "error": "搜索服务返回了非对象 JSON", "error_code": "SEARCH_FAILED",
        }
    if data.get("success"):
        SEARCH_CACHE.put(cache_key, data)
    return _attach_search_content(data, read_top, content_max_chars) if read_top else data


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
    if mode not in FETCH_MODES:
        error = f"无效抓取模式: {mode}。可选: {', '.join(FETCH_MODES)}"
    elif not _is_int(timeout) or timeout < 1 or timeout > 120:
        error = "timeout 必须是 1-120 的整数（秒）"
    elif not _is_int(max_length) or max_length < 0 or max_length > 200000:
        error = "max_length 必须是 0-200000 的整数（0 表示不截断）"
    elif not _is_int(start_index) or start_index < 0:
        error = "start_index 必须是非负整数"
    elif not _is_int(max_age_seconds) or max_age_seconds < -1 or max_age_seconds > 604800:
        error = "max_age_seconds 必须是 -1 或 0-604800 的整数"
    elif not _is_int(max_tokens) or max_tokens < 0 or max_tokens > 200000:
        error = "max_tokens 必须是 0-200000 的整数"
    elif (bool_error := _bool_error(compress, "compress")):
        error = bool_error
    elif (bool_error := _bool_error(use_cache, "use_cache")):
        error = bool_error
    elif (bool_error := _optional_bool_error(respect_robots, "respect_robots")):
        error = bool_error
    elif len(query) > 500:
        error = "query 长度不能超过 500 字符"
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
        max_age_seconds=max_age_seconds, query=query, max_tokens=max_tokens,
    )


@mcp.tool()
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
        dict: {success, url, count, limit, urls: [{url, source}], sources?,
               sitemap_errors?, truncated?, error?}
    """
    seed = url.strip() if isinstance(url, str) else ""
    url_error = _validate_http_url(seed)
    if url_error:
        return {"success": False, "url": seed, "count": 0, "urls": [],
                "error": url_error, "error_code": "INVALID_ARGUMENT"}
    if not _is_int(limit) or limit < 1 or limit > 5000:
        return {"success": False, "url": seed, "count": 0, "urls": [],
                "error": "limit 必须是 1-5000 的整数", "error_code": "INVALID_ARGUMENT"}
    search = search.strip() if isinstance(search, str) else ""
    for name, value in (
        ("include_sitemap", include_sitemap),
        ("include_homepage", include_homepage),
        ("same_domain", same_domain),
        ("respect_robots", respect_robots),
    ):
        bool_error = _bool_error(value, name)
        if bool_error:
            return {"success": False, "url": seed, "count": 0, "urls": [],
                    "error": bool_error, "error_code": "INVALID_ARGUMENT"}
    if not include_sitemap and not include_homepage and not search:
        return {"success": False, "url": seed, "count": 0, "urls": [],
                "error": "至少需要启用 include_sitemap、include_homepage 或提供 search",
                "error_code": "INVALID_ARGUMENT"}
    origin = _origin_of(seed)
    if not origin:
        return {"success": False, "url": seed, "count": 0, "urls": [],
                "error": "无法解析站点 origin", "error_code": "INVALID_ARGUMENT"}

    urls: list[dict[str, str]] = []
    seen: set[str] = set()
    source_counts: dict[str, int] = {}
    sitemap_errors: list[str] = []
    policy_cache: dict[tuple[str, str], str | None] = {}
    truncated = False

    def _policy_error(candidate: str) -> str | None:
        """按 (scheme, netloc) 缓存 DNS/SSRF 结果，但每个 URL 都先做形状校验。

        不能只按 hostname 缓存：同一域名下不同端口、userinfo 或 scheme
        的候选 URL 可能违反策略，复用一个 None 会绕过逐 URL 校验；
        长度和控制字符校验也必须逐 URL 执行，不能随 origin 缓存。
        """
        shape_error = url_shape_error(candidate)
        if shape_error:
            return shape_error
        parsed = urlsplit(candidate)
        key = (parsed.scheme.lower(), parsed.netloc.lower())
        if key not in policy_cache:
            policy_cache[key] = ssrf_blocked(candidate)
        return policy_cache[key]

    def _add(candidate: str, source: str) -> None:
        nonlocal truncated
        if not isinstance(candidate, str) or not candidate.strip():
            return
        candidate = candidate.strip()
        # 先做廉价的同站/路径过滤，避免为必然丢弃的链接做 DNS/SSRF 查询
        if same_domain and not same_site(candidate, seed):
            return
        try:
            path = urlsplit(candidate).path or "/"
        except ValueError:
            return
        if not path_allowed(path, path_include, path_exclude):
            return
        if _policy_error(candidate):
            return
        key = normalize_url(candidate)
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
        robots = _run_fetch_file(
            robots_url, "get", False, 10, 0, 0,
            use_cache=True, respect_robots=False,
        )
        if robots.get("success"):
            sitemap_roots.extend(parse_robots_sitemaps(robots.get("content", ""), origin))
        default_sitemap = origin.rstrip("/") + "/sitemap.xml"
        if normalize_url(default_sitemap) not in {normalize_url(item) for item in sitemap_roots}:
            sitemap_roots.append(default_sitemap)
        for sitemap_url in sitemap_roots[:10]:
            if _policy_error(sitemap_url):
                continue
            try:
                entries, errors, _truncated, _fetched = collect_sitemap_entries(
                    sitemap_url, max_urls=max(limit, 50), recursive=True
                )
            except SitemapError as exc:
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
        # 站点根 URL 本身始终计入，避免没有 sitemap/站内链接的站点返回空结果
        _add(seed, "homepage")
        fetched = _run_fetch_file(
            seed, "get", False, 20, 0, 0,
            use_cache=True, respect_robots=respect_robots,
        )
        if fetched.get("success"):
            base = fetched.get("effective_url") or seed
            for link in extract_links(
                fetched.get("content", ""), base,
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
            search_data = search_web(search, min(20, max(1, limit)), SEARXNG_INSTANCE)
        except Exception as exc:
            search_data = {"success": False, "error": str(exc)}
        if search_data.get("success"):
            for row in search_data.get("results") or []:
                if isinstance(row, dict) and isinstance(row.get("url"), str):
                    _add(row["url"], "search")
        else:
            sitemap_errors.append(f"search: {search_data.get('error') or '搜索失败'}")

    out: dict[str, object] = {
        "success": bool(urls) or not (sitemap_errors or homepage_error),
        "url": seed,
        "count": len(urls),
        "limit": limit,
        "urls": urls,
        "sources": source_counts,
    }
    if sitemap_errors:
        out["sitemap_errors"] = sitemap_errors[:20]
    if homepage_error:
        out["homepage_error"] = homepage_error
    if truncated:
        out["truncated"] = True
    if not urls and (sitemap_errors or homepage_error):
        out["error"] = "没有发现可用 URL"
        out["error_code"] = "MAP_FAILED"
    return out


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

    queue: list[tuple[str, int]] = [(seed_url, 0)]
    queued: set[str] = {normalize_url(seed_url)}
    visited: set[str] = set()
    results: list[dict] = []
    invalid: list[str] = []
    while queue and len(results) < max_pages:
        if _cancelled():
            break
        url, depth = queue.pop(0)
        key = normalize_url(url)
        if key in visited:
            continue
        if _validate_http_url(url):
            # 非法 URL 不进入 visited 统计，避免 unique 与 succeeded+failed 不一致
            if url not in invalid:
                invalid.append(url)
            continue
        visited.add(key)
        try:
            raw = _run_fetch_file(
                url, mode, False, timeout, max_length=0, start_index=0,
                use_cache=True, respect_robots=respect_robots,
                cancel_event=cancel_event,
            )
        except FetchCancelledError:
            break
        if _cancelled():
            break
        if not raw.get("success"):
            item = dict(raw)
            item["depth"] = depth
            results.append(apply_window(item, 0, max_length))
            continue
        html = raw.get("content", "")
        content = html
        if compress and not raw.get("format"):
            if _cancelled():
                break
            content = _compress_html_text(html) or html
        item = dict(raw)
        item["content"] = content
        item["depth"] = depth
        item["crawled_url"] = url
        results.append(apply_window(item, 0, max_length))

        if depth >= max_depth or raw.get("format"):
            continue
        base = raw.get("effective_url") or url
        for link in extract_links(
            html, base,
            same_domain=same_domain,
            path_include=path_include,
            path_exclude=path_exclude,
            max_links=max(max_pages * 5, 100),
        ):
            link_key = normalize_url(link["url"])
            if link_key and link_key not in visited and link_key not in queued:
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
        "truncated": bool(queue),
        "results": results,
    }
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


@mcp.tool()
def smart3w_crawl(
    urls: list[str] | None = None,
    mode: str = "get",
    compress: bool = True,
    timeout: int = 20,
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
    url_list = list(urls) if isinstance(urls, (list, tuple)) else []
    seed = seed_url.strip() if isinstance(seed_url, str) else ""
    if not _is_int(max_depth) or max_depth < 0 or max_depth > 5:
        return {"success": False, "error": "max_depth 必须是 0-5 的整数",
                "error_code": "INVALID_ARGUMENT"}
    if not _is_int(max_pages) or max_pages < 1 or max_pages > 500:
        return {"success": False, "error": "max_pages 必须是 1-500 的整数",
                "error_code": "INVALID_ARGUMENT"}
    if not seed and max_depth > 0:
        if len(url_list) == 1:
            seed = url_list[0].strip() if isinstance(url_list[0], str) else ""
            url_list = []
        elif url_list:
            return {"success": False, "error": "递归抓取请提供 seed_url，或只传一个 URL",
                    "error_code": "INVALID_ARGUMENT"}

    if mode not in FETCH_MODES:
        return {"success": False, "error": f"无效抓取模式: {mode}",
                "error_code": "INVALID_ARGUMENT"}
    if not _is_int(timeout) or timeout < 1 or timeout > 120:
        return {"success": False, "error": "timeout 必须是 1-120 的整数（秒）",
                "error_code": "INVALID_ARGUMENT"}
    if not _is_int(max_length) or max_length < 0 or max_length > 200000:
        return {"success": False, "error": "max_length 必须是 0-200000 的整数",
                "error_code": "INVALID_ARGUMENT"}
    if not _is_int(concurrency) or concurrency < 1 or concurrency > 8:
        return {"success": False, "error": "concurrency 必须是 1-8 的整数",
                "error_code": "INVALID_ARGUMENT"}
    for name, value in (
        ("compress", compress),
        ("respect_robots", respect_robots),
        ("same_domain", same_domain),
        ("run_async", run_async),
    ):
        bool_error = _bool_error(value, name)
        if bool_error:
            return {"success": False, "error": bool_error, "error_code": "INVALID_ARGUMENT"}

    if seed:
        if url_list:
            return {"success": False, "error": "seed_url 与 urls 不能同时使用",
                    "error_code": "INVALID_ARGUMENT"}
        seed_error = _validate_http_url(seed)
        if seed_error:
            return {"success": False, "seed_url": seed, "error": seed_error,
                    "error_code": "INVALID_ARGUMENT"}
        depth = 1 if max_depth == 0 else max_depth
        if run_async:
            try:
                job = _new_job()
            except JobLimitError as exc:
                return {"success": False, "error": str(exc), "error_code": "JOB_LIMIT"}
            thread = threading.Thread(
                target=_run_job,
                args=(
                    job, _crawl_site, seed, mode, compress, timeout, max_length,
                    respect_robots, depth, max_pages, same_domain, path_include, path_exclude,
                    job,
                ),
                daemon=True,
                name=f"smart3w-crawl-{job['job_id'][:8]}",
            )
            try:
                thread.start()
            except Exception as exc:
                with JOBS_LOCK:
                    job["status"] = "failed"
                    job["error"] = f"后台任务启动失败: {exc}"
                    job["finished_at"] = time.time()
                return {"success": False, "job_id": job["job_id"],
                        "error": f"后台任务启动失败: {exc}",
                        "error_code": "INTERNAL_ERROR"}
            return {
                "success": True, "job_id": job["job_id"], "status": "queued",
                "seed_url": seed, "message": "任务已提交，请用 smart3w_job 查询进度和结果",
            }
        return _crawl_site(
            seed, mode, compress, timeout, max_length, respect_robots,
            depth, max_pages, same_domain, path_include, path_exclude,
        )

    if not url_list or len(url_list) > 100:
        return {"success": False, "error": "urls 必须是 1-100 个 URL，或提供 seed_url",
                "error_code": "INVALID_ARGUMENT"}

    invalid: list[str] = []
    valid: list[str] = []
    unique: list[str] = []
    seen: set[str] = set()
    for raw_url in url_list:
        if not isinstance(raw_url, str):
            invalid.append(repr(raw_url))
            continue
        url = raw_url.strip()
        if _validate_http_url(url):
            invalid.append(raw_url)
            continue
        valid.append(url)
        key = normalize_url(url)
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
            **base, "success": False, "succeeded": 0, "failed": 0, "results": [],
            "error": "没有可抓取的合法 URL", "error_code": "INVALID_ARGUMENT",
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
        out["error_code"] = "CRAWL_FAILED"
    return out


@mcp.tool()
def smart3w_extract(
    urls: list[str],
    schema: dict[str, object],
    mode: str = "css",
    timeout: int = 20,
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
    if not _is_int(timeout) or timeout < 1 or timeout > 120:
        return {"success": False, "error": "timeout 必须是 1-120 的整数（秒）", "error_code": "INVALID_ARGUMENT"}
    if not _is_int(concurrency) or concurrency < 1 or concurrency > 8:
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
        if _validate_http_url(url):
            invalid.append(raw_url)
            continue
        valid.append(url)
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)
    if not unique:
        return {
            "success": False, "requested": len(urls), "unique": 0,
            "invalid_count": len(invalid), "invalid": invalid,
            "succeeded": 0, "failed": 0, "results": [],
            "error": "没有可抽取的合法 URL", "error_code": "INVALID_ARGUMENT",
        }

    def _extract_one(url: str) -> dict:
        try:
            raw = _run_fetch_file(
                url, "get", False, timeout, max_length=0, start_index=0,
                use_cache=True, respect_robots=respect_robots,
            )
        except FetchError as exc:
            return {"url": url, "success": False, "error": str(exc),
                    "error_code": exc.error_code}
        except Exception as exc:
            return {"url": url, "success": False, "error": str(exc),
                    "error_code": "INTERNAL_ERROR"}
        if not raw.get("success"):
            return {
                "url": url, "success": False,
                "error": raw.get("error") or "抓取失败",
                "error_code": raw.get("error_code", "FETCH_FAILED"),
            }
        if raw.get("format"):
            return {
                "url": url, "success": False,
                "error": f"文档格式 {raw.get('format')} 不支持 CSS/XPath 抽取",
                "error_code": "UNSUPPORTED_FORMAT",
            }
        try:
            payload = extract_fields(raw.get("content", ""), schema, mode)
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
                    "url": unique[idx], "success": False,
                    "error": str(exc), "error_code": "INTERNAL_ERROR",
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


@mcp.tool()
def smart3w_job(job_id: str = "", cancel: bool = False) -> dict[str, object]:
    """Query or cancel a background crawl job created with smart3w_crawl(run_async=true).

    Args:
        job_id: 任务 id；留空则列出最近任务
        cancel: 是否请求取消（仅 queued/running 任务有效）

    Returns:
        dict: {success, job_id?, status?, result?, error?} 或 {success, jobs, count}
    """
    if not isinstance(job_id, str):
        return {"success": False, "error": "job_id 必须是字符串",
                "error_code": "INVALID_ARGUMENT"}
    if not isinstance(cancel, bool):
        return {"success": False, "error": "cancel 必须是布尔值", "error_code": "INVALID_ARGUMENT"}
    job_id = job_id.strip()
    if not job_id:
        with JOBS_LOCK:
            jobs = sorted(JOBS.values(), key=lambda item: item.get("created_at", 0), reverse=True)
            summaries = []
            for job in jobs[:20]:
                view = _job_view(job)
                view.pop("result", None)
                summaries.append(view)
        return {"success": True, "jobs": summaries, "count": len(summaries)}

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return {"success": False, "job_id": job_id,
                    "error": "任务不存在（后台任务仅保存在当前 MCP 进程内）",
                    "error_code": "JOB_NOT_FOUND"}
        if cancel and job.get("status") in ("queued", "running"):
            job["cancel_requested"] = True
            event = job.get("cancel_event")
            if event is not None:
                event.set()
        return {"success": True, **_job_view(job)}


@mcp.tool()
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
        return {"success": False, "url": str(url), "error": "url 必须是字符串",
                "error_code": "INVALID_ARGUMENT"}
    url = url.strip()
    # URL 策略包含 DNS 解析，放到线程里避免阻塞事件循环
    url_error = await asyncio.to_thread(_validate_http_url, url)
    if url_error:
        return {"success": False, "url": url, "error": url_error,
                "error_code": "INVALID_ARGUMENT"}
    if not _is_int(timeout) or timeout < 1 or timeout > 120:
        return {"success": False, "url": url, "error": "timeout 必须是 1-120 的整数",
                "error_code": "INVALID_ARGUMENT"}
    if not _is_int(wait_ms) or wait_ms < 0 or wait_ms > 10000:
        return {"success": False, "url": url, "error": "wait_ms 必须是 0-10000 的整数",
                "error_code": "INVALID_ARGUMENT"}
    if not _is_int(max_content) or max_content < 0 or max_content > 1000000:
        return {"success": False, "url": url, "error": "max_content 必须是 0-1000000 的整数",
                "error_code": "INVALID_ARGUMENT"}
    if content_format not in {"markdown", "html", "text"}:
        return {"success": False, "url": url, "error": "content_format 必须是 markdown/html/text",
                "error_code": "INVALID_ARGUMENT"}
    for name, value in (("return_cookies", return_cookies), ("respect_robots", respect_robots)):
        bool_error = _bool_error(value, name)
        if bool_error:
            return {"success": False, "url": url, "error": bool_error,
                    "error_code": "INVALID_ARGUMENT"}
    try:
        normalized_actions = normalize_actions(actions or [])
        profile = validate_profile(profile)
        normalized_cookies = normalize_cookies(cookies or [])
    except InteractionError as exc:
        return {"success": False, "url": url, "error": str(exc), "error_code": exc.code}
    if respect_robots and not await asyncio.to_thread(ROBOTS_CHECKER.allows, url):
        return {"success": False, "url": url, "error": "robots.txt 禁止抓取该 URL",
                "error_code": "ROBOTS_BLOCKED"}
    try:
        result = await asyncio.to_thread(
            run_interaction,
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
    except InteractionError as exc:
        return {"success": False, "url": url, "error": str(exc), "error_code": exc.code}
    except Exception as exc:
        return {"success": False, "url": url, "error": f"浏览器交互失败: {exc}",
                "error_code": "INTERACT_FAILED"}
    # run_interaction 正常返回视为成功；若内部已标记失败则保留其结论
    result.setdefault("success", True)
    return result


@mcp.tool()
def smart3w_sitemap(url: str, max_urls: int = 50, recursive: bool = False) -> dict[str, object]:
    """Parse a sitemap (supports index and URL-set formats). Returns discovered URLs.

    Args:
        url: Sitemap URL
        max_urls: 最多返回/展开的 URL 数（1-10000）
        recursive: 是否递归展开 Sitemap Index（最多 2 层）

    Returns:
        dict: {success, output}（output 为格式化文本）或 {success: False, error, error_code}
    """
    if not _is_int(max_urls) or max_urls < 1 or max_urls > 10000:
        return {"success": False, "error": "max_urls 必须是 1-10000 的整数",
                "error_code": "INVALID_ARGUMENT"}
    if not isinstance(recursive, bool):
        return {"success": False, "error": "recursive 必须是布尔值", "error_code": "INVALID_ARGUMENT"}
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
        dict: {success, output, rust?} 或 {success: False, error, error_code}
    """
    if not isinstance(check_search, bool):
        return {"success": False, "error": "check_search 必须是布尔值",
                "error_code": "INVALID_ARGUMENT"}
    args = ["doctor"]
    if check_search:
        args.append("--check-search")
    try:
        output = _run_fetch(args)
    except FetchError as exc:
        return {"success": False, "error": str(exc), "error_code": exc.error_code}
    result: dict[str, object] = {"success": True, "output": output.strip()}
    try:
        import smart3w_rust

        result["rust"] = smart3w_rust.doctor()
    except Exception as exc:  # pragma: no cover - 诊断信息不应导致工具失败
        result["rust"] = {"available": False, "error": str(exc)}
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="smart3w-mcp", description="Smart3W MCP Server")
    transport = (os.getenv("MCP_TRANSPORT") or "streamable-http").strip().lower()
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=transport,
    )
    parser.add_argument("--version", action="version", version=f"smart3w {__version__}")
    args = parser.parse_args(argv)
    if args.transport not in ("stdio", "streamable-http"):
        parser.error(f"无效的 MCP_TRANSPORT: {args.transport}（可选 stdio/streamable-http）")
    if args.transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
            streamable_http_path=(os.getenv("MCP_PATH") or "/mcp").strip() or "/mcp",
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
