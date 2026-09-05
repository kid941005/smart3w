#!/usr/bin/env python3
"""Smart3W 共享 Python 逻辑：搜索 / Sitemap 解析 / 正文压缩。

供 scripts/fetch.sh 以子命令方式调用，替代原先散落在 Bash heredoc 中的内嵌代码：
    python3 smart3w_utils.py search <query> [count] [instance]
    python3 smart3w_utils.py sitemap <url> [max_urls]
    python3 smart3w_utils.py compress <input> <output>
    python3 smart3w_utils.py compress-wechat <input> <output>

约定：数据走 stdout，日志/错误走 stderr；compress 系列以退出码表示成功与否。
"""

import argparse
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_INSTANCE = "https://searxng.hqgg.top:59826"
SEARCH_TIMEOUT = 30
SITEMAP_TIMEOUT = 15

IMAGE_ATTRS = ("src", "data-src", "data-original")
SKIP_TAGS = ("script", "style", "nav", "footer", "header", "aside")
CONTENT_TAGS = ("p", "section", "blockquote", "ul", "ol", "h1", "h2", "h3", "h4", "img")
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


def _img_markdown(node):
    """从 img 节点提取 Markdown 图片链接；无有效 src 时返回 None。"""
    for attr in IMAGE_ATTRS:
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


def compress_html(input_path, output_path):
    """使用 readability-lxml 提取正文并转为 Markdown；成功返回 True。"""
    try:
        from bs4 import BeautifulSoup
        from readability import Document

        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
        if not html.strip():
            return False

        doc = Document(html)
        title = doc.title().strip()
        soup = BeautifulSoup(doc.summary(), "html.parser")
        for tag in soup.find_all(SKIP_TAGS):
            tag.decompose()

        parts = []
        if title:
            parts.append("# " + title)

        # find_all 已递归覆盖所有 <img>，无需再对子节点重复遍历
        for node in soup.find_all(CONTENT_TAGS):
            if node.name == "img":
                md = _img_markdown(node)
                if md:
                    parts.append(md)
                continue
            text = node.get_text(separator=" ", strip=True)
            if not text:
                continue
            level = HEADING_LEVELS.get(node.name)
            parts.append(("#" * level + " " if level else "") + text)

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

        parts = []
        handled = set()
        for node in content_div.find_all(CONTENT_TAGS):
            if id(node) in handled:
                continue
            if node.name == "img":
                md = _img_markdown(node)
                if md:
                    parts.append(md)
                continue

            text = ""
            if node.name == "p":
                text = node.get_text(separator=" ", strip=True)
            elif node.name == "section":
                direct_ps = node.find_all("p", recursive=False)
                if direct_ps:
                    for p in direct_ps:
                        handled.add(id(p))
                        p_text = p.get_text(separator=" ", strip=True)
                        if p_text:
                            parts.append(p_text)
                elif not (node.find("img") and not node.get_text(strip=True)):
                    text = node.get_text(separator=" ", strip=True)
            else:
                text = node.get_text(separator=" ", strip=True)

            if text:
                parts.append(text)

        text = _dedupe_parts(parts)
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
