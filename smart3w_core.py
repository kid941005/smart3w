#!/usr/bin/env python3
"""Smart3W 核心逻辑：搜索 / Sitemap 解析 / 正文压缩。

本模块不依赖 MCP，也不依赖 shell，供 MCP 服务与 scripts/smart3w_utils.py
CLI 包装层共同调用，避免“MCP → bash → Python”的重复子进程与重复解析。
"""

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from smart3w_url_utils import env_flag, normalize_url, ssrf_blocked

DEFAULT_INSTANCE = "https://searxng.hqgg.top:59826"
SEARCH_TIMEOUT = 30
SITEMAP_TIMEOUT = 15
MAX_SITEMAP_BYTES = 10 * 1024 * 1024
SITEMAP_MAX_DEPTH = 2
SITEMAP_MAX_FETCHES = 100
SITEMAP_TOTAL_TIMEOUT = 120
SEARXNG_MAX_PAGES = 10
SEARXNG_PAGE_SIZE = 10
SEARCH_TOTAL_TIMEOUT = 120
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

SEARCH_TIME_RANGES = ("", "day", "week", "month", "year")
FETCH_MODES = ("smart", "get", "fetch", "stealthy")

IMAGE_ATTRS = ("src", "data-src", "data-original")
WECHAT_IMAGE_ATTRS = ("data-src", "src", "data-original")
SKIP_TAGS = ("script", "style", "nav", "footer", "header", "aside")
CONTAINER_TAGS = ("section", "article", "div", "main", "body", "html", "figure")
HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class SitemapError(RuntimeError):
    """Sitemap 获取 / 解析失败；CLI 与 MCP 分别转换为退出码和结构化错误。"""


def _ssl_context():
    """返回 SSL 上下文；SMART3W_SSL_VERIFY=0/off/false 时关闭证书校验（仅限自签实例）。"""
    ctx = ssl.create_default_context()
    if not env_flag("SMART3W_SSL_VERIFY", True):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _img_markdown(node, attrs=IMAGE_ATTRS):
    """从 img 节点提取 Markdown 图片链接；无有效 src 时返回 None。"""
    for attr in attrs:
        src = node.get(attr)
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        return f"![]({src})"
    return None


def _is_list_item(part):
    """判断 Markdown 片段是否为列表项（含缩进的嵌套项）。"""
    stripped = part.lstrip()
    return stripped.startswith(("- ", "* ")) or (
        stripped[:1].isdigit() and ". " in stripped[:5]
    )


def _dedupe_parts(parts):
    """去除空段落、重复图片与连续重复文本，输出单块 Markdown 文本。"""
    lines = []
    seen_images = set()
    prev = None
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("![]("):
            if part in seen_images:
                continue
            seen_images.add(part)
        elif part == prev:
            continue
        lines.append(part)
        prev = part

    # 相邻列表项用单换行连接（紧凑列表），其余块用空行分隔
    out = []
    prev_is_list = False
    for part in lines:
        is_list = _is_list_item(part)
        if out:
            out.append("\n" if (is_list and prev_is_list) else "\n\n")
        out.append(part)
        prev_is_list = is_list
    return "".join(out).strip()


def _render_inline(node, img_attrs=IMAGE_ATTRS):
    """递归渲染行内内容：链接、加粗/斜体、行内代码、换行、图片。"""
    parts = []
    for child in getattr(node, "children", []):
        if isinstance(child, str):
            parts.append(child)
            continue
        name = getattr(child, "name", None)
        if name is None:
            parts.append(str(child))
            continue
        if name in SKIP_TAGS:
            continue
        if name == "img":
            md = _img_markdown(child, img_attrs)
            if md:
                parts.append(md)
        elif name == "a":
            text = _render_inline(child, img_attrs).strip()
            href = (child.get("href") or "").strip()
            if href.lower().startswith(("javascript:", "data:")):
                href = ""
            parts.append(f"[{text}]({href})" if text and href else text)
        elif name == "br":
            parts.append("\n")
        elif name == "code":
            parts.append("`" + child.get_text() + "`")
        elif name in ("strong", "b"):
            text = _render_inline(child, img_attrs)
            parts.append(f"**{text}**" if text.strip() else "")
        elif name in ("em", "i"):
            text = _render_inline(child, img_attrs)
            parts.append(f"*{text}*" if text.strip() else "")
        else:
            parts.append(_render_inline(child, img_attrs))
    return "".join(parts)


def _paragraph_text(node, img_attrs=IMAGE_ATTRS):
    """行内渲染后压缩空白，得到单段文本。"""
    return " ".join(_render_inline(node, img_attrs).split())


def _render_table(node, img_attrs=IMAGE_ATTRS):
    """把 <table> 渲染为 Markdown 表格（首行作为表头）。"""
    rows = []
    for tr in node.find_all("tr"):
        cells = [
            _paragraph_text(cell, img_attrs).replace("|", "\\|")
            for cell in tr.find_all(["td", "th"], recursive=False)
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    has_header = node.find("th") is not None
    header = rows[0] if has_header else [""] * width
    data_rows = rows[1:] if has_header else rows
    out = ["| " + " | ".join(header) + " |"]
    out.append("| " + " | ".join(["---"] * width) + " |")
    for row in data_rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def _render_list(node, out, depth=0, img_attrs=IMAGE_ATTRS):
    """递归渲染 <ul>/<ol>；嵌套列表缩进输出，避免父项文本与子项重复。"""
    ordered = node.name == "ol"
    indent = "  " * depth
    for index, item in enumerate(node.find_all("li", recursive=False), start=1):
        inline_parts = []
        nested_lists = []
        for child in item.children:
            if isinstance(child, str):
                inline_parts.append(child)
                continue
            name = getattr(child, "name", None)
            if name in SKIP_TAGS:
                continue
            if name in ("ul", "ol"):
                nested_lists.append(child)
            else:
                inline_parts.append(_render_inline(child, img_attrs))
        text = " ".join(" ".join(inline_parts).split())
        marker = f"{index}." if ordered else "-"
        if text:
            out.append(f"{indent}{marker} {text}")
        for nested in nested_lists:
            _render_list(nested, out, depth + 1, img_attrs)


def _render_blocks(container, out, depth=0, img_attrs=IMAGE_ATTRS):
    """按文档顺序递归渲染块级节点；每个节点只渲染一次，避免嵌套重复输出。"""
    buffer: list[str] = []

    def flush():
        if not buffer:
            return
        text = " ".join(" ".join(buffer).split())
        buffer.clear()
        if text:
            out.append(text)

    for child in container.children:
        if isinstance(child, str):
            buffer.append(child)
            continue
        name = getattr(child, "name", None)
        if name is None or name in SKIP_TAGS:
            continue
        if name in HEADING_LEVELS:
            flush()
            text = _paragraph_text(child, img_attrs)
            if text:
                out.append("#" * HEADING_LEVELS[name] + " " + text)
        elif name == "p":
            flush()
            text = _paragraph_text(child, img_attrs)
            if text:
                out.append(text)
        elif name in ("ul", "ol"):
            flush()
            _render_list(child, out, depth, img_attrs)
        elif name == "blockquote":
            flush()
            inner: list[str] = []
            _render_blocks(child, inner, depth, img_attrs)
            for block in inner:
                out.extend("> " + line for line in block.splitlines())
        elif name == "pre":
            flush()
            code = child.get_text().rstrip("\n")
            if code.strip():
                out.append("```\n" + code + "\n```")
        elif name == "table":
            flush()
            table = _render_table(child, img_attrs)
            if table:
                out.append(table)
        elif name == "img":
            flush()
            md = _img_markdown(child, img_attrs)
            if md:
                out.append(md)
        elif name in CONTAINER_TAGS:
            flush()
            _render_blocks(child, out, depth, img_attrs)
        else:
            # 行内元素或未知块：按行内内容累积，稍后合并为段落
            buffer.append(_render_inline(child, img_attrs))
    flush()


def _extract_blocks(container, wechat=False):
    """遍历容器，按文档顺序渲染全部块级内容，避免嵌套节点重复输出。"""
    parts: list[str] = []
    _render_blocks(container, parts, 0, WECHAT_IMAGE_ATTRS if wechat else IMAGE_ATTRS)
    return parts


def compress_html(input_path, output_path):
    """使用 readability-lxml 提取正文并转为 Markdown（保留链接/列表/表格/代码块）；成功返回 True。"""
    try:
        from bs4 import BeautifulSoup
        from readability import Document

        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
        if not html.strip():
            return False

        doc = Document(html)
        title = (doc.title() or "").strip()
        # content() 比 summary() 保留更多结构（列表/表格/代码块），更利于 Markdown 保真
        soup = BeautifulSoup(doc.content(), "html.parser")
        for tag in soup.find_all(SKIP_TAGS):
            tag.decompose()

        body = _extract_blocks(soup, wechat=False)
        # 页面标题与正文首个标题重复时只保留正文标题，避免重复输出
        if title and body and body[0].startswith("# ") and body[0][2:].strip() == title:
            body = body[1:]

        parts = []
        if title:
            parts.append("# " + title)
        parts.extend(body)

        text = _dedupe_parts(parts)
        if not text:
            return False
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return True
    except Exception as exc:  # 压缩失败不中断抓取，调用方会回退为原始 HTML
        print(f"compress 失败: {exc}", file=sys.stderr)
        return False


def compress_wechat(input_path, output_path):
    """微信文章专用提取（js_content / rich_media_content）；成功返回 True。"""
    try:
        from bs4 import BeautifulSoup

        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
        if not html.strip():
            return False

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(SKIP_TAGS):
            tag.decompose()

        content_div = soup.find("div", id="js_content")
        if content_div is None:
            content_div = soup.find("div", class_="rich_media_content")
        if content_div is None:
            return False

        text = _dedupe_parts(_extract_blocks(content_div, wechat=True))
        if not text or len(text) <= 50:
            return False
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return True
    except Exception as exc:
        print(f"compress-wechat 失败: {exc}", file=sys.stderr)
        return False


def _search_instances(primary):
    """返回去重后的实例列表：主实例 + SEARXNG_INSTANCES 环境变量中的备用实例。"""
    seen = []
    candidates = [primary if isinstance(primary, str) else ""] + [
        x.strip() for x in os.environ.get("SEARXNG_INSTANCES", "").split(",") if x.strip()
    ]
    for url in candidates:
        url = (url or "").strip().rstrip("/")
        if url and url not in seen:
            seen.append(url)
    return seen


def _as_list(value):
    """把 SearXNG 返回的 engines/positions 归一化为标量 list，兼容字符串/数字与 None。"""
    if isinstance(value, list):
        return [
            item for item in value
            if isinstance(item, (str, int, float)) and not isinstance(item, bool)
        ]
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [value]
    return []


def _fetch_search(instance, query, count, language, time_range, categories, safesearch,
                  engines, pageno=1):
    """请求单个 SearXNG 实例，返回原始 JSON。"""
    if not instance.startswith(("http://", "https://")):
        raise ValueError(f"实例地址必须是 http/https: {instance}")
    params = {"q": query, "format": "json"}
    if language:
        params["language"] = language
    if time_range:
        params["time_range"] = time_range
    if categories:
        params["categories"] = categories
    if safesearch:
        params["safesearch"] = str(safesearch)
    if engines:
        params["engines"] = engines
    if pageno > 1:
        params["pageno"] = str(pageno)
    url = f"{instance.rstrip('/')}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=SEARCH_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _as_engine_list(value):
    """engines 统一为字符串，避免上下文中混入数字导致 sorted() 崩溃。"""
    return [str(item) for item in _as_list(value)]


def _merge_results(merged, results):
    """按规范化 URL 合并多页/多引擎结果；返回新增条目数。"""
    added = 0
    for result in results or []:
        if not isinstance(result, dict):
            continue
        raw_url = result.get("url")
        if not isinstance(raw_url, str):
            continue
        key = normalize_url(raw_url)
        if not key:
            continue
        if key in merged:
            prev = merged[key]
            prev["engines"] = sorted(
                set(prev.get("engines") or []) | set(_as_engine_list(result.get("engines")))
            )
            positions = (prev.get("positions") or []) + _as_list(result.get("positions"))
            prev["positions"] = list(dict.fromkeys(positions))
            if not prev.get("score") and result.get("score") is not None:
                prev["score"] = result["score"]
            continue
        merged[key] = {
            "title": result.get("title", ""),
            "url": raw_url,
            "snippet": result.get("content", ""),
            "score": result.get("score"),
            "engines": sorted(set(_as_engine_list(result.get("engines")))),
            "positions": _as_list(result.get("positions")),
        }
        added += 1
    return added


def search_web(query, count=10, instance=None, language="zh-CN", time_range="",
               categories="", safesearch=0, engines=""):
    """SearXNG 搜索；多实例 fallback + 结果去重 + 自动翻页，返回结构化 dict。"""
    query = (query or "").strip() if isinstance(query, str) else ""
    out = {"success": False, "query": query, "results": [], "result_count": 0}
    if not query:
        out["error"] = "query 不能为空"
        return out
    if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > 100:
        out["error"] = "count 必须是 1-100 的整数"
        return out
    if (
        not isinstance(safesearch, int)
        or isinstance(safesearch, bool)
        or safesearch not in (0, 1, 2)
    ):
        out["error"] = "safesearch 必须是 0/1/2"
        return out
    if time_range not in SEARCH_TIME_RANGES:
        out["error"] = "time_range 必须是 day/week/month/year 之一或留空"
        return out

    if not isinstance(instance, str) or not instance.strip():
        instance = os.environ.get("SEARXNG_INSTANCE", DEFAULT_INSTANCE)
    instance = instance.strip()
    errors = []
    merged = {}
    used_instance = None
    first_data = None
    deadline = time.monotonic() + SEARCH_TOTAL_TIMEOUT
    for inst in _search_instances(instance):
        if time.monotonic() >= deadline:
            errors.append("搜索总超时")
            break
        try:
            payload = _fetch_search(
                inst, query, count, language, time_range, categories, safesearch, engines,
                pageno=1,
            )
        except Exception as exc:
            errors.append(f"{inst}: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{inst}: 返回了非对象 JSON")
            continue
        used_instance = inst
        first_data = payload
        _merge_results(merged, payload.get("results"))
        break  # 第一个成功的实例即采用；备用实例仅在主实例失败时兜底

    if used_instance is None or first_data is None:
        out["error"] = "; ".join(errors) or "无可用 SearXNG 实例"
        return out

    # 单页通常只返回 10 条左右；只有首屏满页时才继续翻页，避免对短结果集做无谓请求
    page = 1
    first_page_size = len(first_data.get("results") or [])
    reported_total = first_data.get("number_of_results")
    # 有 total 时以 total 为准；没有时退回“首屏满页”判断，兼容不同 SearXNG 页大小
    should_paginate = (
        isinstance(reported_total, int) and reported_total > len(merged)
    ) or first_page_size >= SEARXNG_PAGE_SIZE
    while (
        should_paginate
        and len(merged) < count
        and page < SEARXNG_MAX_PAGES
        and time.monotonic() < deadline
    ):
        page += 1
        try:
            payload = _fetch_search(
                used_instance, query, count, language, time_range, categories,
                safesearch, engines, pageno=page,
            )
        except Exception as exc:
            errors.append(f"{used_instance} page {page}: {exc}")
            break
        if not isinstance(payload, dict):
            break
        if _merge_results(merged, payload.get("results")) == 0:
            break

    results = list(merged.values())[:count]
    out.update({
        "success": True,
        "engine": "searxng",
        "instance": used_instance,
        "params": {
            "language": language,
            "time_range": time_range,
            "categories": categories,
            "safesearch": safesearch,
            "engines": engines,
        },
        "results": results,
        "result_count": len(results),
    })
    # 透传 SearXNG 扩展元数据（仅在存在时附加，避免空字段噪音）
    for key in ("answers", "corrections", "suggestions", "infoboxes"):
        if first_data.get(key):
            out[key] = first_data[key]
    if errors:
        out["log"] = "; ".join(errors)
    return out


def cmd_search(query, count, instance, language="zh-CN", time_range="", categories="",
               safesearch=0, engines=""):
    """CLI 入口：搜索并输出纯 JSON；返回是否成功，供 CLI 设置退出码。"""
    result = search_web(
        query, count, instance, language=language, time_range=time_range,
        categories=categories, safesearch=safesearch, engines=engines,
    )
    print(json.dumps(result, ensure_ascii=False))
    return bool(result.get("success"))


def _child_text(node, tag):
    """读取任意命名空间下子元素的文本（兼容无 namespace 的 sitemap）。"""
    child = node.find(f"{{*}}{tag}")
    if child is None or not child.text:
        return ""
    return child.text.strip()


def _fetch_sitemap_root(url):
    """下载并解析一个 sitemap，返回 ElementTree 根节点；失败抛 SitemapError。"""
    blocked = ssrf_blocked(url)
    if blocked:
        raise SitemapError(f"目标被拦截: {blocked}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=SITEMAP_TIMEOUT) as resp:
            # 校验重定向后的最终 URL，防止 302 跳到内网/保留地址绕过 SSRF
            blocked = ssrf_blocked(resp.geturl())
            if blocked:
                raise SitemapError(f"重定向目标被拦截: {blocked}")
            content = resp.read(MAX_SITEMAP_BYTES).decode("utf-8", errors="replace")
    except SitemapError:
        raise
    except Exception as exc:
        raise SitemapError(f"解析失败: {exc}") from exc
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise SitemapError(f"解析失败: {exc}") from exc


def parse_sitemap(sitemap_url, max_urls=50, recursive=False):
    """解析 Sitemap，返回格式化文本；支持 index/urlset 与可选递归展开。"""
    if not isinstance(sitemap_url, str) or not sitemap_url.startswith(("http://", "https://")):
        raise SitemapError("仅支持 http/https sitemap URL")
    if (
        not isinstance(max_urls, int)
        or isinstance(max_urls, bool)
        or max_urls < 1
        or max_urls > 10000
    ):
        raise SitemapError("max_urls 必须是 1-10000 的整数")

    entries = []
    errors = []
    visited = set()
    queue = [(sitemap_url, 0)]
    deadline = time.monotonic() + SITEMAP_TOTAL_TIMEOUT
    while (
        queue
        and len(entries) < max_urls
        and len(visited) < SITEMAP_MAX_FETCHES
        and time.monotonic() < deadline
    ):
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            root = _fetch_sitemap_root(url)
        except SitemapError as exc:
            # 根 sitemap 失败必须向上抛；递归中的子 sitemap 失败只跳过，保留部分结果
            if url == sitemap_url:
                raise
            errors.append(f"{url}: {exc}")
            continue
        if root.tag.endswith("sitemapindex"):
            for sitemap in root.findall("{*}sitemap"):
                if len(entries) >= max_urls:
                    break
                loc = _child_text(sitemap, "loc")
                if not loc:
                    continue
                loc = urllib.parse.urljoin(url, loc)
                if recursive and depth < SITEMAP_MAX_DEPTH:
                    queue.append((loc, depth + 1))
                else:
                    entries.append(("", "index", loc))
        else:
            for url_node in root.findall("{*}url"):
                if len(entries) >= max_urls:
                    break
                loc = _child_text(url_node, "loc")
                if not loc:
                    continue
                loc = urllib.parse.urljoin(url, loc)
                lastmod = _child_text(url_node, "lastmod")[:10]
                entries.append((lastmod, "page", loc))

    # 递归时不同子 sitemap 可能包含相同 URL，按 URL 去重并保持顺序
    unique = []
    seen_urls = set()
    for entry in entries:
        if entry[2] in seen_urls:
            continue
        seen_urls.add(entry[2])
        unique.append(entry)
    entries = unique[:max_urls]

    lines = [f"✅ 共解析到 {len(entries)} 个URL", ""]
    for index, (lastmod, url_type, url) in enumerate(entries):
        prefix = "📋 " if url_type == "index" else "  "
        date_part = f"[{lastmod}] " if lastmod else "          "
        lines.append(f"{prefix}{index + 1:3d}. {date_part}{url}")
    if queue:
        lines.append(
            f"\n... 已达到限制（max_urls={max_urls}，最多抓取 {SITEMAP_MAX_FETCHES} 个 sitemap / "
            f"{SITEMAP_TOTAL_TIMEOUT}s），仍有未展开的 sitemap"
        )
    if errors:
        detail = "; ".join(errors[:5])
        if len(errors) > 5:
            detail += f"; ... 共 {len(errors)} 个失败"
        lines.append(f"\n⚠️ 已跳过失败的子 sitemap: {detail}")
    return "\n".join(lines)


def cmd_sitemap(sitemap_url, max_urls, recursive=False):
    """CLI 入口：解析 Sitemap 并输出文本；失败时 stderr + 非零退出码。"""
    try:
        print(parse_sitemap(sitemap_url, max_urls, recursive=recursive))
    except SitemapError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
