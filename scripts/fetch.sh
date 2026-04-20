#!/bin/bash
# smart3w 智能抓取脚本 (优化版)
# 优化内容：
#   - 增加 curl fallback 降级机制
#   - 优化临时文件命名避免冲突
#   - 增加重试机制
#   - 支持自定义 User-Agent
# 用法:
#   ./fetch.sh search <关键词> [数量]           SearXNG 搜索
#   ./fetch.sh get <URL> [输出文件] [--no-compress]   快速抓取（HTTP）
#   ./fetch.sh fetch <URL> [输出文件] [--no-compress] 动态页面（浏览器渲染）
#   ./fetch.sh stealthy <URL> [输出文件] [--no-compress] 绕过反爬
#   ./fetch.sh sitemap <url> [最大条数]           Sitemap 索引解析
#   ./fetch.sh smart <URL> [输出文件] [--no-compress] 智能选择（默认）

ACTION="${1:-smart}"
shift

SEARXNG_INSTANCE="${SEARXNG_INSTANCE:-https://searxng.hqgg.top:59826}"

# 默认启用压缩
COMPRESS=1
# 默认 User-Agent
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
RETRY=2

REMAINING=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-compress) COMPRESS=0; shift ;;
        --ua) USER_AGENT="$2"; shift 2 ;;
        --retry) RETRY="$2"; shift 2 ;;
        *)             REMAINING+=("$1"); shift ;;
    esac
done
set -- "${REMAINING[@]}"

# ----------------------------------------
# 正文压缩：用 readability-lxml 提取干净文本
# ----------------------------------------
_compress_html() {
    python3 -c "
import sys, re
from readability import Document
html = sys.stdin.read()
if not html.strip():
    sys.exit(1)
try:
    doc = Document(html)
    summary = doc.summary()
    text = re.sub(r'<[^>]+>', '', summary)
    text = re.sub(r'\s+', ' ', text).strip()
    if text:
        print(text)
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
"
}

# ----------------------------------------
# 压缩汇报：显示压缩前后大小
# ----------------------------------------
_report() {
    local raw_bytes=$1
    local file=$2
    local final_bytes
    final_bytes=$(wc -c < "$file")
    if [ "$raw_bytes" -gt 0 ]; then
        local ratio=$(( final_bytes * 100 / raw_bytes ))
        echo "✅ 抓取成功 → $file"
        echo "   原始: ${raw_bytes}B | 压缩后: ${final_bytes}B | 保留: ${ratio}%"
    else
        echo "✅ 成功 → $file (${final_bytes}B)"
    fi
}

# ----------------------------------------
# curl 降级抓取
# ----------------------------------------
_curl_fetch() {
    local url="$1"
    local output="$2"
    local attempt=1
    local max_attempts=${RETRY:-2}
    
    while [ $attempt -le $max_attempts ]; do
        if [ $attempt -gt 1 ]; then
            echo "   curl 重试 ($attempt/$max_attempts)..."
            sleep 1
        fi
        
        # curl 抓取，带完整 UA 和常见请求头
        curl -L -s -o "$output" \
            -A "$USER_AGENT" \
            -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" \
            -H "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8" \
            -H "Accept-Encoding: gzip, deflate, br" \
            --compressed \
            --connect-timeout 10 \
            --max-time 30 \
            "$url" 2>/dev/null
        
        if [ -s "$output" ]; then
            return 0
        fi
        
        attempt=$((attempt + 1))
    done
    
    return 1
}

# ----------------------------------------
# scrapling 抓取（带重试）
# ----------------------------------------
_scrapling_fetch() {
    local action="$1"
    local url="$2"
    local output="$3"
    local attempt=1
    local max_attempts=${RETRY:-2}
    
    while [ $attempt -le $max_attempts ]; do
        if [ $attempt -gt 1 ]; then
            echo "   scrapling 重试 ($attempt/$max_attempts)..."
            sleep 1
        fi
        
        case "$action" in
            get)
                scrapling extract get "$url" "$output" 2>/dev/null
                ;;
            fetch)
                scrapling extract fetch "$url" "$output" --headless 2>/dev/null
                ;;
            stealthy)
                scrapling extract stealthy-fetch "$url" "$output" --headless --solve-cloudflare 2>/dev/null
                ;;
        esac
        
        if [ -s "$output" ]; then
            return 0
        fi
        
        attempt=$((attempt + 1))
    done
    
    return 1
}

# ----------------------------------------
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
            echo "用法: $0 get <URL> [输出文件] [--no-compress] [--ua 'User-Agent'] [--retry N]" >&2
            exit 1
        fi
        
        echo "=== HTTP 抓取 ==="
        echo "目标: $URL"
        
        # 生成唯一临时文件
        TEMP_RAW="/tmp/smart3w_raw_$(date +%s)_${RANDOM}.html"
        
        # 策略1: scrapling
        echo "▶ 策略1: scrapling..."
        if _scrapling_fetch "get" "$URL" "$TEMP_RAW"; then
            RAW_BYTES=$(wc -c < "$TEMP_RAW")
            SUCCESS=1
        else
            RAW_BYTES=0
            SUCCESS=0
        fi
        
        # 策略2: curl 降级
        if [ "$SUCCESS" -eq 0 ]; then
            echo "⚠️ scrapling 失败，尝试 curl..."
            if _curl_fetch "$URL" "$TEMP_RAW"; then
                RAW_BYTES=$(wc -c < "$TEMP_RAW")
                SUCCESS=1
                echo "   ✅ curl 成功"
            fi
        fi
        
        if [ "$SUCCESS" -eq 1 ] && [ -s "$TEMP_RAW" ]; then
            if [ "$COMPRESS" -eq 1 ]; then
                if _compress_html < "$TEMP_RAW" > "$OUTPUT" 2>/dev/null && [ -s "$OUTPUT" ]; then
                    rm -f "$TEMP_RAW"
                    _report "$RAW_BYTES" "$OUTPUT"
                else
                    mv "$TEMP_RAW" "$OUTPUT"
                    _report 0 "$OUTPUT"
                fi
            else
                mv "$TEMP_RAW" "$OUTPUT"
                _report 0 "$OUTPUT"
            fi
        else
            rm -f "$TEMP_RAW"
            echo "❌ HTTP 抓取失败"
            exit 1
        fi
        ;;

    fetch)
        URL="$1"
        OUTPUT="${2:-/tmp/fetch_result.md}"
        if [ -z "$URL" ]; then
            echo "用法: $0 fetch <URL> [输出文件] [--no-compress] [--ua 'User-Agent'] [--retry N]" >&2
            exit 1
        fi
        echo "=== 浏览器渲染抓取 ==="
        echo "目标: $URL"
        
        TEMP_RAW="/tmp/smart3w_raw_$(date +%s)_${RANDOM}.html"
        
        echo "▶ 策略1: scrapling fetch..."
        if _scrapling_fetch "fetch" "$URL" "$TEMP_RAW"; then
            RAW_BYTES=$(wc -c < "$TEMP_RAW")
            SUCCESS=1
        else
            RAW_BYTES=0
            SUCCESS=0
        fi
        
        if [ "$SUCCESS" -eq 0 ]; then
            echo "⚠️ scrapling 失败，尝试 curl..."
            if _curl_fetch "$URL" "$TEMP_RAW"; then
                RAW_BYTES=$(wc -c < "$TEMP_RAW")
                SUCCESS=1
                echo "   ✅ curl 成功"
            fi
        fi
        
        if [ "$SUCCESS" -eq 1 ] && [ -s "$TEMP_RAW" ]; then
            if [ "$COMPRESS" -eq 1 ]; then
                if _compress_html < "$TEMP_RAW" > "$OUTPUT" 2>/dev/null && [ -s "$OUTPUT" ]; then
                    rm -f "$TEMP_RAW"
                    _report "$RAW_BYTES" "$OUTPUT"
                else
                    mv "$TEMP_RAW" "$OUTPUT"
                    _report 0 "$OUTPUT"
                fi
            else
                mv "$TEMP_RAW" "$OUTPUT"
                _report 0 "$OUTPUT"
            fi
        else
            rm -f "$TEMP_RAW"
            echo "❌ 浏览器渲染失败"
            exit 1
        fi
        ;;

    stealthy)
        URL="$1"
        OUTPUT="${2:-/tmp/fetch_result.md}"
        if [ -z "$URL" ]; then
            echo "用法: $0 stealthy <URL> [输出文件] [--no-compress] [--ua 'User-Agent'] [--retry N]" >&2
            exit 1
        fi
        echo "=== 隐身浏览器抓取（绕过反爬）==="
        echo "目标: $URL"
        
        TEMP_RAW="/tmp/smart3w_raw_$(date +%s)_${RANDOM}.html"
        
        echo "▶ 策略1: scrapling stealthy..."
        if _scrapling_fetch "stealthy" "$URL" "$TEMP_RAW"; then
            RAW_BYTES=$(wc -c < "$TEMP_RAW")
            SUCCESS=1
        else
            RAW_BYTES=0
            SUCCESS=0
        fi
        
        if [ "$SUCCESS" -eq 0 ]; then
            echo "⚠️ scrapling 失败，尝试 curl..."
            if _curl_fetch "$URL" "$TEMP_RAW"; then
                RAW_BYTES=$(wc -c < "$TEMP_RAW")
                SUCCESS=1
                echo "   ✅ curl 成功"
            fi
        fi
        
        if [ "$SUCCESS" -eq 1 ] && [ -s "$TEMP_RAW" ]; then
            if [ "$COMPRESS" -eq 1 ]; then
                if _compress_html < "$TEMP_RAW" > "$OUTPUT" 2>/dev/null && [ -s "$OUTPUT" ]; then
                    rm -f "$TEMP_RAW"
                    _report "$RAW_BYTES" "$OUTPUT"
                else
                    mv "$TEMP_RAW" "$OUTPUT"
                    _report 0 "$OUTPUT"
                fi
            else
                mv "$TEMP_RAW" "$OUTPUT"
                _report 0 "$OUTPUT"
            fi
        else
            rm -f "$TEMP_RAW"
            echo "❌ 隐身浏览器抓取失败"
            exit 1
        fi
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
    if "sitemapindex" in root.tag:
        for sm in root.findall(f"{SM}sitemap"):
            loc = sm.find(f"{SM}loc")
            if loc is not None and loc.text:
                urls.append(("index", "", loc.text))
        for sm in root.findall("sitemap"):
            loc = sm.find("loc")
            if loc is not None and loc.text and loc.text not in [u[2] for u in urls]:
                urls.append(("index", "", loc.text))
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
            echo "用法: $0 smart <URL> [输出文件] [--no-compress] [--ua 'User-Agent'] [--retry N]" >&2
            echo "或:   $0 search <关键词> [数量]" >&2
            echo "或:   $0 sitemap <url> [最大条数]" >&2
            exit 1
        fi
        echo "=== Smart3W 智能抓取（优化版）==="
        echo "目标: $URL"
        echo ""

        # Cloudflare 预检测
        _has_cf() {
            result=$(python3 -c "
import sys, urllib.request, ssl
url = sys.argv[1]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, context=ctx, timeout=3)
    h = {k.lower(): v for k, v in resp.headers.items()}
    if '__cfduid' in h or 'cf-mitigations' in h:
        print('1')
    elif 'cf-ray' in h:
        ccs = h.get('cf-cache-status', '').strip().upper()
        print('0' if ccs == 'HIT' else '1')
    else:
        print('0')
except:
    print('0')
" "$URL")
            echo "$result"
        }

        echo -n "[检测] Cloudflare... "
        if [ "$(_has_cf)" = "1" ]; then
            echo "检测到 ✅"
            CF_MODE=1
        else
            echo "未发现"
            CF_MODE=0
        fi

        # 生成唯一临时文件
        RANDOM_SUFFIX="${RANDOM}_$$"
        RESULT_HTTP="/tmp/smart3w_http_${RANDOM_SUFFIX}.html"
        RESULT_STEALTHY="/tmp/smart3w_stealthy_${RANDOM_SUFFIX}.html"
        RESULT_CURL="/tmp/smart3w_curl_${RANDOM_SUFFIX}.html"
        LOCKFILE="/tmp/smart3w_lock_${RANDOM_SUFFIX}"

        _finish() {
            echo "$1" > "$LOCKFILE"
        }

        SUCCESS=0

        # 策略1: scrapling HTTP (5s 超时)
        (
            timeout 5 scrapling extract get "$URL" "$RESULT_HTTP" 2>/dev/null && \
            [ -s "$RESULT_HTTP" ] && _finish "http"
        ) &
        PID1=$!

        # 策略2: scrapling stealthy
        if [ "$CF_MODE" = "1" ]; then
            STEALTHY_TIMEOUT=15
            STEALTHY_FLAGS="--headless --solve-cloudflare"
        else
            STEALTHY_TIMEOUT=10
            STEALTHY_FLAGS="--headless"
        fi
        (
            timeout "$STEALTHY_TIMEOUT" scrapling extract stealthy-fetch "$URL" "$RESULT_STEALTHY" $STEALTHY_FLAGS 2>/dev/null && \
            [ -s "$RESULT_STEALTHY" ] && _finish "stealthy"
        ) &
        PID2=$!

        # 等待第一个完成者
        WINNER=""
        while true; do
            if [ -s "$LOCKFILE" ]; then
                WINNER=$(cat "$LOCKFILE")
                break
            fi
            if ! kill -0 "$PID1" 2>/dev/null && ! kill -0 "$PID2" 2>/dev/null; then
                break
            fi
            sleep 0.2
        done

        # 杀掉未完成的进程
        kill "$PID1" "$PID2" 2>/dev/null
        wait "$PID1" "$PID2" 2>/dev/null

        # 选择结果
        RAW_FILE=""
        LABEL=""
        if [ -n "$WINNER" ]; then
            case "$WINNER" in
                http)    RAW_FILE="$RESULT_HTTP"; LABEL="scrapling-HTTP" ;;
                stealthy) RAW_FILE="$RESULT_STEALTHY"; LABEL="scrapling-stealthy" ;;
            esac
        fi

        # 如果 scrapling 都失败，尝试 curl
        if [ -z "$RAW_FILE" ] || [ ! -s "$RAW_FILE" ]; then
            echo "⚠️ scrapling 未成功，尝试 curl..."
            for attempt in 1 2 3; do
                if [ $attempt -gt 1 ]; then
                    echo "   curl 重试 ($attempt/3)..."
                    sleep 1
                fi
                
                curl -L -s -o "$RESULT_CURL" \
                    -A "$USER_AGENT" \
                    -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" \
                    -H "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8" \
                    -H "Accept-Encoding: gzip, deflate, br" \
                    --compressed \
                    --connect-timeout 10 \
                    --max-time 30 \
                    "$URL" 2>/dev/null
                
                if [ -s "$RESULT_CURL" ]; then
                    RAW_FILE="$RESULT_CURL"
                    LABEL="curl"
                    SUCCESS=1
                    echo "   ✅ curl 成功"
                    break
                fi
            done
        else
            SUCCESS=1
        fi

        # 清理锁文件和临时文件
        rm -f "$LOCKFILE" "$RESULT_HTTP" "$RESULT_STEALTHY"

        if [ "$SUCCESS" -eq 1 ] && [ -s "$RAW_FILE" ]; then
            if [ "$COMPRESS" -eq 1 ]; then
                if _compress_html < "$RAW_FILE" > "$OUTPUT" 2>/dev/null && [ -s "$OUTPUT" ]; then
                    RAW_BYTES=$(wc -c < "$RAW_FILE")
                    rm -f "$RAW_FILE" "$RESULT_CURL"
                    echo "🏁 ${LABEL}胜出"
                    _report "$RAW_BYTES" "$OUTPUT"
                else
                    mv "$RAW_FILE" "$OUTPUT"
                    rm -f "$RESULT_CURL"
                    echo "🏁 ${LABEL}胜出（压缩降级）"
                    _report 0 "$OUTPUT"
                fi
            else
                mv "$RAW_FILE" "$OUTPUT"
                rm -f "$RESULT_CURL"
                echo "🏁 ${LABEL}胜出"
                _report 0 "$OUTPUT"
            fi
        else
            rm -f "$RESULT_CURL"
            echo "❌ 所有方式均失败"
            exit 1
        fi
        exit 0
        ;;
esac
