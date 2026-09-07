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
