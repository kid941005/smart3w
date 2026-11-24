"""mcp_server 层测试：结构化返回、搜索缓存与 SSRF 拦截（无真实网络依赖）。"""

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server  # noqa: E402


def test_cli_version_works_when_run_as_script():
    """回归：直接 python mcp_server.py 启动时工具包不能再触发循环导入。"""
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(root / "mcp_server.py"), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "smart3w" in proc.stdout


def test_server_uses_mcpserver_api():
    from mcp.server import MCPServer

    assert isinstance(mcp_server.mcp, MCPServer)


def test_main_passes_streamable_http_options(monkeypatch):
    captured = {}
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setenv("MCP_PATH", "/custom-mcp")

    mcp_server.main(["--transport", "streamable-http"])

    assert captured == {
        "transport": "streamable-http",
        "host": mcp_server.DEFAULT_HOST,
        "port": mcp_server.DEFAULT_PORT,
        "streamable_http_path": "/custom-mcp",
    }


def test_main_uses_stdio_without_http_options(monkeypatch):
    captured = {}
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **kwargs: captured.update(kwargs))

    mcp_server.main(["--transport", "stdio"])

    assert captured == {"transport": "stdio"}


def test_build_info_defaults_to_unknown(monkeypatch):
    monkeypatch.delenv("SMART3W_BUILD_COMMIT", raising=False)
    monkeypatch.delenv("SMART3W_BUILD_TIME", raising=False)

    info = mcp_server._build_info()

    assert info == {
        "version": mcp_server.__version__,
        "commit": "unknown",
        "build_time": "unknown",
    }


def test_build_info_reads_environment(monkeypatch):
    monkeypatch.setenv("SMART3W_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("SMART3W_BUILD_TIME", "2026-11-23T15:00:00Z")

    info = mcp_server._build_info()

    assert info["version"] == mcp_server.__version__
    assert info["commit"] == "abc1234"
    assert info["build_time"] == "2026-11-23T15:00:00Z"


def test_startup_message_contains_version_and_build_info(monkeypatch):
    monkeypatch.setenv("SMART3W_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("SMART3W_BUILD_TIME", "2026-11-23T15:00:00Z")

    message = mcp_server._startup_message("streamable-http", "0.0.0.0", 50826, "/mcp")

    assert f"version={mcp_server.__version__}" in message
    assert "commit=abc1234" in message
    assert "build_time=2026-11-23T15:00:00Z" in message
    assert "transport=streamable-http" in message
    assert "endpoint=http://0.0.0.0:50826/mcp" in message


def test_startup_log_emits_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_STARTUP_LOGGED", False)
    monkeypatch.setattr(mcp_server._startup_logger, "info", lambda message: calls.append(message))

    mcp_server._log_startup("stdio", "0.0.0.0", 50826, "/mcp")
    mcp_server._log_startup("stdio", "0.0.0.0", 50826, "/mcp")

    assert len(calls) == 1
    assert "version=" in calls[0]


def test_fetch_blocks_private_ip_without_subprocess(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda *a, **k: calls.append(1) or {},
    )
    result = mcp_server.smart3w_fetch("http://127.0.0.1/admin")
    assert result["success"] is False
    assert "内网" in result["error"] or "保留" in result["error"]
    assert calls == []


def test_fetch_returns_dict_on_validation_error():
    result = mcp_server.smart3w_fetch("not a url")
    assert isinstance(result, dict)
    assert result["success"] is False


def test_search_invalid_count_message():
    result = mcp_server.smart3w_search("q", 101)
    assert result["success"] is False
    assert "count" in result["error"]
    assert "query" not in result["error"]


def test_search_rejects_bool_safesearch():
    result = mcp_server.smart3w_search("q", 5, safesearch=True)
    assert result["success"] is False
    assert "safesearch" in result["error"]


def test_search_caches_successful_results(monkeypatch):
    payload = {
        "success": True,
        "query": "q",
        "engine": "searxng",
        "instance": "https://a.example",
        "params": {},
        "results": [{"title": "t", "url": "https://example.com/x", "snippet": "s"}],
        "result_count": 1,
    }
    calls = []
    monkeypatch.setattr(
        mcp_server, "search_web", lambda *args, **kwargs: calls.append(args) or payload
    )

    first = mcp_server.smart3w_search("cache-hit", 5)
    second = mcp_server.smart3w_search("cache-hit", 5)

    assert first["success"] is True
    assert second["cached"] is True
    assert len(calls) == 1


def test_search_calls_core_with_params(monkeypatch):
    captured = {}

    def fake_search(query, count, instance, **kwargs):
        captured.update({"query": query, "count": count, "instance": instance, **kwargs})
        return {"success": True, "results": [], "result_count": 0}

    monkeypatch.setattr(mcp_server, "search_web", fake_search)
    mcp_server.smart3w_search("q", 3, language="en-US", safesearch=1, engines="google")

    assert captured["query"] == "q"
    assert captured["count"] == 3
    assert captured["language"] == "en-US"
    assert captured["safesearch"] == 1
    assert captured["engines"] == "google"


def test_search_does_not_cache_failures(monkeypatch):
    payload = {
        "success": False,
        "query": "q",
        "results": [],
        "result_count": 0,
        "error": "boom",
    }
    calls = []
    monkeypatch.setattr(
        mcp_server, "search_web", lambda *args, **kwargs: calls.append(args) or payload
    )

    mcp_server.smart3w_search("cache-miss", 5)
    mcp_server.smart3w_search("cache-miss", 5)

    assert len(calls) == 2


def test_sitemap_uses_core_and_reports_errors(monkeypatch):
    calls = []

    def fake_sitemap(url, max_urls, recursive=False):
        calls.append((url, max_urls, recursive))
        return "OK"

    monkeypatch.setattr(mcp_server, "parse_sitemap", fake_sitemap)
    result = mcp_server.smart3w_sitemap("https://example.com/sitemap.xml", 5, recursive=True)

    assert result == {"success": True, "output": "OK"}
    assert calls == [("https://example.com/sitemap.xml", 5, True)]


def test_read_effective_url_missing_or_empty(tmp_path):
    p = tmp_path / "x.effective"
    assert mcp_server._read_effective_url(str(p)) is None
    p.write_text("   ", encoding="utf-8")
    assert mcp_server._read_effective_url(str(p)) is None
    p.write_text("\nhttps://example.com/final\n", encoding="utf-8")
    assert mcp_server._read_effective_url(str(p)) == "https://example.com/final"


def test_do_fetch_file_blocks_redirect_to_private(monkeypatch):
    def fake_run_fetch(args, timeout=mcp_server.DEFAULT_TIMEOUT, respect_robots=None):
        Path(args[2]).write_text("内网敏感内容", encoding="utf-8")
        eff = args[args.index("--effective-url") + 1]
        Path(eff).write_text("http://127.0.0.1/admin", encoding="utf-8")
        return "   方法: curl\n"

    monkeypatch.setattr(mcp_server, "_run_fetch", fake_run_fetch)
    result = mcp_server._do_fetch_file("http://example.com/", "get", True, 30)

    assert result["success"] is False
    assert result["error_code"] == "SSRF_BLOCKED"
    assert result["effective_url"] == "http://127.0.0.1/admin"
    assert "content" not in result


def test_do_fetch_file_includes_effective_url(monkeypatch):
    def fake_run_fetch(args, timeout=mcp_server.DEFAULT_TIMEOUT, respect_robots=None):
        Path(args[2]).write_text("页面内容", encoding="utf-8")
        eff = args[args.index("--effective-url") + 1]
        Path(eff).write_text("http://93.184.216.34/final", encoding="utf-8")
        return "   方法: curl\n"

    monkeypatch.setattr(mcp_server, "_run_fetch", fake_run_fetch)
    result = mcp_server._do_fetch_file("http://example.com/", "get", True, 30)

    assert result["success"] is True
    assert result["effective_url"] == "http://93.184.216.34/final"
    assert result["content"] == "页面内容"


def test_do_fetch_file_adds_accept_html_flag(monkeypatch):
    captured = {}

    def fake_run_fetch(args, timeout=mcp_server.DEFAULT_TIMEOUT, respect_robots=None):
        captured["args"] = list(args)
        Path(args[2]).write_text("<html><body><h1>Title</h1></body></html>", encoding="utf-8")
        return "   方法: python-http\n"

    monkeypatch.setattr(mcp_server, "_run_fetch", fake_run_fetch)
    result = mcp_server._do_fetch_file(
        "http://example.com/", "get", False, 30, accept_html=True
    )

    assert result["success"] is True
    assert "--accept-html" in captured["args"]


def test_run_fetch_file_requests_html_for_uncompressed_content(monkeypatch):
    captured = {}

    def fake_do_fetch(url, mode, compress, timeout, **kwargs):
        captured["accept_html"] = kwargs.get("accept_html")
        return {"success": True, "content": "<html></html>"}

    monkeypatch.setattr(mcp_server, "_do_fetch_file", fake_do_fetch)
    monkeypatch.setattr(mcp_server.RATE_LIMITER, "wait", lambda url: None)

    mcp_server._run_fetch_file(
        "https://example.com/", "get", False, 10, 0, 0,
        use_cache=False, respect_robots=False,
    )
    assert captured["accept_html"] is True

    mcp_server._run_fetch_file(
        "https://example.com/", "get", True, 10, 0, 0,
        use_cache=False, respect_robots=False,
    )
    assert captured["accept_html"] is False


def test_fetch_timeout_returns_structured_error(monkeypatch):
    def fake_run_fetch(*args, **kwargs):
        raise mcp_server.FetchTimeoutError("fetch.sh 执行超时")

    monkeypatch.setattr(mcp_server, "_run_fetch", fake_run_fetch)
    result = mcp_server.smart3w_fetch(
        "http://93.184.216.34/", mode="get", timeout=5, use_cache=False
    )

    assert result["success"] is False
    assert result["error_code"] == "FETCH_TIMEOUT"


def test_fetch_rejects_credentials_in_url():
    result = mcp_server.smart3w_fetch("http://user:pass@example.com/")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_fetch_rejects_host_not_in_allowlist(monkeypatch):
    monkeypatch.setenv("SMART3W_ALLOWED_HOSTS", "example.com")
    result = mcp_server.smart3w_fetch("https://other.com/")

    assert result["success"] is False
    assert "SMART3W_ALLOWED_HOSTS" in result["error"]


def test_crawl_reports_invalid_and_duplicate_urls(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda *args, **kwargs: {"success": False, "error": "boom"},
    )
    result = mcp_server.smart3w_crawl(
        ["not a url", "https://example.com", "https://example.com"], mode="get"
    )

    assert result["invalid_count"] == 1
    assert result["duplicates_skipped"] == 1
    assert result["succeeded"] == 0
    assert result["success"] is False


def test_crawl_preserves_fetch_error_code(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda *args, **kwargs: {
            "success": False,
            "url": "https://example.com",
            "error": "fetch failed",
            "error_code": "FETCH_FAILED",
        },
    )
    result = mcp_server.smart3w_crawl(["https://example.com"], mode="get")

    assert result["results"][0]["error_code"] == "FETCH_FAILED"


def test_crawl_preserves_input_order(monkeypatch):
    def fake_fetch(url, *args, **kwargs):
        time.sleep(0.05 if "slow" in url else 0)
        return {"success": True, "url": url, "content": url}

    monkeypatch.setattr(mcp_server, "_run_fetch_file", fake_fetch)
    result = mcp_server.smart3w_crawl(
        ["https://example.com/slow", "https://example.com/fast"],
        mode="get",
        concurrency=2,
    )

    assert [item["url"] for item in result["results"]] == [
        "https://example.com/slow",
        "https://example.com/fast",
    ]

def test_search_read_top_attaches_content(monkeypatch):
    payload = {
        "success": True,
        "query": "q",
        "results": [
            {"title": "one", "url": "https://example.com/one", "snippet": "s1"},
            {"title": "two", "url": "https://example.com/two", "snippet": "s2"},
        ],
        "result_count": 2,
    }
    monkeypatch.setattr(mcp_server, "search_web", lambda *args, **kwargs: payload)
    calls = []

    def fake_fetch(url, *args, **kwargs):
        calls.append((url, args, kwargs))
        return {
            "success": True,
            "content": "正文",
            "truncated": False,
            "total_length": 2,
            "effective_url": url,
        }

    monkeypatch.setattr(mcp_server, "_run_fetch_file", fake_fetch)
    result = mcp_server.smart3w_search("q", read_top=1, content_max_chars=50)

    assert result["results"][0]["content"] == "正文"
    assert result["results"][0]["content_truncated"] is False
    assert "content" not in result["results"][1]
    assert result["read_top"] == 1
    assert result["read_succeeded"] == 1
    assert result["read_failed"] == 0
    assert calls and calls[0][0] == "https://example.com/one"


def test_search_read_top_records_fetch_failure(monkeypatch):
    payload = {
        "success": True,
        "query": "q",
        "results": [{"title": "one", "url": "https://example.com/one", "snippet": "s"}],
        "result_count": 1,
    }
    monkeypatch.setattr(mcp_server, "search_web", lambda *args, **kwargs: payload)
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda *args, **kwargs: {"success": False, "error": "boom", "error_code": "FETCH_FAILED"},
    )
    result = mcp_server.smart3w_search("q", read_top=1)

    assert result["results"][0]["content"] == ""
    assert result["results"][0]["read_error"] == "boom"
    assert result["read_failed"] == 1


def test_search_rejects_invalid_read_top():
    result = mcp_server.smart3w_search("q", read_top=6)
    assert result["success"] is False
    assert "read_top" in result["error"]


def test_do_fetch_file_parses_document_marker(monkeypatch):
    def fake_run_fetch(args, timeout=mcp_server.DEFAULT_TIMEOUT, respect_robots=None):
        Path(args[2]).write_text("Hello PDF", encoding="utf-8")
        return (
            '__SMART3W_DOCUMENT__ {"kind": "pdf", "pages": 1, "chars": 9}\n'
            "   方法: curl\n"
        )

    monkeypatch.setattr(mcp_server, "_run_fetch", fake_run_fetch)
    result = mcp_server._do_fetch_file("http://example.com/sample.pdf", "get", True, 30)

    assert result["success"] is True
    assert result["format"] == "pdf"
    assert result["pages"] == 1
    assert result["chars"] == 9
    assert result["document"]["kind"] == "pdf"


def test_resolve_bash_prefers_override(monkeypatch, tmp_path):
    fake = tmp_path / "bash.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("SMART3W_BASH", str(fake))
    assert mcp_server._resolve_bash() == str(fake)

def test_map_collects_sitemap_and_homepage_links(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)

    def fake_fetch(url, *args, **kwargs):
        if url.endswith("/robots.txt"):
            return {"success": True, "content": "Sitemap: https://example.com/sitemap.xml"}
        return {
            "success": True,
            "content": '<a href="/from-home">Home</a>',
            "effective_url": "https://example.com/",
        }

    def fake_collect(sitemap_url, max_urls=50, recursive=False):
        return [("", "page", "https://example.com/from-sitemap")], [], False, 1

    monkeypatch.setattr(mcp_server, "_run_fetch_file", fake_fetch)
    monkeypatch.setattr(mcp_server, "collect_sitemap_entries", fake_collect)
    result = mcp_server.smart3w_map("https://example.com/", limit=10)

    assert result["success"] is True
    assert result["count"] == 3
    assert result["sources"] == {"sitemap": 1, "homepage": 2}
    assert {item["url"] for item in result["urls"]} == {
        "https://example.com/",
        "https://example.com/from-sitemap",
        "https://example.com/from-home",
    }


def test_map_success_requires_at_least_one_url(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)

    def fake_fetch(url, *args, **kwargs):
        return {"success": True, "content": ""}

    monkeypatch.setattr(mcp_server, "_run_fetch_file", fake_fetch)
    monkeypatch.setattr(
        mcp_server, "collect_sitemap_entries",
        lambda sitemap_url, max_urls=50, recursive=False: ([], [], False, 1),
    )
    result = mcp_server.smart3w_map(
        "https://example.com/", include_sitemap=True, include_homepage=False
    )

    assert result["success"] is False
    assert result["count"] == 0
    assert result["complete"] is False
    assert result["partial"] is False
    assert result["error_code"] == "MAP_FAILED"


def test_map_marks_partial_when_a_source_fails(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)

    def fake_fetch(url, *args, **kwargs):
        return {
            "success": True,
            "content": '<a href="/ok">OK</a>',
            "effective_url": "https://example.com/",
        }

    monkeypatch.setattr(mcp_server, "_run_fetch_file", fake_fetch)
    monkeypatch.setattr(
        mcp_server, "collect_sitemap_entries",
        lambda sitemap_url, max_urls=50, recursive=False: (
            [], ["https://example.com/sitemap.xml: timeout"], False, 0
        ),
    )
    result = mcp_server.smart3w_map(
        "https://example.com/", include_sitemap=True, include_homepage=True
    )

    assert result["success"] is True
    assert result["count"] >= 2
    assert result["complete"] is False
    assert result["partial"] is True
    assert result["sitemap_errors"]


def test_map_homepage_failure_is_not_a_success(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(
        mcp_server, "_run_fetch_file",
        lambda *args, **kwargs: {"success": False, "error": "blocked"},
    )
    result = mcp_server.smart3w_map(
        "https://example.com/", include_sitemap=False, include_homepage=True
    )

    assert result["success"] is False
    assert result["complete"] is False
    assert result["partial"] is False
    assert result["error_code"] == "MAP_FAILED"
    assert result["homepage_error"] == "blocked"


def test_map_respects_path_filters(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)

    def fake_fetch(url, *args, **kwargs):
        if url.endswith("/robots.txt"):
            return {"success": False, "error": "not found"}
        return {
            "success": True,
            "content": '<a href="/docs/a">A</a><a href="/blog/b">B</a>',
        }

    monkeypatch.setattr(mcp_server, "_run_fetch_file", fake_fetch)
    result = mcp_server.smart3w_map(
        "https://example.com/", include_sitemap=False, path_include="/docs/*"
    )

    assert [item["url"] for item in result["urls"]] == ["https://example.com/docs/a"]


def test_crawl_site_bfs_follows_links(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(mcp_server, "_compress_html_text", lambda html: html)

    pages = {
        "https://example.com/": '<a href="/a">A</a><a href="/b">B</a>',
        "https://example.com/a": '<a href="/c">C</a>',
        "https://example.com/b": "B page",
        "https://example.com/c": "C page",
    }

    def fake_fetch(url, *args, **kwargs):
        return {
            "success": True,
            "content": pages.get(url, ""),
            "effective_url": url,
        }

    monkeypatch.setattr(mcp_server, "_run_fetch_file", fake_fetch)
    result = mcp_server.smart3w_crawl(
        seed_url="https://example.com/", max_depth=1, max_pages=10, compress=False
    )

    assert result["success"] is True
    assert result["unique"] == 3
    assert [item["crawled_url"] for item in result["results"]] == [
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_crawl_rejects_invalid_max_depth(monkeypatch):
    result = mcp_server.smart3w_crawl(seed_url="https://example.com/", max_depth=9)
    assert result["success"] is False
    assert "max_depth" in result["error"]
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_extract_css_fields(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda *args, **kwargs: {
            "success": True,
            "content": '<h1>Hello</h1><a href="/x">X</a>',
        },
    )
    result = mcp_server.smart3w_extract(
        ["https://example.com/page"],
        {"title": "h1", "links": {"selector": "a", "attr": "href", "all": True}},
    )

    assert result["success"] is True
    assert result["results"][0]["data"]["title"] == "Hello"
    assert result["results"][0]["data"]["links"] == ["/x"]


def test_extract_rejects_empty_schema(monkeypatch):
    result = mcp_server.smart3w_extract(["https://example.com/"], {})
    assert result["success"] is False
    assert "schema" in result["error"]


def test_job_missing_returns_not_found():
    result = mcp_server.smart3w_job("does-not-exist")
    assert result["success"] is False
    assert result["error_code"] == "JOB_NOT_FOUND"


def test_crawl_run_async_creates_and_completes_job(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(
        mcp_server, "_crawl_site", lambda *args, **kwargs: {"success": True, "results": []}
    )
    submitted = mcp_server.smart3w_crawl(
        seed_url="https://example.com/", max_depth=1, run_async=True
    )
    assert submitted["success"] is True
    assert submitted["status"] == "queued"

    job_id = submitted["job_id"]
    for _ in range(40):
        job = mcp_server.smart3w_job(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert job["status"] == "completed"
    assert job["result"]["success"] is True


def test_run_fetch_cancel_event_terminates_inflight_subprocess(monkeypatch, tmp_path):
    script = tmp_path / "slow.sh"
    script.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "FETCH_SH", script)
    cancel_event = threading.Event()
    timer = threading.Timer(0.2, cancel_event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(mcp_server.FetchCancelledError):
            mcp_server._run_fetch([], timeout=30, cancel_event=cancel_event)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3


def test_async_crawl_cancel_interrupts_inflight_fetch(monkeypatch):
    fetch_started = threading.Event()
    cancel_seen = threading.Event()

    def slow_fetch(url, *args, **kwargs):
        cancel_event = kwargs.get("cancel_event")
        fetch_started.set()
        for _ in range(100):
            if cancel_event is not None and cancel_event.is_set():
                cancel_seen.set()
                raise mcp_server.FetchCancelledError("抓取已取消")
            time.sleep(0.02)
        return {"success": True, "content": "", "effective_url": url}

    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(mcp_server, "_run_fetch_file", slow_fetch)
    submitted = mcp_server.smart3w_crawl(
        seed_url="https://example.com/", max_depth=1, run_async=True
    )
    assert submitted["success"] is True
    job_id = submitted["job_id"]
    assert fetch_started.wait(2)

    started = time.monotonic()
    cancelled = mcp_server.smart3w_job(job_id, cancel=True)
    assert cancelled["success"] is True

    for _ in range(100):
        job = mcp_server.smart3w_job(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.02)

    assert cancel_seen.is_set()
    assert job["status"] == "cancelled"
    assert time.monotonic() - started < 3


def test_map_does_not_reuse_host_policy_for_invalid_port(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)

    def fake_fetch(url, *args, **kwargs):
        body = (
            '<a href="https://example.com/good">good</a>'
            '<a href="https://example.com:99999/bad">bad</a>'
        )
        return {"success": True, "content": body, "effective_url": url}

    monkeypatch.setattr(mcp_server, "_run_fetch_file", fake_fetch)
    result = mcp_server.smart3w_map(
        "https://example.com/", include_sitemap=False, include_homepage=True
    )
    urls = [item["url"] for item in result["urls"]]
    assert "https://example.com/good" in urls
    assert not any("99999" in url for url in urls)


def test_new_job_rejects_when_registry_full_of_active_jobs(monkeypatch):
    monkeypatch.setattr(mcp_server, "JOBS", {})
    monkeypatch.setattr(mcp_server, "JOB_MAX_KEEP", 1)
    job = mcp_server._new_job()
    job["status"] = "running"
    with pytest.raises(mcp_server.JobLimitError):
        mcp_server._new_job()


def test_new_job_evicts_finished_job(monkeypatch):
    monkeypatch.setattr(mcp_server, "JOBS", {})
    monkeypatch.setattr(mcp_server, "JOB_MAX_KEEP", 1)
    first = mcp_server._new_job()
    first["status"] = "completed"
    second = mcp_server._new_job()
    assert second["job_id"] in mcp_server.JOBS
    assert first["job_id"] not in mcp_server.JOBS


def test_run_job_marks_fetch_cancelled_as_cancelled(monkeypatch):
    monkeypatch.setattr(mcp_server, "JOBS", {})
    job = mcp_server._new_job()

    def cancel_target():
        raise mcp_server.FetchCancelledError("抓取已取消")

    mcp_server._run_job(job, cancel_target)
    assert job["status"] == "cancelled"
    assert job["error"] == "抓取已取消"


def test_crawl_strips_urls_before_validation(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda url, *a, **k: {"success": True, "content": "", "effective_url": url},
    )
    result = mcp_server.smart3w_crawl(
        urls=[" https://example.com/ ", "https://example.com/two "], max_depth=0
    )
    assert result["success"] is True
    assert result["invalid_count"] == 0
    assert result["unique"] == 2


def test_extract_strips_urls_before_validation(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda url, *a, **k: {
            "success": True, "content": "<h1>Hello</h1>", "effective_url": url,
        },
    )
    result = mcp_server.smart3w_extract([" https://example.com/ "], {"title": "h1"})
    assert result["success"] is True
    assert result["invalid_count"] == 0
    assert result["results"][0]["data"]["title"] == "Hello"


def test_sitemap_and_doctor_reject_non_boolean_options():
    sitemap = mcp_server.smart3w_sitemap("https://example.com/sitemap.xml", recursive="yes")
    assert sitemap["error_code"] == "INVALID_ARGUMENT"
    doctor = mcp_server.smart3w_doctor(check_search="yes")
    assert doctor["error_code"] == "INVALID_ARGUMENT"


def test_sitemap_rejects_invalid_max_urls_with_error_code():
    result = mcp_server.smart3w_sitemap("https://example.com/sitemap.xml", 0)
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_main_rejects_invalid_transport_env(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "bogus")
    with pytest.raises(SystemExit) as excinfo:
        mcp_server.main([])
    assert excinfo.value.code == 2


def test_main_defaults_empty_path_to_mcp(monkeypatch):
    captured = {}
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setenv("MCP_PATH", "")
    mcp_server.main(["--transport", "streamable-http"])
    assert captured["streamable_http_path"] == "/mcp"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"compress": "false"},
        {"use_cache": "false"},
        {"respect_robots": "false"},
    ],
)
def test_fetch_rejects_non_boolean_flags(kwargs):
    result = mcp_server.smart3w_fetch("https://example.com/", **kwargs)
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "name",
    ["include_sitemap", "include_homepage", "same_domain", "respect_robots"],
)
def test_map_rejects_non_boolean_flags(monkeypatch, name):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    result = mcp_server.smart3w_map("https://example.com/", **{name: "yes"})
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_map_requires_at_least_one_source(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    result = mcp_server.smart3w_map(
        "https://example.com/",
        include_sitemap=False,
        include_homepage=False,
        search="",
    )
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_map_checks_url_shape_even_when_origin_is_cached(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(mcp_server, "ssrf_blocked", lambda url: None)
    monkeypatch.setattr(
        mcp_server,
        "extract_links",
        lambda *args, **kwargs: [
            {"url": "https://example.com/" + "a" * 9000},
            {"url": "https://example.com/\x01bad"},
        ],
    )
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda *args, **kwargs: {
            "success": True,
            "content": "",
            "effective_url": "https://example.com/",
        },
    )
    result = mcp_server.smart3w_map(
        "https://example.com/", include_sitemap=False, include_homepage=True
    )
    assert result["success"] is True
    assert [item["url"] for item in result["urls"]] == ["https://example.com/"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"compress": "false"},
        {"respect_robots": "false"},
        {"same_domain": "false"},
        {"run_async": "false"},
    ],
)
def test_crawl_rejects_non_boolean_flags(kwargs):
    result = mcp_server.smart3w_crawl(urls=["https://example.com/"], **kwargs)
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_crawl_seed_failure_sets_error_code(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(
        mcp_server,
        "_run_fetch_file",
        lambda *args, **kwargs: {"success": False, "error": "boom"},
    )
    result = mcp_server.smart3w_crawl(seed_url="https://example.com/", max_depth=1)
    assert result["success"] is False
    assert result["error_code"] == "CRAWL_FAILED"


def test_extract_rejects_non_boolean_respect_robots(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    result = mcp_server.smart3w_extract(
        ["https://example.com/"], {"title": "h1"}, respect_robots="false"
    )
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_job_rejects_non_boolean_cancel():
    result = mcp_server.smart3w_job("missing", cancel="yes")
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_interact_rejects_non_boolean_flags(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    for kwargs in ({"return_cookies": "yes"}, {"respect_robots": "no"}):
        result = asyncio.run(mcp_server.smart3w_interact("https://example.com/", **kwargs))
        assert result["success"] is False
        assert result["error_code"] == "INVALID_ARGUMENT"
