"""Smart3W 公共 URL 工具：规范化、追踪参数过滤与 SSRF 检查。

仅依赖标准库，便于单元测试；被 MCP 层（smart3w_fetch_utils / mcp_server）与
子进程脚本（scripts/smart3w_utils.py）共同引用，避免 URL 逻辑重复实现。
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

# 内网 / 保留 / 特殊用途地址段（SSRF 防护用）。
# 参考 modelcontextprotocol/servers/src/fetch 的地址分类，并补充 NAT64 本地前缀、
# IPv4-compatible IPv6 与 IANA 公共例外，避免漏拦或误拦。
PRIVATE_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    # IPv4-compatible IPv6（RFC 4291 2.5.5.1）：包含 :: 与 ::1，且不能路由。
    ipaddress.ip_network("::/96"),
    # NAT64 本地使用前缀（RFC 8215），不是公网 NAT64。
    ipaddress.ip_network("64:ff9b:1::/48"),
    # 2001::/23 覆盖 Teredo、benchmarking、ORCHID 等特殊用途段。
    ipaddress.ip_network("2001::/23"),
    ipaddress.ip_network("2001:db8::/32"),
    ipaddress.ip_network("2002::/16"),         # 6to4
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fec0::/10"),         # 已废弃的 site-local
    ipaddress.ip_network("ff00::/8"),
)

# 上述特殊段中仍然全球可达的例外（与 CPython ipaddress 的公开例外一致）。
PUBLIC_EXCEPTIONS = (
    ipaddress.ip_network("192.0.0.9/32"),
    ipaddress.ip_network("192.0.0.10/32"),
    ipaddress.ip_network("2001:1::1/128"),
    ipaddress.ip_network("2001:1::2/128"),
    ipaddress.ip_network("2001:3::/32"),
    ipaddress.ip_network("2001:4:112::/48"),
    ipaddress.ip_network("2001:20::/28"),
    ipaddress.ip_network("2001:30::/28"),
)

# RFC 6052 的 well-known NAT64 前缀：按低 32 位嵌入的 IPv4 判断。
NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

# 可选的主机白名单：逗号分隔，支持精确主机、IP 与 *.example.com。
ALLOWED_HOSTS_ENV = "SMART3W_ALLOWED_HOSTS"


def _normalize_allowed_host(entry: str) -> str | None:
    """规范化一条白名单条目；非法条目返回 None（配置错误不应阻断全部抓取）。"""
    value = (entry or "").strip().rstrip(".").lower()
    if not value:
        return None
    wildcard = value.startswith("*.")
    name = value[2:] if wildcard else value
    if not name or any(ch in name for ch in "*/\\:@[]?#% ") or ".." in name:
        return None
    try:
        return ipaddress.ip_address(name.strip("[]")).compressed
    except ValueError:
        pass
    return f"*.{name}" if wildcard else name


def get_allowed_hosts() -> tuple[str, ...]:
    """读取 SMART3W_ALLOWED_HOSTS（每次调用读取，便于运行期修改）。"""
    raw = os.environ.get(ALLOWED_HOSTS_ENV, "")
    entries: list[str] = []
    for part in raw.split(","):
        normalized = _normalize_allowed_host(part)
        if normalized and normalized not in entries:
            entries.append(normalized)
    return tuple(entries)


def host_is_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    """判断 host 是否命中白名单；通配符只匹配子域名，不匹配裸域名。"""
    value = (host or "").strip().strip("[]").rstrip(".").lower()
    try:
        value = ipaddress.ip_address(value).compressed
    except ValueError:
        pass
    for entry in allowed_hosts:
        if entry.startswith("*."):
            if value.endswith(entry[1:]) and value != entry[2:]:
                return True
        elif value == entry:
            return True
    return False


def allowed_hosts_error(url: str) -> str | None:
    """检查 URL 是否在可选的 SMART3W_ALLOWED_HOSTS 白名单内。"""
    allowed = get_allowed_hosts()
    if not allowed:
        return None
    if not isinstance(url, str):
        return "URL 格式无效"
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        host = (parsed.hostname or "").strip().strip("[]").rstrip(".")
    except ValueError:
        return "URL 格式无效"
    if not host:
        return "URL 缺少主机名"
    if not host_is_allowed(host, allowed):
        return f"目标主机不在 {ALLOWED_HOSTS_ENV} 白名单中: {host}"
    return None


def env_flag(name: str, default: bool = False) -> bool:
    """读取布尔型环境变量；未设置或值无法识别时返回 default。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def is_private_ip(ip: str) -> bool:
    """判断 IP 是否不属于全球可达的公网地址。

    IPv6 地址会先剥离 zone id（如 fe80::1%eth0）；IPv4-mapped IPv6
    （::ffff:127.0.0.1）与 well-known NAT64（64:ff9b::/96）按其嵌入的
    IPv4 地址递归判断；其余特殊用途段与 IANA 公共例外参考官方 fetch 实现。
    """
    try:
        addr = ipaddress.ip_address((ip or "").split("%", 1)[0])
    except ValueError:
        return True  # 无法解析为合法 IP 时按不安全处理
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return is_private_ip(str(mapped))
        if addr in NAT64_WELL_KNOWN:
            embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
            return is_private_ip(str(embedded))
    if any(addr in net for net in PUBLIC_EXCEPTIONS):
        return False
    if any(addr in net for net in PRIVATE_NETWORKS):
        return True
    return not (addr.is_global and not addr.is_multicast)


def resolve_target_ips(host: str) -> list[str]:
    """解析主机名到 IP 列表；解析失败返回空列表。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return []
    ips: list[str] = []
    for info in infos:
        ip = str(info[4][0]).split("%", 1)[0]
        if ip not in ips:
            ips.append(ip)
    return ips


def ssrf_blocked(url: str) -> str | None:
    """SSRF 检查：目标指向内网/保留地址时返回错误信息，否则返回 None。

    SMART3W_ALLOW_PRIVATE_IPS=1 可关闭（仅建议在内网可信环境使用）。
    SMART3W_ALLOWED_HOSTS 非空时，还会先做主机白名单校验。
    注意：DNS 解析与实际连接之间仍存在 rebinding 窗口，生产环境应叠加
    出站网络策略（如只允许公网出口 / 代理白名单）。
    """
    allow_error = allowed_hosts_error(url)
    if allow_error:
        return allow_error
    if env_flag("SMART3W_ALLOW_PRIVATE_IPS", False):
        return None
    raw = (url or "").strip()
    if not raw or any(ord(ch) < 0x20 for ch in raw):
        return "URL 格式无效"
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").strip().strip("[]")
    except ValueError:
        return "URL 格式无效"
    if not host:
        return "URL 缺少主机名"
    host = host.split("%", 1)[0].rstrip(".")
    if not host or any(ch.isspace() for ch in host):
        return "URL 主机名包含非法字符"
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


def url_policy_error(url: str, max_length: int = 8192) -> str | None:
    """统一 URL 形状 + 主机白名单 + SSRF 校验；MCP 与 HTTP 抓取器共用。"""
    if not isinstance(url, str):
        return "URL 必须是字符串"
    raw = url.strip()
    if not raw or len(raw) > max_length:
        return f"URL 不能为空且长度不能超过 {max_length}"
    if any(ord(ch) < 0x20 for ch in raw):
        return "URL 包含非法控制字符"
    try:
        parsed = urllib.parse.urlsplit(raw)
        _ = parsed.port  # 触发非法端口校验
    except ValueError as exc:
        return f"URL 格式无效: {exc}"
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return "仅支持 http/https URL"
    if parsed.username or parsed.password:
        return "URL 不允许包含用户名/密码"
    return ssrf_blocked(raw)


def normalize_url(url: str) -> str:
    """URL 规范化：小写 scheme/host、去默认端口与 fragment、去追踪参数并排序 query。

    同时丢弃 URL 中的 userinfo（user:pass@host），避免凭据进入缓存键或日志；
    对 IPv6 主机名保留方括号，保证输出仍是合法 URL。
    """
    if not isinstance(url, str):
        return ""
    raw = url.strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw
    scheme = parsed.scheme.lower()
    try:
        host = (parsed.hostname or "").lower()
    except ValueError:
        return raw
    if scheme not in ("http", "https") or not host:
        return raw
    try:
        port = parsed.port
    except ValueError:
        return raw  # 端口非法时不尝试“修复”，保持原样交由调用方报错
    if port is not None and (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        port = None
    host_part = f"[{host}]" if ":" in host else host
    netloc = host_part if port is None else f"{host_part}:{port}"
    query = urllib.parse.urlencode(sorted(
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ))
    return urllib.parse.urlunsplit((scheme, netloc, parsed.path or "/", query, ""))
