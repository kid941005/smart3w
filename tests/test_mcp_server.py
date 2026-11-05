"""mcp_server 层测试：结构化返回、搜索缓存与 SSRF 拦截（无真实网络依赖）。"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server  # noqa: E402


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
    def fake_run_fetch(args, timeout=mcp_server.DEFAULT_TIMEOUT):
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
    def fake_run_fetch(args, timeout=mcp_server.DEFAULT_TIMEOUT):
        Path(args[2]).write_text("页面内容", encoding="utf-8")
        eff = args[args.index("--effective-url") + 1]
        Path(eff).write_text("http://93.184.216.34/final", encoding="utf-8")
        return "   方法: curl\n"

    monkeypatch.setattr(mcp_server, "_run_fetch", fake_run_fetch)
    result = mcp_server._do_fetch_file("http://example.com/", "get", True, 30)

    assert result["success"] is True
    assert result["effective_url"] == "http://93.184.216.34/final"
    assert result["content"] == "页面内容"


def test_fetch_timeout_returns_structured_error(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="fetch.sh", timeout=1)

    monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)
    result = mcp_server.smart3w_fetch(
        "http://93.184.216.34/", mode="get", timeout=5, use_cache=False
    )

    assert result["success"] is False
    assert result["error_code"] == "FETCH_TIMEOUT"


def test_fetch_rejects_credentials_in_url():
    result = mcp_server.smart3w_fetch("http://user:pass@example.com/")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


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
