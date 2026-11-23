"""Smart3W 站点发现与结构化抽取工具。

供 MCP 层实现 smart3w_map / smart3w_crawl(seed_url) / smart3w_extract 使用。
仅依赖 bs4 / lxml（项目已有依赖），不执行网络请求；所有发现 URL 仍由
mcp_server 统一调用 url_policy_error 做 SSRF / allowlist 校验。
"""

from __future__ import annotations

import fnmatch
import re
import urllib.parse

from bs4 import BeautifulSoup

from smart3w_url_utils import normalize_url


def same_site(candidate: str, root: str, include_subdomains: bool = True) -> bool:
    """判断 candidate 是否与 root 同站；默认允许子域名。"""
    try:
        c_host = (urllib.parse.urlsplit(candidate).hostname or "").strip().lower().rstrip(".")
        r_host = (urllib.parse.urlsplit(root).hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return False
    if not c_host or not r_host:
        return False
    if c_host == r_host:
        return True
    return include_subdomains and c_host.endswith("." + r_host)


def _split_patterns(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def path_allowed(path: str, include: str = "", exclude: str = "") -> bool:
    """按 path_include / path_exclude（逗号分隔，支持 fnmatch 通配符）过滤路径。"""
    normalized = "/" + (path or "").lstrip("/")

    def _match(pattern: str) -> bool:
        if any(ch in pattern for ch in "*?["):
            candidates = [normalized, normalized.lstrip("/")]
            if "/" not in pattern:
                candidates.append(normalized.rsplit("/", 1)[-1])
            return any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates)
        prefix = "/" + pattern.rstrip("/").lstrip("/")
        if prefix == "/":
            return True
        return normalized == prefix or normalized.startswith(prefix + "/")

    includes = _split_patterns(include)
    if includes and not any(_match(pattern) for pattern in includes):
        return False
    excludes = _split_patterns(exclude)
    if any(_match(pattern) for pattern in excludes):
        return False
    return True


def extract_links(
    html: str,
    base_url: str,
    *,
    same_domain: bool = True,
    include_subdomains: bool = True,
    path_include: str = "",
    path_exclude: str = "",
    max_links: int = 5000,
) -> list[dict[str, str]]:
    """从 HTML 中提取 http/https 链接，去重并返回 [{url, text, path}]。"""
    if not isinstance(html, str) or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        absolute = urllib.parse.urljoin(base_url, href)
        absolute = urllib.parse.urldefrag(absolute)[0]
        try:
            parsed = urllib.parse.urlsplit(absolute)
        except ValueError:
            continue
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            continue
        if same_domain and not same_site(absolute, base_url, include_subdomains=include_subdomains):
            continue
        path = parsed.path or "/"
        if not path_allowed(path, path_include, path_exclude):
            continue
        key = normalize_url(absolute)
        if not key or key in seen:
            continue
        seen.add(key)
        text = anchor.get_text(" ", strip=True)
        links.append({"url": absolute, "text": text, "path": path})
        if len(links) >= max_links:
            break
    return links


def parse_robots_sitemaps(text: str, base_url: str) -> list[str]:
    """从 robots.txt 文本中提取 Sitemap: 行，解析为绝对 URL 并去重。"""
    found: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        match = re.match(r"^\s*sitemap\s*:\s*(\S+)\s*$", line, re.IGNORECASE)
        if not match:
            continue
        url = urllib.parse.urljoin(base_url, match.group(1).strip())
        key = normalize_url(url)
        if key and key not in seen:
            seen.add(key)
            found.append(url)
    return found


def _css_value(node, attr: str):
    if attr and attr != "text":
        return node.get(attr)
    return node.get_text(" ", strip=True)


def _xpath_value(node, attr: str):
    if isinstance(node, bool):
        return "true" if node else "false"
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, str):
        return node.strip()
    if attr and attr != "text":
        return node.get(attr)
    return (node.text_content() or "").strip()


def _field_spec(value) -> dict:
    if isinstance(value, str):
        return {"selector": value, "attr": "text", "all": False}
    if isinstance(value, dict):
        selector = value.get("selector") or value.get("css") or value.get("xpath") or ""
        return {
            "selector": selector,
            "attr": str(value.get("attr") or "text"),
            "all": bool(value.get("all") or value.get("multiple")),
        }
    return {"selector": "", "attr": "text", "all": False}


def extract_fields(html: str, schema: dict, mode: str = "css") -> dict:
    """按 CSS/XPath schema 抽取字段，返回 {data, missing}。"""
    if not isinstance(schema, dict) or not schema:
        raise ValueError("schema 必须是非空对象")
    if mode not in ("css", "xpath"):
        raise ValueError("mode 必须是 css 或 xpath")
    if not isinstance(html, str) or not html.strip():
        raise ValueError("HTML 内容为空")

    if mode == "css":
        soup = BeautifulSoup(html, "html.parser")

        def select(selector: str):
            return soup.select(selector)
    else:
        from lxml import html as lxml_html

        tree = lxml_html.fromstring(html)

        def select(selector: str):
            return tree.xpath(selector)

    data: dict[str, object] = {}
    missing: list[str] = []
    for field, raw_spec in schema.items():
        spec = _field_spec(raw_spec)
        selector = spec["selector"]
        if not selector:
            data[str(field)] = [] if spec["all"] else None
            missing.append(str(field))
            continue
        try:
            nodes = select(selector)
        except Exception as exc:
            raise ValueError(f"字段 {field} 选择器无效: {exc}") from exc
        if nodes is None:
            nodes = []
        elif not isinstance(nodes, (list, tuple)):
            nodes = [nodes]
        values = []
        for node in nodes:
            value = _xpath_value(node, spec["attr"]) if mode == "xpath" else _css_value(node, spec["attr"])
            if value:
                values.append(value)
        if spec["all"]:
            data[str(field)] = values
        else:
            data[str(field)] = values[0] if values else None
        if not values:
            missing.append(str(field))
    return {"data": data, "missing": missing}
