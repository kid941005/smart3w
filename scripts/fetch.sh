#!/bin/bash
# =============================================================================
# Smart3W - 智能网页搜索与抓取工具
# =============================================================================
# 策略优先级：curl → scrapling → scrapling-stealthy
# 设计原则：最可靠的方案最先尝试，复杂方案留作最后手段
# =============================================================================

set -u

ACTION="${1:-smart}"
shift 2>/dev/null || true

SEARXNG_INSTANCE="${SEARXNG_INSTANCE:-https://searxng.hqgg.top:59826}"

# 默认参数
COMPRESS=1
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT_CURL=15
TIMEOUT_SCRAPLING=10
TIMEOUT_STEALTHY=20
RETRY=2

# 解析参数
REMOTE_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-compress) COMPRESS=0; shift ;;
        --ua|--user-agent) USER_AGENT="$2"; shift 2 ;;
        --timeout) TIMEOUT_CURL="$2"; shift 2 ;;
        --retry) RETRY="$2"; shift 2 ;;
        --) shift; REMOTE_ARGS+=("$@"); break ;;
        -*) echo "未知参数: $1"; exit 1 ;;
        *) REMOTE_ARGS+=("$1"); shift ;;
    esac
done
set -- "${REMOTE_ARGS[@]}"

# =============================================================================
# 辅助函数
# =============================================================================

# 日志输出
_log() { echo "[$(date '+%H:%M:%S')] $1"; }
_info() { echo "ℹ️  $1"; }
_warn() { echo "⚠️  $1"; }
_err() { echo "❌ $1"; }
_ok() { echo "✅ $1"; }

# 创建唯一临时文件
_make_temp() {
    mktemp /tmp/smart3w_XXXXXX_$(date +%s).html
}

# 检查文件是否有效（非空且可读）
_is_valid_file() {
    [[ -s "$1" ]] && [[ -r "$1" ]]
}

# =============================================================================
# 策略1: curl 抓取（最可靠，适用于 95% 网站）
# =============================================================================
_fetch_by_curl() {
    local url="$1"
    local output="$2"
    local attempt=1
    
    _info "策略1: curl 抓取..."
    
    while [[ $attempt -le $RETRY ]]; do
        [[ $attempt -gt 1 ]] && _info "  重试 ($attempt/$RETRY)..."
        
        # curl 抓取，带完整请求头和压缩支持
        curl -L -s --compressed \
            -A "$USER_AGENT" \
            -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" \
            -H "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8" \
            -H "Accept-Encoding: gzip, deflate, br" \
            -H "Connection: keep-alive" \
            -H "Cache-Control: no-cache" \
            --connect-timeout 10 \
            --max-time "$TIMEOUT_CURL" \
            -o "$output" \
            "$url" 2>/dev/null
        
        if _is_valid_file "$output"; then
            local size=$(wc -c < "$output")
            _ok "  curl 成功 (${size}B)"
            return 0
        fi
        
        attempt=$((attempt + 1))
        [[ $attempt -le $RETRY ]] && sleep 1
    done
    
    return 1
}

# =============================================================================
# 策略2: scrapling HTTP 抓取（处理需要 JS 渲染的页面）
# =============================================================================
_fetch_by_scrapling() {
    local url="$1"
    local output="$2"
    local attempt=1
    
    _info "策略2: scrapling HTTP..."
    
    while [[ $attempt -le $RETRY ]]; do
        [[ $attempt -gt 1 ]] && _info "  重试 ($attempt/$RETRY)..."
        
        if timeout "$TIMEOUT_SCRAPLING" scrapling extract get "$url" "$output" 2>/dev/null && \
           _is_valid_file "$output"; then
            local size=$(wc -c < "$output")
            _ok "  scrapling HTTP 成功 (${size}B)"
            return 0
        fi
        
        attempt=$((attempt + 1))
        [[ $attempt -le $RETRY ]] && sleep 1
    done
    
    return 1
}

# =============================================================================
# 策略3: scrapling stealthy（绕过 Cloudflare 等反爬）
# =============================================================================
_fetch_by_stealthy() {
    local url="$1"
    local output="$2"
    local attempt=1
    
    _info "策略3: scrapling stealthy (绕过反爬)..."
    
    while [[ $attempt -le $RETRY ]]; do
        [[ $attempt -gt 1 ]] && _info "  重试 ($attempt/$RETRY)..."
        
        if timeout "$TIMEOUT_STEALTHY" scrapling extract stealthy-fetch "$url" "$output" \
            --headless --solve-cloudflare 2>/dev/null && \
           _is_valid_file "$output"; then
            local size=$(wc -c < "$output")
            _ok "  scrapling stealthy 成功 (${size}B)"
            return 0
        fi
        
        attempt=$((attempt + 1))
        [[ $attempt -le $RETRY ]] && sleep 2
    done
    
    return 1
}

# =============================================================================
# 内容压缩: readability-lxml 提取正文
# =============================================================================
_compress_html() {
    local input="$1"
    local output="$2"
    
    python3 -c "
import sys, re
from readability import Document

try:
    with open('$input', 'r', encoding='utf-8', errors='ignore') as f:
        html = f.read()
    
    if not html.strip():
        sys.exit(1)
    
    doc = Document(html)
    summary = doc.summary()
    text = re.sub(r'<[^>]+>', '', summary)
    text = re.sub(r'\s+', ' ', text).strip()
    
    if text:
        with open('$output', 'w', encoding='utf-8') as f:
            f.write(text)
        print('OK')
    else:
        sys.exit(1)
except Exception as e:
    sys.exit(1)
" 2>/dev/null
}

# =============================================================================
# 主抓取逻辑（策略循环）
# =============================================================================
do_fetch() {
    local url="$1"
    local output="$2"
    local method_used=""
    
    _log "开始抓取: $url"
    
    # 临时文件
    local temp_raw=$(_make_temp)
    local temp_compressed=$(_make_temp)
    
    # 策略1: curl (最可靠)
    if _fetch_by_curl "$url" "$temp_raw"; then
        method_used="curl"
    # 策略2: scrapling HTTP
    elif _fetch_by_scrapling "$url" "$temp_raw"; then
        method_used="scrapling-http"
    # 策略3: scrapling stealthy
    elif _fetch_by_stealthy "$url" "$temp_raw"; then
        method_used="scrapling-stealthy"
    else
        _err "所有策略均失败"
        rm -f "$temp_raw" "$temp_compressed"
        return 1
    fi
    
    # 内容压缩
    if [[ $COMPRESS -eq 1 ]]; then
        if _compress_html "$temp_raw" "$temp_compressed"; then
            local raw_size=$(wc -c < "$temp_raw")
            local compressed_size=$(wc -c < "$temp_compressed")
            local ratio=$(( compressed_size * 100 / raw_size ))
            mv "$temp_compressed" "$output"
            rm -f "$temp_raw"
            echo ""
            _ok "抓取完成 → $output"
            echo "   原始: ${raw_size}B | 压缩后: ${compressed_size}B | 保留: ${ratio}%"
        else
            _warn "压缩失败，保留原始内容"
            mv "$temp_raw" "$output"
            rm -f "$temp_compressed"
            echo ""
            _ok "抓取完成(原始) → $output"
        fi
    else
        mv "$temp_raw" "$output"
        rm -f "$temp_compressed"
        echo ""
        _ok "抓取完成(原始) → $output"
    fi
    
    echo "   方法: $method_used"
    return 0
}

# =============================================================================
# 网页搜索 (SearXNG)
# =============================================================================
do_search() {
    local query="$1"
    local count="${2:-10}"
    
    if [[ -z "$query" ]]; then
        echo "用法: $0 search <关键词> [数量]" >&2
        exit 1
    fi
    
    _info "搜索: $query (SearXNG)"
    
    python3 - "$query" "$count" "$SEARXNG_INSTANCE" << 'PYEOF'
import sys, json, urllib.request, urllib.parse, ssl

query = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
instance = sys.argv[3] if len(sys.argv) > 3 else "https://searxng.hqgg.top:59826"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

params = {"q": query, "format": "json", "language": "zh-CN"}
url = f"{instance}/search?{urllib.parse.urlencode(params)}"

try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        results = []
        for r in data.get("results", [])[:count]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")
            })
        print(json.dumps({
            "success": True, "query": query,
            "results": results, "result_count": len(results)
        }, ensure_ascii=False, indent=2))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "query": query}))
PYEOF
}

# =============================================================================
# Sitemap 解析
# =============================================================================
do_sitemap() {
    local sitemap_url="$1"
    local max_urls="${2:-50}"
    
    if [[ -z "$sitemap_url" ]]; then
        echo "用法: $0 sitemap <sitemap_url> [最大条数]" >&2
        exit 1
    fi
    
    _info "解析 Sitemap: $sitemap_url"
    
    python3 - "$sitemap_url" "$max_urls" << 'PYEOF'
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

    print(f"✅ 共解析到 {len(urls)} 个URL\n")
    for i, (lm, utype, u) in enumerate(urls[:max_urls]):
        prefix = "📋 " if utype == "index" else "  "
        m = f"[{lm}] " if lm else "          "
        print(f"{prefix}{i+1:3d}. {m}{u}")

    if len(urls) > max_urls:
        print(f"\n... 还有 {len(urls) - max_urls} 个URL")

except Exception as e:
    print(f"❌ 解析失败: {e}")
PYEOF
}

# =============================================================================
# 主入口
# =============================================================================

case "$ACTION" in
    search)
        query="${REMOTE_ARGS[0]:-}"
        count="${REMOTE_ARGS[1]:-10}"
        do_search "$query" "$count"
        ;;
    
    sitemap)
        url="${REMOTE_ARGS[0]:-}"
        max="${REMOTE_ARGS[1]:-50}"
        do_sitemap "$url" "$max"
        ;;
    
    get|fetch|stealthy|smart)
        url="${REMOTE_ARGS[0]:-}"
        output="${REMOTE_ARGS[1]:-/tmp/fetch_result.md}"
        
        if [[ -z "$url" ]]; then
            echo "用法: $0 get|smart <URL> [输出文件] [--no-compress] [--ua 'UA'] [--timeout N] [--retry N]" >&2
            echo "       $0 search <关键词> [数量]" >&2
            echo "       $0 sitemap <url> [最大条数]" >&2
            exit 1
        fi
        
        do_fetch "$url" "$output"
        ;;
    
    help|--help|-h)
        echo "Smart3W - 智能网页搜索与抓取工具"
        echo ""
        echo "用法:"
        echo "  $0 search <关键词> [数量]        SearXNG 搜索"
        echo "  $0 get <URL> [输出文件]          HTTP 抓取（curl 优先）"
        echo "  $0 smart <URL> [输出文件]        智能抓取（自动选择最佳策略）"
        echo "  $0 fetch <URL> [输出文件]        浏览器渲染抓取"
        echo "  $0 stealthy <URL> [输出文件]    绕过反爬抓取"
        echo "  $0 sitemap <url> [最大条数]      Sitemap 解析"
        echo ""
        echo "参数:"
        echo "  --no-compress    跳过内容压缩，获取原始 HTML"
        echo "  --ua 'UA'        自定义 User-Agent"
        echo "  --timeout N      curl 超时时间（秒）"
        echo "  --retry N        失败重试次数"
        ;;
    
    *)
        echo "未知命令: $ACTION" >&2
        echo "使用 '$0 help' 查看帮助" >&2
        exit 1
        ;;
esac
