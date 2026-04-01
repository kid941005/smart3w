#!/bin/bash
# smart3w 智能抓取脚本
# 用法:
#   ./fetch.sh search <关键词> [数量]   SearXNG 搜索
#   ./fetch.sh get <URL> [输出文件]     快速抓取（HTTP）
#   ./fetch.sh fetch <URL> [输出文件]   动态页面（浏览器渲染）
#   ./fetch.sh stealthy <URL> [输出文件] 绕过反爬
#   ./fetch.sh smart <URL> [输出文件]   智能选择（默认）

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

    smart|*)
        URL="$1"
        OUTPUT="${2:-/tmp/fetch_result.md}"
        if [ -z "$URL" ]; then
            echo "用法: $0 smart <URL> [输出文件]" >&2
            echo "或:   $0 search <关键词> [数量]" >&2
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
