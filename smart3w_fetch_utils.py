"""Smart3W 抓取治理工具：URL 规范化、TTL 缓存、robots.txt 检查与域名限速。

独立于 MCP / 抓取实现，仅依赖标准库，便于单元测试。
"""

from __future__ import annotations

import os
import threading
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import OrderedDict

USER_AGENT = "Smart3W/2.2.5 (+https://github.com/kid941005/smart3w)"

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "spm", "from",
})

DEFAULT_CACHE_TTL = int(os.environ.get("SMART3W_CACHE_TTL", "300"))
DEFAULT_CACHE_SIZE = int(os.environ.get("SMART3W_CACHE_SIZE", "64"))
DEFAULT_ROBOTS_TTL = int(os.environ.get("SMART3W_ROBOTS_TTL", "3600"))
DEFAULT_MIN_INTERVAL = float(os.environ.get("SMART3W_MIN_INTERVAL", "0.2"))
DEFAULT_RESPECT_ROBOTS = os.environ.get(
    "SMART3W_RESPECT_ROBOTS", "0"
).strip().lower() in ("1", "true", "yes", "on")
ROBOTS_FETCH_TIMEOUT = 5


def normalize_url(url: str) -> str:
    """URL 规范化：小写 scheme/host、去默认端口与 fragment、去追踪参数并排序 query。"""
    raw = (url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return raw
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None and (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    query = urllib.parse.urlencode(sorted(
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ))
    return urllib.parse.urlunsplit((scheme, netloc, parsed.path or "/", query, ""))


class TTLCache:
    """线程安全的有界 TTL 缓存（LRU 淘汰）。"""

    def __init__(self, ttl: int | float = DEFAULT_CACHE_TTL, max_size: int = DEFAULT_CACHE_SIZE):
        self.ttl = max(0.0, float(ttl))
        self.max_size = max(1, int(max_size))
        self._data: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> object | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.monotonic() - ts > self.ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key: str, value: object) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class RobotsChecker:
    """按域名缓存 robots.txt，并判断给定 URL 是否允许抓取。"""

    def __init__(
        self,
        ttl: int | float = DEFAULT_ROBOTS_TTL,
        timeout: int = ROBOTS_FETCH_TIMEOUT,
        user_agent: str = USER_AGENT,
    ):
        self.ttl = max(0.0, float(ttl))
        self.timeout = timeout
        self.user_agent = user_agent
        self._cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._lock = threading.Lock()

    def _load(self, scheme: str, domain: str) -> urllib.robotparser.RobotFileParser | None:
        """抓取并解析 robots.txt；任何失败都返回 None（视为无限制）。"""
        url = f"{scheme}://{domain}/robots.txt"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(raw.splitlines())
        return rp

    def allows(self, url: str) -> bool:
        """返回是否允许抓取；无法读取 robots.txt 时放行。"""
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            return False
        scheme = parsed.scheme.lower()
        domain = (parsed.hostname or "").lower()
        if scheme not in ("http", "https") or not domain:
            return False
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(domain)
            if entry is None or now - entry[0] > self.ttl:
                rp = self._load(scheme, domain)
                self._cache[domain] = (now, rp)
            else:
                rp = entry[1]
        if rp is None:
            return True
        return rp.can_fetch(self.user_agent, url)


class DomainRateLimiter:
    """按域名保证最小请求间隔，避免对同一站点过度抓取。"""

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL):
        self.min_interval = max(0.0, float(min_interval))
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        try:
            domain = (urllib.parse.urlsplit(url).hostname or "").lower()
        except ValueError:
            domain = ""
        if not domain:
            return
        delay = 0.0
        with self._lock:
            now = time.monotonic()
            last = self._last.get(domain, -float("inf"))
            delay = self.min_interval - (now - last)
            if delay > 0:
                self._last[domain] = now + delay
            else:
                self._last[domain] = now
        if delay > 0:
            time.sleep(delay)


RATE_LIMIT_MARKERS = ("429", "too many requests", "rate limit", "ratelimit", "access denied")


def is_rate_limited(message: str) -> bool:
    """根据错误信息判断是否命中限流/反爬，用于 crawl 的重试策略。"""
    low = (message or "").lower()
    return any(marker in low for marker in RATE_LIMIT_MARKERS)


def apply_window(result: dict, start_index: int = 0, max_length: int = 0) -> dict:
    """按 start_index / max_length 对结果正文切片，并补充窗口统计字段。"""
    content = result.get("content", "") or ""
    total_length = len(content)
    content = content[start_index:] if start_index else content
    truncated = bool(max_length and len(content) > max_length)
    if truncated:
        content = content[:max_length]
    out = dict(result)
    out.update({
        "content": content,
        "total_length": total_length,
        "chars_returned": len(content),
        "truncated": truncated,
        "start_index": start_index,
        "max_length": max_length,
    })
    return out
