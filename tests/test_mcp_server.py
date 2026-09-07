"""mcp_server 层测试：结构化返回、搜索缓存与 SSRF 拦截（无真实网络依赖）。"""

import json
import sys
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


def test_search_caches_successful_results(monkeypatch):
    payload = json.dumps({
        "success": True,
        "query": "q",
        "engine": "searxng",
        "instance": "https://a.example",
        "params": {},
        "results": [{"title": "t", "url": "https://example.com/x", "snippet": "s"}],
        "result_count": 1,
    })
    calls = []
    monkeypatch.setattr(mcp_server, "_run_fetch", lambda args: calls.append(args) or payload)

    first = mcp_server.smart3w_search("cache-hit", 5)
    second = mcp_server.smart3w_search("cache-hit", 5)

    assert first["success"] is True
    assert second["cached"] is True
    assert len(calls) == 1


def test_search_does_not_cache_failures(monkeypatch):
    payload = json.dumps({
        "success": False,
        "query": "q",
        "results": [],
        "result_count": 0,
        "error": "boom",
    })
    calls = []
    monkeypatch.setattr(mcp_server, "_run_fetch", lambda args: calls.append(args) or payload)

    mcp_server.smart3w_search("cache-miss", 5)
    mcp_server.smart3w_search("cache-miss", 5)

    assert len(calls) == 2


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
