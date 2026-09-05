#!/usr/bin/env python3
"""Smart3W 共享 Python 逻辑：搜索 / Sitemap 解析 / 正文压缩。

供 scripts/fetch.sh 以子命令方式调用：
    python3 smart3w_utils.py search <query> [count] [instance]
    python3 smart3w_utils.py sitemap <url> [max_urls]
    python3 smart3w_utils.py compress <input> <output>
    python3 smart3w_utils.py compress-wechat <input> <output>

约定：数据走 stdout，日志/错误走 stderr；compress 系列以退出码表示成功与否。
"""

import argparse
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_INSTANCE = "https://searxng.hqgg.top:59826"
SEARCH_TIMEOUT = 30
SITEMAP_TIMEOUT = 15

IMAGE_ATTRS = ("src", "data-src", "data-original")
WECHAT_IMAGE_ATTRS = ("data-src", "src", "data-original")
SKIP_TAGS = ("script", "style", "nav", "footer", "header", "aside")
CONTENT_TAGS = ("p", "section", "blockquote", "ul", "ol", "h1", "h2", "h3", "h4", "pre", "table", "img")
HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}


def _ensure_utf8_stdio():
    """Windows/GBK 环境下强制 stdout/stderr 使用 UTF-8，避免 JSON/日志编码报错。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _ssl_context():
    """返回 SSL 上下文；SMART3W_SSL_VERIFY=0/off/false 时关闭证书校验（仅限自签实例）。"""
    verify = os.environ.get("SMART3W_SSL_VERIFY", "1").strip().lower()
    disabled = verify in ("0", "false", "no", "off")
    ctx = ssl.create_default_context()
    if disabled:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _img_markdown(node, attrs=IMAGE_ATTRS):
    """从 img 节点提取 Markdown 图片链接；无有效 src 时返回 None。"""
    for attr in attrs:
        src = node.get(attr)
        if src:
            if src.startswith("//"):
                src = "https:" + src
            return f"![]({src})"
    return None


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
    return "\n\n".join(lines).strip()


def _render_inline(node):
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
            md = _img_markdown(child)
            if md:
                parts.append(md)
        elif name == "a":
            text = _render_inline(child).strip()
            href = child.get("href") or ""
            parts.append(f"[{text}]({href})" if text and href else text)
        elif name == "br":
            parts.append("\n")
        elif name == "code":
            parts.append("`" + child.get_text() + "`")
        elif name in ("strong", "b"):
            text = _render_inline(child)
            parts.append(f"**{text}**" if text.strip() else "")
        elif name in ("em", "i"):
            text = _render_inline(child)
            parts.append(f"*{text}*" if text.strip() else "")
        else:
            parts.append(_render_inline(child))
    return "".join(parts)


def _paragraph_text(node):
    """行内渲染后压缩空白，得到单段文本。"""
    return " ".join(_render_inline(node).split())


def _render_table(node):
    """把 <table> 渲染为 Markdown 表格（首行作为表头）。"""
    rows = []
    for tr in node.find_all("tr"):
        cells = [
            c.get_text(" ", strip=True).replace("|", "\\|")
            for c in tr.find_all(["td", "th"], recursive=False)
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |"]
    out.append("| " + " | ".join(["---"] * width) + " |")
    for row in rows[1:]:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def _render_block(node, handled):
    """渲染一个块级节点为 Markdown 段落列表；consumed 的子节点写入 handled 防止重复输出。"""
    name = node.name
    if name == "img":
        md = _img_markdown(node)
        return [md] if md else []
    if name in HEADING_LEVELS:
        text = _paragraph_text(node)
        return [("#" * HEADING_LEVELS[name] + " " + text)] if text else []
    if name == "p":
        text = _paragraph_text(node)
        return [text] if text else []
    if name == "blockquote":
        for p in node.find_all("p"):
            handled.add(id(p))
        text = _paragraph_text(node)
        return ["> " + text] if text else []
    if name in ("ul", "ol"):
        out = []
        for i, li in enumerate(node.find_all("li", recursive=False), start=1):
            text = _paragraph_text(li)
            if text:
                out.append(f"{i}. {text}" if name == "ol" else f"- {text}")
        return out
    if name == "pre":
        code = node.get_text()
        return [f"```\n{code}\n```"] if code.strip() else []
    if name == "table":
        md = _render_table(node)
        return [md] if md else []
    if name == "section":
        direct_blocks = node.find_all(CONTENT_TAGS, recursive=False)
        if direct_blocks:
            out = []
            for child in direct_blocks:
                handled.add(id(child))
                out.extend(_render_block(child, handled))
            return out
        text = _paragraph_text(node)
        return [text] if text else []
    return []


def _extract_blocks(container, wechat=False):
    """遍历容器，按文档顺序渲染全部块级内容，避免嵌套节点重复输出。"""
    parts = []
    handled = set()
    img_attrs = WECHAT_IMAGE_ATTRS if wechat else IMAGE_ATTRS
    for node in container.find_all(CONTENT_TAGS):
        if id(node) in handled:
            continue
        if node.name == "img":
            md = _img_markdown(node, img_attrs)
            if md:
                parts.append(md)
            continue
        parts.extend(_render_block(node, handled))
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
        title = doc.title().strip()
        # content() 比 summary() 保留更多结构（列表/表格/代码块），更利于 Markdown 保真
        soup = BeautifulSoup(doc.content(), "html.parser")
        for tag in soup.find_all(SKIP_TAGS):
            tag.decompose()

        parts = []
        if title:
            parts.append("# " + title)
        parts.extend(_extract_blocks(soup, wechat=False))

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


def cmd_search(query, count, instance):
    """SearXNG 搜索；无论成败都输出纯 JSON（保持与旧行为一致，退出码为 0）。"""
    params = {"q": query, "format": "json", "language": "zh-CN"}
    url = f"{instance}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=SEARCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in data.get("results", [])[:count]
        ]
        out = {"success": True, "query": query, "results": results, "result_count": len(results)}
    except Exception as exc:
        out = {"success": False, "error": str(exc), "query": query}
    print(json.dumps(out, ensure_ascii=False))


def cmd_sitemap(sitemap_url, max_urls):
    """解析 Sitemap（支持 index 与 urlset 格式），结果输出到 stdout。"""
    req = urllib.request.Request(sitemap_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=SITEMAP_TIMEOUT) as resp:
            content = resp.read().decode("utf-8", errors="replace")

        root = ET.fromstring(content)
        sm_ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        urls = []
        if "sitemapindex" in root.tag:
            for sm in root.findall(f"{sm_ns}sitemap"):
                loc = sm.find(f"{sm_ns}loc")
                if loc is not None and loc.text:
                    urls.append(("index", "", loc.text))
        else:
            for u in root.findall(f"{sm_ns}url"):
                loc = u.find(f"{sm_ns}loc")
                if loc is None:
                    loc = u.find("loc")
                lastmod = u.find(f"{sm_ns}lastmod")
                if lastmod is None:
                    lastmod = u.find("lastmod")
                lm = lastmod.text[:10] if lastmod is not None and lastmod.text else ""
                if loc is not None and loc.text:
                    urls.append((lm, "page", loc.text))

        print(f"✅ 共解析到 {len(urls)} 个URL\n")
        for i, (lm, utype, u) in enumerate(urls[:max_urls]):
            prefix = "📋 " if utype == "index" else "  "
            m = f"[{lm}] " if lm else "          "
            print(f"{prefix}{i+1:3d}. {m}{u}")
        if len(urls) > max_urls:
            print(f"\n... 还有 {len(urls) - max_urls} 个URL")
    except Exception as exc:
        print(f"❌ 解析失败: {exc}")


def main(argv=None):
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="smart3w_utils", description="Smart3W 搜索/Sitemap/压缩工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="SearXNG 搜索，输出 JSON")
    p_search.add_argument("query")
    p_search.add_argument("count", type=int, nargs="?", default=10)
    p_search.add_argument("instance", nargs="?", default=os.environ.get("SEARXNG_INSTANCE", DEFAULT_INSTANCE))

    p_sitemap = sub.add_parser("sitemap", help="解析 Sitemap")
    p_sitemap.add_argument("url")
    p_sitemap.add_argument("max_urls", type=int, nargs="?", default=50)

    for name, func in (("compress", compress_html), ("compress-wechat", compress_wechat)):
        p = sub.add_parser(name, help="正文压缩为 Markdown")
        p.add_argument("input")
        p.add_argument("output")
        p.set_defaults(_func=func)

    args = parser.parse_args(argv)

    if args.command == "search":
        cmd_search(args.query, args.count, args.instance)
    elif args.command == "sitemap":
        cmd_sitemap(args.url, args.max_urls)
    else:
        ok = args._func(args.input, args.output)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
