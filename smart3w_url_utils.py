"""Smart3W 公共 URL 工具：规范化、追踪参数过滤与 SSRF 检查。

仅依赖标准库，便于单元测试；被 MCP 层（smart3w_fetch_utils）与子进程脚本
（scripts/smart3w_utils.py）共同引用，避免 URL 逻辑重复实现。
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "spm", "from",
})

# 内网 / 保留 / 特殊用途地址段（SSRF 防护用）
PRIVATE_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("2001:db8::/32"),
)


def is_private_ip(ip: str) -> bool:
    """判断 IP 是否属于内网/保留/特殊用途地址段。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # 无法解析为合法 IP 时按不安全处理
    return any(addr in net for net in PRIVATE_NETWORKS)


def resolve_target_ips(host: str) -> list[str]:
    """解析主机名到 IP 列表；解析失败返回空列表。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return []
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    return ips


def ssrf_blocked(url: str) -> str | None:
    """SSRF 检查：目标指向内网/保留地址时返回错误信息，否则返回 None。

    SMART3W_ALLOW_PRIVATE_IPS=1 可关闭（仅建议在内网可信环境使用）。
    """
    if os.environ.get("SMART3W_ALLOW_PRIVATE_IPS", "0").strip().lower() in ("1", "true", "yes", "on"):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").strip().strip("[]")
    except ValueError:
        return "URL 格式无效"
    if not host:
        return "URL 缺少主机名"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        ips = resolve_target_ips(host)
        if not ips:
            return None  # 解析失败交给抓取层报错，避免误拦
        blocked = [ip for ip in ips if is_private_ip(ip)]
        if blocked:
            return f"目标主机解析到内网/保留地址，已拦截: {', '.join(blocked)}"
        return None
    if is_private_ip(host):
        return f"目标 IP 属于内网/保留地址，已拦截: {host}"
    return None


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
