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
        --check-search) REMOTE_ARGS+=("$1"); shift ;;
        --ua|--user-agent) USER_AGENT="$2"; shift 2 ;;
        --timeout)
            [[ $# -ge 2 ]] || { echo "--timeout 需要一个正整数参数" >&2; exit 1; }
            [[ "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--timeout 必须为正整数" >&2; exit 1; }
            TIMEOUT_CURL="$2"
            TIMEOUT_SCRAPLING="$2"
            TIMEOUT_STEALTHY="$2"
            shift 2
            ;;
        --retry)
            [[ $# -ge 2 ]] || { echo "--retry 需要一个正整数参数" >&2; exit 1; }
            [[ "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--retry 必须为正整数" >&2; exit 1; }
            RETRY="$2"
            shift 2
            ;;
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
        
        if timeout "$TIMEOUT_SCRAPLING" scrapling extract fetch "$url" "$output" --real-chrome 2>/dev/null && \
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
            --real-chrome --headless --solve-cloudflare 2>/dev/null && \
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
# 内容压缩: 依赖检查
# =============================================================================
_check_compress_dependencies() {
    python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
mods = ('readability', 'bs4')
missing = [m for m in mods if importlib.util.find_spec(m) is None]
sys.exit(0 if not missing else 1)
PY
}

_compress_dependency_hint() {
    _warn "压缩依赖缺失，需安装: readability-lxml beautifulsoup4"
}

_doctor_check_cmd() {
    local cmd="$1"
    if command -v "$cmd" >/dev/null 2>&1; then
        _ok "$cmd 已安装"
        return 0
    fi
    _err "$cmd 未安装"
    return 1
}

_doctor_check_python_module() {
    local module="$1"
    if python3 - <<PY >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('$module') else 1)
PY
    then
        _ok "Python 模块 $module 已安装"
        return 0
    fi
    _err "Python 模块 $module 未安装"
    return 1
}

_doctor_check_file() {
    local path="$1"
    if [[ -x "$path" ]]; then
        _ok "$path 存在且可执行"
        return 0
    fi
    _err "$path 不存在或不可执行"
    return 1
}

_doctor() {
    local failed=0
    local check_search=0

    [[ "${1:-}" == "--check-search" ]] && check_search=1

    _doctor_check_cmd curl || failed=1
    _doctor_check_cmd python3 || failed=1
    _doctor_check_cmd scrapling || failed=1
    _doctor_check_python_module readability || failed=1
    _doctor_check_python_module bs4 || failed=1
    _doctor_check_file /opt/google/chrome/chrome || failed=1

    if [[ $check_search -eq 1 ]]; then
        if curl -fsS --max-time 10 "$SEARXNG_INSTANCE/search?q=smart3w&format=json" >/dev/null 2>&1; then
            _ok "SEARXNG_INSTANCE 可连通"
        else
            _err "SEARXNG_INSTANCE 不可连通"
            failed=1
        fi
    fi

    if [[ $failed -eq 0 ]]; then
        _ok "doctor 检查通过"
        return 0
    fi

    _err "doctor 检查失败"
    return 1
}

# =============================================================================
# 内容压缩: readability-lxml 提取正文
# =============================================================================
_compress_html() {
    local input="$1"
    local output="$2"
    
    python3 -c "
import sys
from readability import Document
from bs4 import BeautifulSoup

try:
    with open('$input', 'r', encoding='utf-8', errors='ignore') as f:
        html = f.read()
    
    if not html.strip():
        sys.exit(1)
    
    doc = Document(html)
    title = doc.title().strip()
    summary = doc.summary()
    soup = BeautifulSoup(summary, 'html.parser')

    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()

    parts = []
    if title:
        parts.append('# ' + title)

    for node in soup.find_all(['p', 'section', 'blockquote', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'img']):
        if node.name == 'img':
            src = node.get('src') or node.get('data-src') or node.get('data-original')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                parts.append(f'![]({src})')
            continue

        text = node.get_text(separator=' ', strip=True)
        if text:
            if node.name == 'h1':
                parts.append('# ' + text)
            elif node.name == 'h2':
                parts.append('## ' + text)
            elif node.name == 'h3':
                parts.append('### ' + text)
            elif node.name == 'h4':
                parts.append('#### ' + text)
            else:
                parts.append(text)

        for img in node.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-original')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                parts.append(f'![]({src})')

    lines = []
    seen_images = set()
    prev = None
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith('![]('):
            if part in seen_images:
                continue
            seen_images.add(part)
        elif part == prev:
            continue
        lines.append(part)
        prev = part

    text = '\n\n'.join(lines).strip()
    if text:
        with open('$output', 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        print('OK')
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# =============================================================================
# 内容压缩: 微信文章专用（BeautifulSoup + js_content）
# =============================================================================
_compress_wechat() {
    local input="$1"
    local output="$2"
    
    python3 -c "
import sys
from bs4 import BeautifulSoup

try:
    with open('$input', 'r', encoding='utf-8', errors='ignore') as f:
        html = f.read()
    
    if not html.strip():
        sys.exit(1)
    
    soup = BeautifulSoup(html, 'html.parser')
    
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()
    
    content_div = soup.find('div', id='js_content')
    if not content_div:
        content_div = soup.find('div', class_='rich_media_content')
    if not content_div:
        sys.exit(1)

    parts = []
    for node in content_div.find_all(['p', 'section', 'blockquote', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'img']):
        if node.name == 'img':
            src = node.get('data-src') or node.get('src') or node.get('data-original')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                parts.append(f'![]({src})')
            continue

        text = ''
        if node.name == 'p':
            text = node.get_text(separator=' ', strip=True)
        elif node.name == 'section':
            direct_ps = node.find_all('p', recursive=False)
            if direct_ps:
                for p in direct_ps:
                    p_text = p.get_text(separator=' ', strip=True)
                    if p_text:
                        parts.append(p_text)
            elif node.find('img') and not node.get_text(strip=True):
                pass
            else:
                text = node.get_text(separator=' ', strip=True)
        else:
            text = node.get_text(separator=' ', strip=True)

        if text:
            parts.append(text)

        for img in node.find_all('img', recursive=False):
            src = img.get('data-src') or img.get('src') or img.get('data-original')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                parts.append(f'![]({src})')

    lines = []
    seen_images = set()
    prev = None
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith('![]('):
            if part in seen_images:
                continue
            seen_images.add(part)
        elif part == prev:
            continue
        lines.append(part)
        prev = part

    text = '\n\n'.join(lines).strip()
    if text and len(text) > 50:
        with open('$output', 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        print('OK_WECHAT')
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# 内容输出收尾：压缩成功
_finalize_compressed_output() {
    local raw_file="$1"
    local compressed_file="$2"
    local output="$3"

    local raw_size=$(wc -c < "$raw_file")
    local compressed_size=$(wc -c < "$compressed_file")
    local ratio=$(( compressed_size * 100 / raw_size ))

    mv "$compressed_file" "$output"
    rm -f "$raw_file"
    echo ""
    _ok "抓取完成 → $output"
    echo "   原始: ${raw_size}B | 压缩后: ${compressed_size}B | 保留: ${ratio}%"
}

# 内容输出收尾：保留原始内容
_finalize_raw_output() {
    local raw_file="$1"
    local extra_file="$2"
    local output="$3"
    local warn_message="${4:-}"

    [[ -n "$warn_message" ]] && _warn "$warn_message"
    mv "$raw_file" "$output"
    rm -f "$extra_file"
    echo ""
    _ok "抓取完成(原始) → $output"
}

# 抓取失败收尾：清理并返回失败
_fail_fetch() {
    local raw_file="$1"
    local extra_file="$2"
    local error_message="$3"

    _err "$error_message"
    rm -f "$raw_file" "$extra_file"
    return 1
}

# 按模式执行抓取策略
_run_fetch_mode() {
    local mode="$1"
    local url="$2"
    local raw_file="$3"
    local result_var="$4"
    local method_name=""

    case "$mode" in
        get)
            _fetch_by_curl "$url" "$raw_file" && method_name="curl"
            ;;
        fetch)
            _fetch_by_scrapling "$url" "$raw_file" && method_name="scrapling-http"
            ;;
        stealthy)
            _fetch_by_stealthy "$url" "$raw_file" && method_name="scrapling-stealthy"
            ;;
        smart)
            if _fetch_by_curl "$url" "$raw_file"; then
                method_name="curl"
            elif _fetch_by_scrapling "$url" "$raw_file"; then
                method_name="scrapling-http"
            elif _fetch_by_stealthy "$url" "$raw_file"; then
                method_name="scrapling-stealthy"
            fi
            ;;
        *)
            return 2
            ;;
    esac

    [[ -n "$method_name" ]] || return 1
    printf -v "$result_var" '%s' "$method_name"
    return 0
}

# =============================================================================
# 主抓取逻辑（按命令选择策略）
# =============================================================================
do_fetch() {
    local mode="$1"
    local url="$2"
    local output="$3"
    local method_used=""
    local run_status=0
    
    _log "开始抓取[$mode]: $url"
    
    # 临时文件
    local temp_raw=$(_make_temp)
    local temp_compressed=$(_make_temp)
    
    _run_fetch_mode "$mode" "$url" "$temp_raw" method_used
    run_status=$?

    if [[ $run_status -ne 0 ]]; then
        case "$mode" in
            get)
                _fail_fetch "$temp_raw" "$temp_compressed" "get 模式抓取失败"
                ;;
            fetch)
                _fail_fetch "$temp_raw" "$temp_compressed" "fetch 模式抓取失败"
                ;;
            stealthy)
                _fail_fetch "$temp_raw" "$temp_compressed" "stealthy 模式抓取失败"
                ;;
            smart)
                _fail_fetch "$temp_raw" "$temp_compressed" "smart 模式所有策略均失败"
                ;;
            *)
                _fail_fetch "$temp_raw" "$temp_compressed" "未知抓取模式: $mode"
                ;;
        esac
    fi
    
    # 内容压缩
    if [[ $COMPRESS -eq 1 ]]; then
        if ! _check_compress_dependencies; then
            _compress_dependency_hint
            _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" "压缩已跳过，保留原始内容"
            echo "   方法: $method_used"
            return 0
        fi

        # 检测是否为微信文章
        if [[ "$url" == *"mp.weixin.qq.com"* ]]; then
            _info "检测到微信文章，使用微信专用提取..."
            if _compress_wechat "$temp_raw" "$temp_compressed"; then
                _finalize_compressed_output "$temp_raw" "$temp_compressed" "$output"
            elif _compress_html "$temp_raw" "$temp_compressed"; then
                _finalize_compressed_output "$temp_raw" "$temp_compressed" "$output"
            else
                _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" "压缩失败，保留原始内容"
            fi
        else
            # 普通网页：先尝试 readability
            if _compress_html "$temp_raw" "$temp_compressed"; then
                _finalize_compressed_output "$temp_raw" "$temp_compressed" "$output"
            else
                _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" "压缩失败，保留原始内容"
            fi
        fi
    else
        _finalize_raw_output "$temp_raw" "$temp_compressed" "$output"
    fi
    
    echo "   方法: $method_used"
    return 0
}

# =============================================================================
# smoke test
# =============================================================================
_do_smoke() {
    local output="./smoke_output.md"
    rm -f "$output"

    do_fetch "get" "https://example.com" "$output" >/dev/null 2>&1 || {
        _err "smoke 失败：抓取未成功"
        rm -f "$output"
        return 1
    }

    if [[ -s "$output" ]]; then
        rm -f "$output"
        echo "SMOKE_OK"
        return 0
    fi

    _err "smoke 失败：输出文件为空"
    rm -f "$output"
    return 1
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
        [[ "$count" =~ ^[1-9][0-9]*$ ]] || { echo "search 数量必须为正整数" >&2; exit 1; }
        do_search "$query" "$count"
        ;;
    
    sitemap)
        url="${REMOTE_ARGS[0]:-}"
        max="${REMOTE_ARGS[1]:-50}"
        [[ "$max" =~ ^[1-9][0-9]*$ ]] || { echo "sitemap 最大条数必须为正整数" >&2; exit 1; }
        do_sitemap "$url" "$max"
        ;;

    doctor)
        if [[ "${REMOTE_ARGS[0]:-}" == "--check-search" ]]; then
            _doctor --check-search
        else
            _doctor
        fi
        ;;

    smoke)
        _do_smoke
        ;;
    
    get|fetch|stealthy|smart)
        url="${REMOTE_ARGS[0]:-}"
        output="${REMOTE_ARGS[1]:-./fetch_result.md}"
        
        if [[ -z "$url" ]]; then
            echo "用法: $0 get|fetch|stealthy|smart <URL> [输出文件] [--no-compress] [--ua 'UA'] [--timeout N] [--retry N]" >&2
            echo "       $0 search <关键词> [数量]" >&2
            echo "       $0 sitemap <url> [最大条数]" >&2
            exit 1
        fi
        
        do_fetch "$ACTION" "$url" "$output"
        ;;
    
    help|--help|-h)
        echo "Smart3W - 智能网页搜索与抓取工具"
        echo ""
        echo "用法:"
        echo "  $0 search <关键词> [数量]        SearXNG 搜索"
        echo "  $0 get <URL> [输出文件]          仅使用 curl 轻量 HTTP 抓取"
        echo "  $0 fetch <URL> [输出文件]        仅使用 scrapling HTTP/渲染型抓取"
        echo "  $0 stealthy <URL> [输出文件]    仅使用 scrapling stealthy 反爬抓取"
        echo "  $0 smart <URL> [输出文件]        自动按 curl → scrapling HTTP → stealthy 降级"
        echo "  $0 sitemap <url> [最大条数]      Sitemap 解析"
        echo "  $0 doctor [--check-search]       检查运行依赖与环境"
        echo "  $0 smoke                         最小抓取 smoke test"
        echo ""
        echo "参数:"
        echo "  --no-compress    跳过内容压缩，获取原始 HTML"
        echo "  --ua 'UA'        自定义 User-Agent"
        echo "  --timeout N      所有抓取模式的超时时间（秒）"
        echo "  --retry N        失败重试次数（正整数）"
        ;;
    
    *)
        echo "未知命令: $ACTION" >&2
        echo "使用 '$0 help' 查看帮助" >&2
        exit 1
        ;;
esac
