#!/bin/bash
# smart3w 智能抓取脚本
# 用法:
#   ./fetch.sh search <关键词> [数量]     SearXNG 搜索
#   ./fetch.sh get <URL> [输出文件]       快速抓取（HTTP）
#   ./fetch.sh fetch <URL> [输出文件]     动态页面（浏览器渲染）
#   ./fetch.sh stealthy <URL> [输出文件]  绕过反爬
#   ./fetch.sh sitemap <url> [最大条数]  Sitemap 索引解析
#   ./fetch.sh smart <URL> [输出文件]    智能选择（默认）

ACTION="${1:-smart}"
shift

SEARXNG_INSTANCE="${SEARXNG_INSTANCE:-https://searxng.example.com:59826}"

case "$ACTION" in
    search)
        QUERY="$1"
        COUNT="${2:-10}"
        if [ -z "$QUERY" ]; then
            echo "用法: $0 search <关键词> [数量]" >&2
            exit 1
        fi
        exec "$(dirname "$0")/search.sh" "$QUERY" "$COUNT"
        ;;

    get)
        URL="$1"
        OUTPUT="${2:-/tmp/fetch_result.md}"
        if [ -z "$URL" ]; then
            echo "用法: $0 get <URL> [输出文件]" >&2
            exit 1
        fi
        echo "=== HTTP 抓取 ==="
        echo "目标: $URL"
        scrapling extract get "$URL" "$OUTPUT"
        echo "✅ 成功 → $OUTPUT ($(wc -c < "$OUTPUT") bytes)"
        ;;

    fetch)
        URL="$1"
        OUTPUT="${2:-/tmp/fetch_result.md}"
        if [ -z "$URL" ]; then
            echo "用法: $0 fetch <URL> [输出文件]" >&2
            exit 1
        fi
        echo "=== 浏览器渲染抓取 ==="
        echo "目标: $URL"
        scrapling extract fetch "$URL" "$OUTPUT" --headless
        echo "✅ 成功 → $OUTPUT ($(wc -c < "$OUTPUT") bytes)"
        ;;

    stealthy)
        URL="$1"
        OUTPUT="${2:-/tmp/fetch_result.md}"
        if [ -z "$URL" ]; then
            echo "用法: $0 stealthy <URL> [输出文件]" >&2
            exit 1
        fi
        echo "=== 隐身浏览器抓取（绕过反爬）==="
        echo "目标: $URL"
        scrapling extract stealthy-fetch "$URL" "$OUTPUT" --headless --solve-cloudflare
        echo "✅ 成功 → $OUTPUT ($(wc -c < "$OUTPUT") bytes)"
        ;;

    sitemap)
        SITEMAP_URL="$1"
        if [ -z "$SITEMAP_URL" ]; then
            echo "用法: $0 sitemap <sitemap_url> [最大条数]" >&2
            exit 1
        fi
        MAX_URLS="${2:-50}"
        echo "=== Sitemap 索引解析 ==="
        echo "目标: $SITEMAP_URL"
        echo "最大URL数: $MAX_URLS"
        echo ""
        python3 - "$SITEMAP_URL" "$MAX_URLS" << 'PYEOF'
import sys, urllib.request, ssl, xml.etree.ElementTree as ET

sitemap_url = sys.argv[1]
max_urls = int(sys.argv[2]) if len(sys.argv) > 2 else 50

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

try:
    req = urllib.request.Request(sitemap_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        content = resp.read().decode("utf-8")

    root = ET.fromstring(content)
    SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

    urls = []
    # Sitemap Index
    if "sitemapindex" in root.tag:
        for sm in root.findall(f"{SM}sitemap"):
            loc = sm.find(f"{SM}loc")
            if loc is not None and loc.text:
                urls.append(("index", "", loc.text))
        for sm in root.findall("sitemap"):
            loc = sm.find("loc")
            if loc is not None and loc.text and loc.text not in [u[2] for u in urls]:
                urls.append(("index", "", loc.text))
    # URL Set
    else:
        for u in root.findall(f"{SM}url"):
            loc = u.find(f"{SM}loc")
            if loc is None:
                loc = u.find("loc")
            lm = ""
            lastmod = u.find(f"{SM}lastmod")
            if lastmod is None:
                lastmod = u.find("lastmod")
            if lastmod is not None and lastmod.text:
                lm = lastmod.text[:10]
            if loc is not None and loc.text:
                urls.append((lm, "page", loc.text))
        for u in root.findall("url"):
            loc = u.find("loc")
            lm = ""
            lastmod = u.find("lastmod")
            if lastmod is not None and lastmod.text:
                lm = lastmod.text[:10]
            if loc is not None and loc.text:
                urls.append((lm, "page", loc.text))

    print(f"✅ 共解析到 {len(urls)} 个URL\n")
    for i, (lm, utype, u) in enumerate(urls[:max_urls]):
        prefix = "📋 " if utype == "index" else "  "
        m = f"[{lm}] " if lm else "          "
        print(f"{prefix}{i+1:3d}. {m}{u}")

    if len(urls) > max_urls:
        print(f"\n... 还有 {len(urls) - max_urls} 个URL，增加数量参数查看更多")

except Exception as e:
    print(f"❌ 解析失败: {e}")
PYEOF
        ;;

    smart|*)
        URL="$1"
        OUTPUT="${2:-/tmp/fetch_result.md}"
        if [ -z "$URL" ]; then
            echo "用法: $0 smart <URL> [输出文件]" >&2
            echo "或:   $0 search <关键词> [数量]" >&2
            echo "或:   $0 sitemap <url> [最大条数]" >&2
            exit 1
        fi
        echo "=== Smart3W 智能抓取 ==="
        echo "目标: $URL"
        echo ""

        # 步骤1: 尝试 HTTP 抓取
        echo "[1/2] 尝试 HTTP 抓取..."
        if scrapling extract get "$URL" "$OUTPUT" 2>/dev/null && [ -s "$OUTPUT" ]; then
            echo "✅ HTTP 抓取成功 → $OUTPUT ($(wc -c < "$OUTPUT") bytes)"
            exit 0
        fi

        # 步骤2: 尝试隐身浏览器（反爬保护）
        echo "[2/2] 尝试隐身浏览器抓取..."
        if scrapling extract stealthy-fetch "$URL" "$OUTPUT" --headless 2>/dev/null && [ -s "$OUTPUT" ]; then
            echo "✅ 隐身抓取成功 → $OUTPUT ($(wc -c < "$OUTPUT") bytes)"
            exit 0
        fi

        echo "❌ 所有方式均失败"
        exit 1
        ;;
esac
