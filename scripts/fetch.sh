#!/bin/bash
# smart3w 智能抓取脚本
# 用法:
#   ./fetch.sh search <关键词> [数量]           SearXNG 搜索
#   ./fetch.sh get <URL> [输出文件] [--no-compress]   快速抓取（HTTP）
#   ./fetch.sh fetch <URL> [输出文件] [--no-compress] 动态页面（浏览器渲染）
#   ./fetch.sh stealthy <URL> [输出文件] [--no-compress] 绕过反爬
#   ./fetch.sh sitemap <url> [最大条数]           Sitemap 索引解析
#   ./fetch.sh smart <URL> [输出文件] [--no-compress] 智能选择（默认）

ACTION="${1:-smart}"
shift

SEARXNG_INSTANCE="${SEARXNG_INSTANCE:-https://searxng.example.com:59826}"

# 默认启用压缩
COMPRESS=1
REMAINING=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-compress) COMPRESS=0; shift ;;
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
    # 提取 HTML 摘要，再去掉所有标签得纯文本
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
            echo "用法: $0 get <URL> [输出文件] [--no-compress]" >&2
            exit 1
        fi
        echo "=== HTTP 抓取 ==="
        echo "目标: $URL"
        TEMP_RAW="/tmp/smart3w_raw_$$.html"
        scrapling extract get "$URL" "$TEMP_RAW" 2>/dev/null
        RAW_BYTES=$(wc -c < "$TEMP_RAW" 2>/dev/null || echo 0)
        if [ -s "$TEMP_RAW" ]; then
            if [ "$COMPRESS" -eq 1 ]; then
                # 尝试压缩，结果写入 OUTPUT；成功则删除 RAW
                if _compress_html < "$TEMP_RAW" > "$OUTPUT" 2>/dev/null && [ -s "$OUTPUT" ]; then
                    rm -f "$TEMP_RAW"
                    _report "$RAW_BYTES" "$OUTPUT"
                else
                    # 压缩失败或为空，保留原始 HTML
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
            echo "用法: $0 fetch <URL> [输出文件] [--no-compress]" >&2
            exit 1
        fi
        echo "=== 浏览器渲染抓取 ==="
        echo "目标: $URL"
        TEMP_RAW="/tmp/smart3w_raw_$$.html"
        scrapling extract fetch "$URL" "$TEMP_RAW" --headless 2>/dev/null
        RAW_BYTES=$(wc -c < "$TEMP_RAW" 2>/dev/null || echo 0)
        if [ -s "$TEMP_RAW" ]; then
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
            echo "用法: $0 stealthy <URL> [输出文件] [--no-compress]" >&2
            exit 1
        fi
        echo "=== 隐身浏览器抓取（绕过反爬）==="
        echo "目标: $URL"
        TEMP_RAW="/tmp/smart3w_raw_$$.html"
        scrapling extract stealthy-fetch "$URL" "$TEMP_RAW" --headless --solve-cloudflare 2>/dev/null
        RAW_BYTES=$(wc -c < "$TEMP_RAW" 2>/dev/null || echo 0)
        if [ -s "$TEMP_RAW" ]; then
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
            echo "用法: $0 smart <URL> [输出文件] [--no-compress]" >&2
            echo "或:   $0 search <关键词> [数量]" >&2
            echo "或:   $0 sitemap <url> [最大条数]" >&2
            exit 1
        fi
        echo "=== Smart3W 智能抓取（并行竞争）==="
        echo "目标: $URL"
        echo ""

        # Cloudflare 预检测（快速请求，约0.5s）
        # 判断：__cfduid/cf-mitigations 出现必然需要解CF；cf-ray 存在时非HIT缓存也需要
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

        echo -n "[0/3] 检测 Cloudflare... "
        if [ "$(_has_cf)" = "1" ]; then
            echo "检测到 ✅"
            CF_MODE=1
        else
            echo "未发现"
            CF_MODE=0
        fi
        echo ""

        # 并行竞速：HTTP（快）与隐身浏览器（稳）同时启动
        LOCKFILE="/tmp/smart3w_lock_$$"
        RESULT1="/tmp/smart3w_res1_$$.html"
        RESULT2="/tmp/smart3w_res2_$$.html"

        _finish() {
            # 子进程调用：标记策略类型到锁文件
            echo "$1" > "$LOCKFILE"
        }

        # 启动 HTTP 策略（5s 超时）
        (
            timeout 5 scrapling extract get "$URL" "$RESULT1" 2>/dev/null && \
            [ -s "$RESULT1" ] && _finish "http"
        ) &
        PID1=$!

        # 启动隐身策略（有CF时解CF，无CF时跳过解CF节省时间）
        if [ "$CF_MODE" = "1" ]; then
            STEALTHY_TIMEOUT=15
            STEALTHY_FLAGS="--headless --solve-cloudflare"
        else
            STEALTHY_TIMEOUT=10
            STEALTHY_FLAGS="--headless"
        fi
        (
            timeout "$STEALTHY_TIMEOUT" scrapling extract stealthy-fetch "$URL" "$RESULT2" $STEALTHY_FLAGS 2>/dev/null && \
            [ -s "$RESULT2" ] && _finish "stealthy"
        ) &
        PID2=$!

        # 等待第一个完成者
        WINNER=""
        while true; do
            if [ -s "$LOCKFILE" ]; then
                WINNER=$(cat "$LOCKFILE")
                break
            fi
            # 两个都死了也退出
            if ! kill -0 "$PID1" 2>/dev/null && ! kill -0 "$PID2" 2>/dev/null; then
                break
            fi
            sleep 0.2
        done

        # 杀掉另一个进程
        kill "$PID1" "$PID2" 2>/dev/null
        wait "$PID1" "$PID2" 2>/dev/null

        # 清理锁文件
        rm -f "$LOCKFILE"

        if [ -z "$WINNER" ]; then
            rm -f "$RESULT1" "$RESULT2"
            # 两个策略都失败了，CF模式下再做最后一次尝试（显式解CF）
            if [ "$CF_MODE" = "1" ]; then
                echo "[最终手段] 尝试强制解 Cloudflare..."
                FINAL="/tmp/smart3w_final_$$.html"
                if timeout 20 scrapling extract stealthy-fetch "$URL" "$FINAL" --headless --solve-cloudflare 2>/dev/null && [ -s "$FINAL" ]; then
                    RAW_BYTES=$(wc -c < "$FINAL")
                    if [ "$COMPRESS" -eq 1 ] && _compress_html < "$FINAL" > "$OUTPUT" 2>/dev/null && [ -s "$OUTPUT" ]; then
                        rm -f "$FINAL"
                        echo "🏁 Cloudflare强制破解成功"
                        _report "$RAW_BYTES" "$OUTPUT"
                    else
                        [ -f "$OUTPUT" ] || mv "$FINAL" "$OUTPUT"
                        rm -f "$FINAL"
                        echo "🏁 Cloudflare强制破解成功（原始）"
                        _report 0 "$OUTPUT"
                    fi
                    exit 0
                fi
                rm -f "$FINAL"
            fi
            echo "❌ 所有方式均失败"
            exit 1
        fi

        # 胜出文件
        case "$WINNER" in
            http)    RAW_FILE="$RESULT1"; LABEL="HTTP 抓取"   ;;
            stealthy) RAW_FILE="$RESULT2"; LABEL="隐身浏览器" ;;
        esac

        # 压缩
        if [ "$COMPRESS" -eq 1 ]; then
            if _compress_html < "$RAW_FILE" > "$OUTPUT" 2>/dev/null && [ -s "$OUTPUT" ]; then
                RAW_BYTES=$(wc -c < "$RAW_FILE")
                rm -f "$RAW_FILE" "$RESULT1" "$RESULT2"
                echo "🏁 ${LABEL}胜出"
                _report "$RAW_BYTES" "$OUTPUT"
            else
                mv "$RAW_FILE" "$OUTPUT"
                rm -f "$RESULT1" "$RESULT2"
                echo "🏁 ${LABEL}胜出（压缩降级）"
                _report 0 "$OUTPUT"
            fi
        else
            mv "$RAW_FILE" "$OUTPUT"
            rm -f "$RESULT1" "$RESULT2"
            echo "🏁 ${LABEL}胜出"
            _report 0 "$OUTPUT"
        fi
        exit 0
        ;;
esac
