"""smart3w_fetch_utils 单元测试（纯标准库，无真实网络依赖）。"""

import sys
import time
import urllib.robotparser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart3w_fetch_utils import (  # noqa: E402
    DomainRateLimiter,
    RobotsChecker,
    TTLCache,
    apply_window,
    is_rate_limited,
    normalize_url,
)


def test_normalize_url_lowercases_strips_tracking_and_sorts_query():
    assert (
        normalize_url("HTTP://Example.COM:80/a?b=2&utm_source=x&a=1#frag")
        == "http://example.com/a?a=1&b=2"
    )


def test_normalize_url_keeps_non_default_port_and_path():
    assert normalize_url("https://example.com:8443/a") == "https://example.com:8443/a"
    assert normalize_url("https://example.com") == "https://example.com/"


def test_normalize_url_returns_raw_for_non_http():
    assert normalize_url("ftp://example.com/a") == "ftp://example.com/a"


def test_ttl_cache_expiry(monkeypatch):
    state = {"now": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: state["now"])
    cache = TTLCache(ttl=1, max_size=2)
    cache.put("a", 1)
    assert cache.get("a") == 1
    state["now"] = 2.0
    assert cache.get("a") is None


def test_ttl_cache_lru_eviction(monkeypatch):
    state = {"now": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: state["now"])
    cache = TTLCache(ttl=1000, max_size=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_robots_allows_blocks_and_caches(monkeypatch):
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /private"])
    loads = []
    checker = RobotsChecker(ttl=1000)
    monkeypatch.setattr(checker, "_load", lambda scheme, domain: loads.append(domain) or rp)

    assert checker.allows("https://example.com/public") is True
    assert checker.allows("https://example.com/private/x") is False
    assert checker.allows("https://example.com/public") is True
    assert loads == ["example.com"]  # 第二次同域名命中缓存


def test_robots_unavailable_allows(monkeypatch):
    checker = RobotsChecker()
    monkeypatch.setattr(checker, "_load", lambda scheme, domain: None)
    assert checker.allows("https://example.com/anything") is True


def test_rate_limiter_spaces_same_domain(monkeypatch):
    sleeps = []
    state = {"now": 0.0}
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(time, "monotonic", lambda: state["now"])
    limiter = DomainRateLimiter(min_interval=1.0)

    limiter.wait("https://example.com/a")
    assert sleeps == []
    limiter.wait("https://example.com/b")
    assert sleeps == [1.0]
    limiter.wait("https://other.com/x")
    assert sleeps == [1.0]  # 不同域名不等待


def test_is_rate_limited():
    assert is_rate_limited("HTTP 429 Too Many Requests")
    assert is_rate_limited("rate limit exceeded")
    assert not is_rate_limited("connection reset")


def test_apply_window_slices_and_reports():
    result = {"content": "0123456789", "success": True}
    out = apply_window(result, start_index=2, max_length=4)
    assert out["content"] == "2345"
    assert out["total_length"] == 10
    assert out["chars_returned"] == 4
    assert out["truncated"] is True

    full = apply_window(result, start_index=0, max_length=0)
    assert full["content"] == "0123456789"
    assert full["truncated"] is False
