"""缓存新鲜度与正文筛选接入测试（无网络）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcp_server  # noqa: E402
from smart3w_fetch_utils import TTLCache  # noqa: E402


def test_ttlcache_get_with_age():
    cache = TTLCache(ttl=60, max_size=2)
    cache.put("k", {"content": "v"})
    entry = cache.get_with_age("k")
    assert entry is not None
    value, age = entry
    assert value == {"content": "v"}
    assert age >= 0


def test_fetch_cache_reports_age_and_can_force_refresh(monkeypatch):
    calls = {"count": 0}

    def fake_fetch(*args, **kwargs):
        calls["count"] += 1
        return {"success": True, "content": "fresh body", "url": args[0]}

    monkeypatch.setattr(mcp_server, "_do_fetch_file", fake_fetch)
    monkeypatch.setattr(mcp_server.RATE_LIMITER, "wait", lambda url: None)
    mcp_server.FETCH_CACHE.clear()

    first = mcp_server._run_fetch_file("https://example.com/cache-age", "get", True, 1, 0, 0)
    second = mcp_server._run_fetch_file("https://example.com/cache-age", "get", True, 1, 0, 0)
    third = mcp_server._run_fetch_file(
        "https://example.com/cache-age", "get", True, 1, 0, 0, max_age_seconds=0
    )

    assert first.get("cached") is not True
    assert second.get("cached") is True
    assert second.get("cache_age") >= 0
    assert third.get("cached") is not True
    assert calls["count"] == 2


def test_finalize_fetch_result_filters_by_query():
    result = {
        "success": True,
        "content": "# Apple\n\nApple fruit dessert.\n\n# Car\n\nCar engine fuel.",
    }
    out = mcp_server._finalize_fetch_result(result, "apple", 0, 0, 0)
    assert "fruit dessert" in out["content"]
    assert "car engine" not in out["content"].lower()
    assert out["original_length"] > len(out["content"])
    assert out["filter"]["query"] == "apple"


def test_smart3w_fetch_validates_max_age_and_tokens():
    bad_age = mcp_server.smart3w_fetch("https://example.com/", max_age_seconds=-2)
    assert bad_age["success"] is False
    assert bad_age["error_code"] == "INVALID_ARGUMENT"

    bad_tokens = mcp_server.smart3w_fetch("https://example.com/", max_tokens=-1)
    assert bad_tokens["success"] is False
    assert bad_tokens["error_code"] == "INVALID_ARGUMENT"
