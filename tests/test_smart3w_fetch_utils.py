"""smart3w_fetch_utils 单元测试（纯标准库，无真实网络依赖）。"""

import socket
import sys
import threading
import time
import urllib.request
import urllib.robotparser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart3w_fetch_utils import (  # noqa: E402
    DomainRateLimiter,
    RobotsChecker,
    TTLCache,
    apply_window,
    is_private_ip,
    is_rate_limited,
    normalize_url,
    ssrf_blocked,
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


def test_normalize_url_keeps_invalid_port():
    assert normalize_url("http://example.com:99999/a") == "http://example.com:99999/a"


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


def test_robots_server_error_disallows(monkeypatch):
    def raise_500(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.com/robots.txt", 500, "boom", {}, None)

    checker = RobotsChecker()
    monkeypatch.setattr(urllib.request, "urlopen", raise_500)
    assert checker.allows("https://example.com/anything") is False


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


def test_rate_limiter_caps_domain_entries():
    limiter = DomainRateLimiter(min_interval=0, max_entries=2)
    for domain in ("a", "b", "c"):
        limiter.wait(f"https://{domain}.example/")

    assert len(limiter._last) == 2


def test_is_rate_limited():
    assert is_rate_limited("HTTP 429 Too Many Requests")
    assert is_rate_limited("rate limit exceeded")
    assert not is_rate_limited("connection reset")


def test_apply_window_handles_non_integer_offsets():
    out = apply_window({"content": "abcdef"}, "x", "y")
    assert out["content"] == "abcdef"
    assert out["start_index"] == 0
    assert out["max_length"] == 0


def test_normalize_url_handles_non_string():
    assert normalize_url(None) == ""
    assert normalize_url(123) == ""


def test_robots_ua_reads_env_at_instance_time(monkeypatch):
    monkeypatch.setenv("SMART3W_ROBOTS_UA", "CustomBot/1.0")
    assert RobotsChecker().user_agent == "CustomBot/1.0"


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


def test_is_private_ip():
    assert is_private_ip("127.0.0.1")
    assert is_private_ip("10.0.0.1")
    assert is_private_ip("172.16.0.1")
    assert is_private_ip("192.168.1.1")
    assert is_private_ip("169.254.0.1")
    assert is_private_ip("::1")
    assert is_private_ip("fc00::1")
    assert not is_private_ip("8.8.8.8")
    assert not is_private_ip("1.1.1.1")
    assert not is_private_ip("2606:4700:4700::1111")


def test_is_private_ip_special_ranges_and_exceptions():
    # IPv4-compatible IPv6、NAT64 本地前缀、特殊用途 IPv6 都应拦截
    assert is_private_ip("::7f00:1")
    assert is_private_ip("64:ff9b:1::1")
    assert is_private_ip("100::1")
    assert is_private_ip("2001::1")
    # well-known NAT64 按嵌入 IPv4 判断
    assert is_private_ip("64:ff9b::7f00:1")
    assert not is_private_ip("64:ff9b::808:808")
    # IANA 公共例外不应误拦
    assert not is_private_ip("192.0.0.9")
    assert not is_private_ip("2001:1::1")
    assert not is_private_ip("2001:3::1")


def test_ssrf_blocked_blocks_private_ip_and_localhost():
    assert ssrf_blocked("http://127.0.0.1/admin")
    assert ssrf_blocked("http://10.0.0.1/")
    assert ssrf_blocked("http://192.168.1.1/")
    assert ssrf_blocked("http://[::1]/")


def test_ssrf_blocked_allows_public_ip_and_public_host():
    assert ssrf_blocked("http://8.8.8.8/") is None
    assert ssrf_blocked("https://example.com/") is None


def test_ssrf_blocked_blocks_hostname_resolving_to_private(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))],
    )
    err = ssrf_blocked("http://internal.example.com/")
    assert err is not None
    assert "10.0.0.5" in err


def test_ssrf_blocked_allow_private_ips_env(monkeypatch):
    monkeypatch.setenv("SMART3W_ALLOW_PRIVATE_IPS", "1")
    assert ssrf_blocked("http://127.0.0.1/") is None
    assert ssrf_blocked("http://192.168.1.1/") is None


def _fake_private_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: [(10, 1, 6, "", ("2001::1", 0, 0, 0))],
    )


def test_trust_proxy_dns_skips_local_resolution(monkeypatch):
    monkeypatch.setenv("SMART3W_PROXY_URL", "http://proxy:8080")
    monkeypatch.setenv("SMART3W_TRUST_PROXY_DNS", "1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    _fake_private_dns(monkeypatch)
    assert ssrf_blocked("https://www.google.com/") is None


def test_trust_proxy_dns_requires_proxy_url(monkeypatch):
    monkeypatch.delenv("SMART3W_PROXY_URL", raising=False)
    monkeypatch.setenv("SMART3W_TRUST_PROXY_DNS", "1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    _fake_private_dns(monkeypatch)
    err = ssrf_blocked("https://www.google.com/")
    assert err is not None
    assert "2001::1" in err


def test_trust_proxy_dns_respects_no_proxy(monkeypatch):
    monkeypatch.setenv("SMART3W_PROXY_URL", "http://proxy:8080")
    monkeypatch.setenv("SMART3W_TRUST_PROXY_DNS", "1")
    monkeypatch.setenv("NO_PROXY", "www.google.com")
    _fake_private_dns(monkeypatch)
    assert ssrf_blocked("https://www.google.com/") is not None


def test_trust_proxy_dns_still_blocks_private_literals_and_localhost(monkeypatch):
    monkeypatch.setenv("SMART3W_PROXY_URL", "http://proxy:8080")
    monkeypatch.setenv("SMART3W_TRUST_PROXY_DNS", "1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert ssrf_blocked("http://127.0.0.1/") is not None
    assert ssrf_blocked("http://localhost/") is not None


def test_allowed_hosts_blocks_unlisted_host(monkeypatch):
    monkeypatch.setenv("SMART3W_ALLOWED_HOSTS", "example.com, *.trusted.com")
    assert ssrf_blocked("https://example.com/a") is None
    assert ssrf_blocked("https://sub.trusted.com/a") is None
    err = ssrf_blocked("https://other.com/a")
    assert err is not None
    assert "SMART3W_ALLOWED_HOSTS" in err


def test_allowed_hosts_wildcard_does_not_match_apex(monkeypatch):
    monkeypatch.setenv("SMART3W_ALLOWED_HOSTS", "*.trusted.com")
    assert ssrf_blocked("https://trusted.com/a") is not None
    assert ssrf_blocked("https://a.trusted.com/a") is None


def test_ssrf_blocked_missing_host():
    assert ssrf_blocked("http:///path") is not None


def test_normalize_url_keeps_ipv6_brackets():
    assert normalize_url("http://[::1]:8080/a") == "http://[::1]:8080/a"


def test_normalize_url_strips_userinfo():
    assert normalize_url("https://user:pass@example.com/a") == "https://example.com/a"


def test_ssrf_blocks_ipv4_mapped_ipv6():
    assert ssrf_blocked("http://[::ffff:127.0.0.1]/")
    assert ssrf_blocked("http://[::ffff:7f00:1]/")
    assert ssrf_blocked("http://[64:ff9b::7f00:1]/")


def test_ttl_cache_zero_ttl_disables_cache():
    cache = TTLCache(ttl=0)
    cache.put("a", 1)
    assert cache.get("a") is None


def test_robots_uses_origin_with_port(monkeypatch):
    checker = RobotsChecker(ttl=1000)
    calls = []
    monkeypatch.setattr(
        checker, "_load", lambda scheme, origin: calls.append((scheme, origin)) or None
    )
    assert checker.allows("http://example.com:8080/x") is True
    assert checker.allows("http://example.com/y") is True
    assert calls == [("http", "example.com:8080"), ("http", "example.com")]


def test_robots_handles_utf8_bom(monkeypatch):
    class FakeResponse:
        def read(self):
            return b"\xef\xbb\xbfUser-agent: *\r\nDisallow: /private\r\n"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    checker = RobotsChecker(ttl=1000)
    assert checker.allows("https://example.com/private/x") is False


def test_robots_loader_does_not_block_other_origins(monkeypatch):
    checker = RobotsChecker(ttl=1000)
    started = threading.Event()
    release = threading.Event()

    def slow_load(scheme, origin):
        if origin == "slow.example":
            started.set()
            assert release.wait(2)
        return None

    monkeypatch.setattr(checker, "_load", slow_load)
    worker = threading.Thread(target=checker.allows, args=("https://slow.example/a",))
    worker.start()
    assert started.wait(2)

    start = time.monotonic()
    assert checker.allows("https://fast.example/b") is True
    assert time.monotonic() - start < 1

    release.set()
    worker.join(2)
