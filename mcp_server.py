#!/usr/bin/env python3
"""Smart3W MCP Server - 将 smart3w 搜索与网页抓取能力暴露为 MCP 工具。"""

import argparse
import json
import logging
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
from pathlib import Path
from typing import Any, cast

from mcp.server import MCPServer

from smart3w_content import filter_content
from smart3w_core import (  # noqa: F401 - 供 smart3w_mcp_tools 通过 srv 动态访问
    DEFAULT_INSTANCE,
    FETCH_MODES,
    SEARCH_TIME_RANGES,
    SitemapError,
    collect_sitemap_entries,
    parse_sitemap,
    search_web,
)
from smart3w_discovery import (  # noqa: F401 - 供 smart3w_mcp_tools 通过 srv 动态访问
    extract_fields,
    extract_links,
    parse_robots_sitemaps,
    path_allowed,
    same_site,
)
from smart3w_fetch_utils import (
    DEFAULT_CACHE_TTL,
    DEFAULT_RESPECT_ROBOTS,
    DomainRateLimiter,
    RobotsChecker,
    TTLCache,
    apply_window,
    is_rate_limited,  # noqa: F401 - 供 smart3w_mcp_tools 通过 srv 动态访问
)
from smart3w_interact import (  # noqa: F401 - 供 smart3w_mcp_tools 通过 srv 动态访问
    InteractionError,
    normalize_actions,
    normalize_cookies,
    run_interaction,
    validate_profile,
)
from smart3w_url_utils import (  # noqa: F401 - 供 smart3w_mcp_tools 通过 srv 动态访问
    normalize_url,
    ssrf_blocked,
    url_policy_error,
    url_shape_error,
)
from smart3w_version import __version__

# 版本号唯一来源：smart3w_version.__version__。

# 启动日志只打印一次；重复调用 main() / 多次初始化不会重复刷屏。
_STARTUP_LOGGED = False
_startup_logger = logging.getLogger("smart3w")


def _build_info() -> dict[str, str]:
    """返回启动日志使用的构建信息；缺失时降级为 unknown。"""
    return {
        "version": __version__,
        "commit": (os.getenv("SMART3W_BUILD_COMMIT") or "").strip() or "unknown",
        "build_time": (os.getenv("SMART3W_BUILD_TIME") or "").strip() or "unknown",
    }


def _startup_message(transport: str, host: str, port: int, path: str) -> str:
    info = _build_info()
    endpoint = f"http://{host}:{port}{path}" if transport == "streamable-http" else "stdio"
    python = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return (
        f"starting smart3w-mcp version={info['version']} "
        f"commit={info['commit']} build_time={info['build_time']} "
        f"transport={transport} endpoint={endpoint} python={python}"
    )


def _log_startup(transport: str, host: str, port: int, path: str) -> None:
    """在服务启动前打印一行 INFO 日志；未配置构建信息时输出 unknown。"""
    global _STARTUP_LOGGED
    if _STARTUP_LOGGED:
        return
    if not _startup_logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        _startup_logger.addHandler(handler)
    _startup_logger.setLevel(logging.INFO)
    _startup_logger.propagate = False
    _startup_logger.info(_startup_message(transport, host, port, path))
    _STARTUP_LOGGED = True


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
# 单次抓取的墙钟上限：即使 smart 模式内部按策略放大，也不能超过 MCP 客户端超时
MAX_FETCH_SECONDS = max(10, _env_int("SMART3W_MAX_FETCH_SECONDS", 110))
# 子进程 stdout/stderr 保留上限，避免异常页面把日志无限读入内存
MAX_SUBPROCESS_OUTPUT_BYTES = max(65536, _env_int("SMART3W_MAX_SUBPROCESS_OUTPUT_BYTES", 4 * 1024 * 1024))

# 抓取治理：URL 去重缓存 / robots / 按域名限速
# max_age_seconds 允许到 7 天，缓存本身必须能存活 7 天，否则文档承诺失效
FETCH_CACHE = TTLCache(ttl=max(DEFAULT_CACHE_TTL, 604800))
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
    # 乘以尝试次数与策略数后，再留固定余量；最后收口到整体墙钟上限。
    budget = (timeout + 15) * FETCH_RETRY * strategies + 15
    return max(10, min(MAX_FETCH_SECONDS, budget))


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
            _killpg = getattr(os, "killpg", None)
            _getpgid = getattr(os, "getpgid", None)
            if _killpg is not None and _getpgid is not None:
                _killpg(_getpgid(proc.pid), signal.SIGTERM)
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
            _killpg = getattr(os, "killpg", None)
            _getpgid = getattr(os, "getpgid", None)
            if _killpg is not None and _getpgid is not None:
                _killpg(_getpgid(proc.pid), getattr(signal, "SIGKILL", signal.SIGTERM))
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


def _read_stream_capped(handle) -> str:
    """读取子进程输出文件，超过上限时截断，避免异常输出撑爆内存。"""
    handle.seek(0)
    data = handle.read(MAX_SUBPROCESS_OUTPUT_BYTES + 1)
    text = data.decode("utf-8", errors="replace")
    if len(data) > MAX_SUBPROCESS_OUTPUT_BYTES:
        text = text[:MAX_SUBPROCESS_OUTPUT_BYTES] + "\n...[output truncated]"
    return text


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
    stdout/stderr 写入临时文件并限量读取，避免子进程输出无上限占用内存。
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

    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    stdout_handle = tempfile.TemporaryFile()
    stderr_handle = tempfile.TemporaryFile()
    proc: subprocess.Popen | None = None
    try:
        try:
            proc = subprocess.Popen(
                command,
                stdout=stdout_handle,
                stderr=stderr_handle,
                env=env,
                **popen_kwargs,
            )
        except OSError as exc:
            raise FetchUnavailableError(f"无法执行 fetch.sh（需要 bash）: {exc}") from exc

        deadline = time.monotonic() + timeout
        while True:
            if cancel_event.is_set():
                _terminate_process_tree(proc)
                raise FetchCancelledError("抓取已取消")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(proc)
                raise FetchTimeoutError(f"fetch.sh 执行超时（>{timeout}s）")

            try:
                proc.wait(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        stdout = _read_stream_capped(stdout_handle)
        stderr = _read_stream_capped(stderr_handle)
        if proc.returncode != 0:
            detail = (stderr or "").strip() or (stdout or "").strip() or f"Exit code {proc.returncode}"
            raise FetchError(detail)
        return stdout or ""
    finally:
        if proc is not None and proc.poll() is None:
            _terminate_process_tree(proc)
        stdout_handle.close()
        stderr_handle.close()


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

        run_kwargs: dict[str, Any] = {"respect_robots": respect_robots}
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
            cached = cast(dict[str, Any], cached)
            # -1 表示按全局 TTL（SMART3W_CACHE_TTL，默认 300s）判定新鲜度；
            # 缓存本身保留 7 天，只用于支持显式 max_age_seconds 的大值。
            effective_max_age = DEFAULT_CACHE_TTL if max_age_seconds < 0 else max_age_seconds
            if age <= effective_max_age:
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
        if result.get("success") and len(str(result.get("content") or "")) <= MAX_CACHE_CONTENT_BYTES:
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


# 异步任务注册表：内存 + JSON 落盘；重启后未完成的任务会标记为失败。
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
JOB_MAX_KEEP = 50
JOB_STATE_PATH = Path(
    os.getenv("SMART3W_JOB_STATE") or (Path.home() / ".smart3w" / "jobs.json")
).expanduser()
_jobs_logger = logging.getLogger("smart3w")


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


def _persist_jobs_locked() -> None:
    """把当前任务表原子写入 JOB_STATE_PATH；调用方必须已持有 JOBS_LOCK。

    写盘失败只记警告，不影响任务执行（任务结果仍可在内存查询）。
    cancel_event 等运行期对象不会写入文件。
    """
    try:
        payload = {"version": 1, "jobs": [_job_view(job) for job in JOBS.values()]}
        JOB_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(JOB_STATE_PATH.parent), prefix=".jobs-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, JOB_STATE_PATH)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
    except Exception as exc:  # noqa: BLE001 - 持久化失败不能影响主流程
        _jobs_logger.warning("任务状态持久化失败 path=%s error=%s", JOB_STATE_PATH, exc)


def _load_jobs() -> None:
    """启动时加载任务快照；queued/running 视为已中断并标记 failed。"""
    if not JOB_STATE_PATH.exists():
        return
    try:
        raw = json.loads(JOB_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 损坏的状态文件不应阻止服务启动
        _jobs_logger.warning("任务状态文件读取失败 path=%s error=%s", JOB_STATE_PATH, exc)
        return
    items = raw.get("jobs") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return
    loaded: list[dict] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("job_id"), str):
            continue
        status = item.get("status")
        if status not in ("queued", "running", "completed", "failed", "cancelled"):
            continue
        job = {
            "job_id": item["job_id"],
            "status": status,
            "created_at": item.get("created_at") or time.time(),
            "started_at": item.get("started_at"),
            "finished_at": item.get("finished_at"),
            "cancel_requested": bool(item.get("cancel_requested")),
            "result": item.get("result"),
            "error": item.get("error"),
            "cancel_event": threading.Event(),
        }
        if status in ("queued", "running"):
            job["status"] = "failed"
            job["error"] = "服务重启，任务已中断，请重新提交"
            job["finished_at"] = time.time()
        loaded.append(job)
    loaded.sort(key=lambda value: value.get("created_at") or 0)
    with JOBS_LOCK:
        for job in loaded[-JOB_MAX_KEEP:]:
            JOBS[job["job_id"]] = job
        _persist_jobs_locked()


def _new_job() -> dict[str, Any]:
    """创建并登记一个后台任务。"""
    job: dict[str, Any] = {
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
        _persist_jobs_locked()
    return job


def _run_job(job: dict, target, *args, **kwargs) -> None:
    """后台线程入口：执行目标函数并记录结果/异常。"""
    with JOBS_LOCK:
        job["status"] = "running"
        job["started_at"] = time.time()
        _persist_jobs_locked()
    try:
        result = target(*args, **kwargs)
        with JOBS_LOCK:
            job["status"] = "cancelled" if job.get("cancel_requested") else "completed"
            job["result"] = result
            job["finished_at"] = time.time()
            _persist_jobs_locked()
    except FetchCancelledError as exc:
        with JOBS_LOCK:
            job["status"] = "cancelled"
            job["error"] = str(exc)
            job["finished_at"] = time.time()
            _persist_jobs_locked()
    except BaseException as exc:  # noqa: BLE001 - 后台线程必须记录所有异常并落终态
        with JOBS_LOCK:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = time.time()
            _persist_jobs_locked()


_load_jobs()


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# MCP Tools（实现已拆分到 smart3w_mcp_tools 包；此处注册并 re-export 以保持兼容）
# ---------------------------------------------------------------------------
from smart3w_mcp_tools import (  # noqa: E402
    smart3w_crawl,
    smart3w_doctor,
    smart3w_extract,
    smart3w_fetch,
    smart3w_interact,
    smart3w_job,
    smart3w_map,
    smart3w_search,
    smart3w_sitemap,
)
from smart3w_mcp_tools.crawl import _crawl_site as _crawl_site  # noqa: E402
from smart3w_mcp_tools.map import _origin_of as _origin_of  # noqa: E402
from smart3w_mcp_tools.search import _attach_search_content as _attach_search_content  # noqa: E402

for _tool in (
    smart3w_search,
    smart3w_fetch,
    smart3w_map,
    smart3w_crawl,
    smart3w_extract,
    smart3w_job,
    smart3w_interact,
    smart3w_sitemap,
    smart3w_doctor,
):
    mcp.tool()(_tool)


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
    path = (os.getenv("MCP_PATH") or "/mcp").strip() or "/mcp"
    _log_startup(args.transport, DEFAULT_HOST, DEFAULT_PORT, path)
    if args.transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
            streamable_http_path=path,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
