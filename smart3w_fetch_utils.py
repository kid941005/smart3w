"""Smart3W 抓取治理工具：TTL 缓存、robots.txt 检查与域名限速。

仅依赖标准库，便于单元测试。URL 规范化 / SSRF 检查等纯 URL 逻辑位于
smart3w_url_utils，本模块继续向后兼容地重新导出这些符号。
"""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import OrderedDict

from smart3w_url_utils import (  # noqa: F401  # 向后兼容：继续从本模块导出
    PRIVATE_NETWORKS,
    TRACKING_PARAMS,
    env_flag,
    is_private_ip,
    normalize_url,
    resolve_target_ips,
    ssrf_blocked,
)

USER_AGENT = "Smart3W/2.3.2 (+https://github.com/kid941005/smart3w)"


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量；非法值回退默认值，避免 import 阶段直接崩溃。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


DEFAULT_CACHE_TTL = _env_int("SMART3W_CACHE_TTL", 300)
DEFAULT_CACHE_SIZE = max(1, _env_int("SMART3W_CACHE_SIZE", 64))
DEFAULT_ROBOTS_TTL = _env_int("SMART3W_ROBOTS_TTL", 3600)
DEFAULT_MIN_INTERVAL = max(0.0, _env_float("SMART3W_MIN_INTERVAL", 0.2))
DEFAULT_RESPECT_ROBOTS = env_flag("SMART3W_RESPECT_ROBOTS", False)
ROBOTS_FETCH_TIMEOUT = 5
ROBOTS_FAILURE_TTL = 60


class TTLCache:
    """线程安全的有界 TTL 缓存（LRU 淘汰）。"""

    def __init__(self, ttl: int | float = DEFAULT_CACHE_TTL, max_size: int = DEFAULT_CACHE_SIZE):
        self.ttl = max(0.0, float(ttl))
        self.max_size = max(1, int(max_size))
        self._data: OrderedDict[object, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: object) -> object | None:
        if self.ttl <= 0:
            return None
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

    def put(self, key: object, value: object) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class RobotsChecker:
    """按 origin（scheme + host + port）缓存 robots.txt，并判断 URL 是否允许抓取。"""

    def __init__(
        self,
        ttl: int | float = DEFAULT_ROBOTS_TTL,
        timeout: int = ROBOTS_FETCH_TIMEOUT,
        user_agent: str | None = None,
        max_entries: int = 512,
    ):
        self.ttl = max(0.0, float(ttl))
        self.failure_ttl = min(self.ttl, ROBOTS_FAILURE_TTL)
        self.timeout = timeout
        # 在实例化时读取，便于运行期/测试修改 SMART3W_ROBOTS_UA
        self.user_agent = user_agent or os.environ.get("SMART3W_ROBOTS_UA") or USER_AGENT
        self.max_entries = max(1, int(max_entries))
        self._cache: dict[tuple[str, str], tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._lock = threading.Lock()

    def _load(self, scheme: str, origin: str) -> urllib.robotparser.RobotFileParser | None:
        """抓取并解析 robots.txt。

        遵循 RFC 9309：401/403 与 5xx 视为完全禁止；其他 4xx 视为无规则；
        网络异常则返回 None，保持向后兼容地放行。
        """
        url = f"{scheme}://{origin}/robots.txt"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # 兼容带 BOM 的 robots.txt（部分站点/Windows 编辑器会写入 UTF-8 BOM）
                raw = resp.read().decode("utf-8-sig", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) or exc.code >= 500:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(["User-agent: *", "Disallow: /"])
                return rp
            return None
        except Exception:
            return None
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(raw.splitlines())
        return rp

    def _cache_put(self, key: tuple[str, str], rp: urllib.robotparser.RobotFileParser | None) -> None:
        with self._lock:
            if len(self._cache) >= self.max_entries and key not in self._cache:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)
            self._cache[key] = (time.monotonic(), rp)

    def allows(self, url: str) -> bool:
        """返回是否允许抓取；无法读取 robots.txt 时放行。

        网络请求在锁外执行，避免某个慢站点阻塞其他域名的检查。
        """
        try:
            parsed = urllib.parse.urlsplit(url)
            scheme = parsed.scheme.lower()
            host = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return False
        if scheme not in ("http", "https") or not host:
            return False
        host_part = f"[{host}]" if ":" in host else host
        origin = host_part if port is None else f"{host_part}:{port}"
        key = (scheme, origin)
        now = time.monotonic()
        missing = object()
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                ttl = self.ttl if entry[1] is not None else self.failure_ttl
                if now - entry[0] <= ttl:
                    rp = entry[1]
                else:
                    rp = missing
            else:
                rp = missing
        if rp is missing:
            rp = self._load(scheme, origin)
            self._cache_put(key, rp)
        if rp is None:
            return True
        return rp.can_fetch(self.user_agent, url)


class DomainRateLimiter:
    """按域名保证最小请求间隔，避免对同一站点过度抓取。"""

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL, max_entries: int = 1024):
        self.min_interval = max(0.0, float(min_interval))
        self.max_entries = max(1, int(max_entries))
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
            if domain not in self._last and len(self._last) >= self.max_entries:
                oldest = min(self._last, key=self._last.get)
                self._last.pop(oldest, None)
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


def _nonneg_int(value: object, default: int = 0) -> int:
    """把输入安全地转换为非负整数；非法值回退默认值。"""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return default


def apply_window(result: dict, start_index: int = 0, max_length: int = 0) -> dict:
    """按 start_index / max_length 对结果正文切片，并补充窗口统计字段。"""
    content = result.get("content", "") or ""
    total_length = len(content)
    start = _nonneg_int(start_index, 0)
    limit = _nonneg_int(max_length, 0)
    content = content[start:] if start else content
    truncated = bool(limit and len(content) > limit)
    if truncated:
        content = content[:limit]
    out = dict(result)
    out.update({
        "content": content,
        "total_length": total_length,
        "chars_returned": len(content),
        "truncated": truncated,
        "start_index": start,
        "max_length": limit,
    })
    return out
