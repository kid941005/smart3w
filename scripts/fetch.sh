#!/bin/bash
# =============================================================================
# Smart3W - 智能网页搜索与抓取工具
# =============================================================================
# 策略优先级：Python HTTP → curl → scrapling → scrapling-stealthy
# 设计原则：最可靠的方案最先尝试，复杂方案留作最后手段
# =============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTILS_PY="$SCRIPT_DIR/smart3w_utils.py"
PYTHON_BIN="${SMART3W_PYTHON:-python3}"

ACTION="${1:-smart}"
# 注意：这里只消费命令名一个参数；写成 "shift 2>/dev/null" 时，bash 会把
# "2>/dev/null" 解析为 stderr 重定向，实际等价于 shift 1（易误读，故显式写 1）。
shift 1 2>/dev/null || true

# 默认值与 smart3w_core.DEFAULT_INSTANCE 保持一致
SEARXNG_INSTANCE="${SEARXNG_INSTANCE:-https://searxng.hqgg.top:59826}"

# 默认参数
COMPRESS=1
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PROXY_URL="${SMART3W_PROXY_URL:-}"
TIMEOUT_CURL=15
TIMEOUT_SCRAPLING=10
TIMEOUT_STEALTHY=20
MAX_ATTEMPTS=2
EFFECTIVE_URL_FILE=""
FETCH_CONTENT_TYPE=""
DASH_SEPARATOR=0

# 解析参数
REMOTE_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-compress) COMPRESS=0; shift ;;
        --check-search) REMOTE_ARGS+=("$1"); shift ;;
        --ua|--user-agent)
            [[ $# -ge 2 ]] || { echo "--ua 需要一个参数" >&2; exit 1; }
            USER_AGENT="$2"; shift 2 ;;
        --timeout)
            [[ $# -ge 2 ]] || { echo "--timeout 需要一个正整数参数" >&2; exit 1; }
            [[ "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--timeout 必须为正整数" >&2; exit 1; }
            TIMEOUT_CURL="$2"
            TIMEOUT_SCRAPLING="$2"
            TIMEOUT_STEALTHY="$2"
            shift 2
            ;;
        --retry|--attempts)
            [[ $# -ge 2 ]] || { echo "$1 需要一个正整数参数" >&2; exit 1; }
            [[ "$2" =~ ^[1-9][0-9]*$ ]] || { echo "$1 必须为正整数" >&2; exit 1; }
            MAX_ATTEMPTS="$2"
            shift 2
            ;;
        --recursive)
            [[ "$ACTION" == "sitemap" ]] || { echo "--recursive 仅用于 sitemap" >&2; exit 1; }
            REMOTE_ARGS+=("$1"); shift ;;
        --effective-url)
            [[ $# -ge 2 ]] || { echo "--effective-url 需要一个参数" >&2; exit 1; }
            EFFECTIVE_URL_FILE="$2"; shift 2 ;;
        --language|--time-range|--categories|--safesearch|--engines)
            [[ $# -ge 2 ]] || { echo "$1 需要一个参数" >&2; exit 1; }
            REMOTE_ARGS+=("$1" "$2")
            shift 2
            ;;
        --) DASH_SEPARATOR=1; shift; REMOTE_ARGS+=("$@"); break ;;
        -*) echo "未知参数: $1" >&2; exit 1 ;;
        *) REMOTE_ARGS+=("$1"); shift ;;
    esac
done

# bash 3.2（macOS 自带）在 set -u 下展开空数组会报错，这里显式判断
if [[ ${#REMOTE_ARGS[@]} -gt 0 ]]; then
    set -- "${REMOTE_ARGS[@]}"
else
    set --
fi

# =============================================================================
# 辅助函数
# =============================================================================

# 日志输出
_log() { echo "[$(date '+%H:%M:%S')] $1" >&2; }
_info() { echo "ℹ️  $1" >&2; }
_warn() { echo "⚠️  $1" >&2; }
# doctor 的输出属于“数据”而非日志：设置 DOCTOR_STDOUT=1 时走 stdout，供 MCP 捕获
_err() {
    if [[ "${DOCTOR_STDOUT:-0}" -eq 1 ]]; then echo "❌ $1"; else echo "❌ $1" >&2; fi
}
_ok() {
    if [[ "${DOCTOR_STDOUT:-0}" -eq 1 ]]; then echo "✅ $1"; else echo "✅ $1" >&2; fi
}

# 创建唯一临时文件
_make_temp() {
    mktemp "${TMPDIR:-/tmp}/smart3w_XXXXXX.html"
}

# 检查文件是否有效（非空且可读）
_is_valid_file() {
    [[ -s "$1" ]] && [[ -r "$1" ]]
}

# 在进入任何抓取策略前做一次统一 URL 策略预检（allowlist / SSRF）。
# 无 Python 或 helper 不可用时返回 0，交给具体策略报错；返回 2 表示明确拒绝。
_policy_check_url() {
    local url="$1"
    local rc=0
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || return 0
    "$PYTHON_BIN" "$SCRIPT_DIR/smart3w_http.py" --check-policy "$url" >/dev/null 2>&1 || rc=$?
    [[ $rc -eq 2 ]] && return 2
    return 0
}

# 有 timeout 命令时加上超时限制，没有时直接执行（提升 macOS 等环境兼容性）
_run_with_timeout() {
    local seconds="$1"; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$seconds" "$@"
    else
        "$@"
    fi
}

# =============================================================================
# 策略1a: Python HTTP 抓取（逐跳 SSRF/robots 校验 + Accept Markdown）
# =============================================================================
_fetch_by_python_http() {
    local url="$1"
    local output="$2"
    local rc=0
    local meta=""
    local -a args=(
        "$PYTHON_BIN" "$SCRIPT_DIR/smart3w_http.py" "$url" "$output"
        --timeout "$TIMEOUT_CURL" --ua "$USER_AGENT" --max-redirs 10
    )
    [[ -n "$EFFECTIVE_URL_FILE" ]] && args+=(--effective-url "$EFFECTIVE_URL_FILE")
    [[ "${SMART3W_RESPECT_ROBOTS:-0}" == "1" ]] && args+=(--respect-robots)

    _info "策略1: Python HTTP 抓取..."
    meta=$("${args[@]}") || rc=$?
    if [[ $rc -eq 0 ]] && _is_valid_file "$output"; then
        FETCH_CONTENT_TYPE="$meta"
        local size
        size=$(wc -c < "$output")
        _ok "  Python HTTP 成功 (${size}B)"
        return 0
    fi
    # 2 = 被 allowlist / SSRF / robots 拒绝，不能回退到 curl 绕过安全策略
    [[ $rc -eq 2 ]] && return 2
    return 1
}

# =============================================================================
# 策略1b: curl 抓取（Python HTTP 不可用或失败时的兼容回退）
# =============================================================================
_fetch_by_curl() {
    local url="$1"
    local output="$2"
    local attempt=1
    local rc=0
    local -a curl_args=(
        -f -L -s --compressed
        -A "$USER_AGENT"
        -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        -H "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8"
        -H "Connection: keep-alive"
        -H "Cache-Control: no-cache"
        --connect-timeout 10
        --max-time "$TIMEOUT_CURL"
        --max-redirs 10
        -o "$output"
    )
    case "${SMART3W_SSL_VERIFY:-1}" in
        0|false|no|off) curl_args+=("-k") ;;
    esac
    [[ -n "$PROXY_URL" ]] && curl_args+=("--proxy" "$PROXY_URL")
    curl_args+=("$url")

    _info "策略1: curl 抓取..."

    while [[ $attempt -le $MAX_ATTEMPTS ]]; do
        [[ $attempt -gt 1 ]] && _info "  第 $attempt/$MAX_ATTEMPTS 次尝试..."
        rc=0

        # MCP 模式额外把最终 URL（跟随重定向后）写入侧车文件，供上层做 SSRF 复查。
        # 必须以 curl 退出码判定成功，避免连接中断时把半截文件当成成功。
        if [[ -n "$EFFECTIVE_URL_FILE" ]]; then
            curl "${curl_args[@]}" -w $'\n%{url_effective}' >"$EFFECTIVE_URL_FILE" 2>/dev/null || rc=$?
        else
            curl "${curl_args[@]}" 2>/dev/null || rc=$?
        fi

        if [[ $rc -eq 0 ]] && _is_valid_file "$output"; then
            local size
            size=$(wc -c < "$output")
            _ok "  curl 成功 (${size}B)"
            return 0
        fi

        attempt=$((attempt + 1))
        [[ $attempt -le $MAX_ATTEMPTS ]] && sleep 1
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
    local -a cmd=(scrapling extract fetch "$url" "$output" --real-chrome)
    [[ -n "$PROXY_URL" ]] && cmd+=(--proxy "$PROXY_URL")

    while [[ $attempt -le $MAX_ATTEMPTS ]]; do
        [[ $attempt -gt 1 ]] && _info "  第 $attempt/$MAX_ATTEMPTS 次尝试..."

        if _run_with_timeout "$TIMEOUT_SCRAPLING" "${cmd[@]}" 2>/dev/null && \
           _is_valid_file "$output"; then
            local size
            size=$(wc -c < "$output")
            _ok "  scrapling HTTP 成功 (${size}B)"
            return 0
        fi

        attempt=$((attempt + 1))
        [[ $attempt -le $MAX_ATTEMPTS ]] && sleep 1
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
    local -a cmd=(scrapling extract stealthy-fetch "$url" "$output" --real-chrome --headless --solve-cloudflare)
    [[ -n "$PROXY_URL" ]] && cmd+=(--proxy "$PROXY_URL")

    while [[ $attempt -le $MAX_ATTEMPTS ]]; do
        [[ $attempt -gt 1 ]] && _info "  第 $attempt/$MAX_ATTEMPTS 次尝试..."

        if _run_with_timeout "$TIMEOUT_STEALTHY" "${cmd[@]}" 2>/dev/null && \
           _is_valid_file "$output"; then
            local size
            size=$(wc -c < "$output")
            _ok "  scrapling stealthy 成功 (${size}B)"
            return 0
        fi

        attempt=$((attempt + 1))
        [[ $attempt -le $MAX_ATTEMPTS ]] && sleep 2
    done

    return 1
}

# =============================================================================
# 内容压缩: 依赖检查
# =============================================================================
# 返回 0=依赖齐全，1=模块缺失，2=Python 解释器不可用
_check_compress_dependencies() {
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || return 2
    "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib.util, sys
mods = ('readability', 'bs4')
missing = [m for m in mods if importlib.util.find_spec(m) is None]
sys.exit(0 if not missing else 1)
PY
}

_compress_dependency_hint() {
    _warn "压缩依赖缺失，需安装: readability-lxml beautifulsoup4（解释器: $PYTHON_BIN）"
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
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        _err "Python 解释器不可用: $PYTHON_BIN"
        return 1
    fi
    if "$PYTHON_BIN" - "$module" <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)
PY
    then
        _ok "Python 模块 $module 已安装"
        return 0
    fi
    _err "Python 模块 $module 未安装"
    return 1
}

# 探测 Chrome 路径：优先 SMART3W_CHROME，其次常见安装位置与 PATH
_find_chrome() {
    if [[ -n "${SMART3W_CHROME:-}" ]]; then
        [[ -x "$SMART3W_CHROME" ]] && { printf '%s' "$SMART3W_CHROME"; return 0; }
        return 1
    fi
    local candidates=(
        /opt/google/chrome/chrome
        /usr/bin/google-chrome
        /usr/bin/google-chrome-stable
        /usr/bin/chromium
        /usr/bin/chromium-browser
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        "/Applications/Chromium.app/Contents/MacOS/Chromium"
        "/c/Program Files/Google/Chrome/Application/chrome.exe"
        "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"
    )
    local candidate
    for candidate in "${candidates[@]}"; do
        [[ -x "$candidate" ]] && { printf '%s' "$candidate"; return 0; }
    done
    command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || command -v chrome 2>/dev/null
}

_doctor() {
    local failed=0
    local check_search=0
    DOCTOR_STDOUT=1

    [[ "${1:-}" == "--check-search" ]] && check_search=1

    _doctor_check_cmd curl || failed=1
    _doctor_check_cmd "$PYTHON_BIN" || failed=1
    _doctor_check_cmd scrapling || failed=1
    _doctor_check_python_module httpx || failed=1
    _doctor_check_python_module readability || failed=1
    _doctor_check_python_module bs4 || failed=1

    local chrome
    if chrome=$(_find_chrome); then
        _ok "Chrome: $chrome"
    else
        _err "未找到 Chrome（仅 fetch/stealthy 需要；可用 SMART3W_CHROME 指定路径）"
        failed=1
    fi

    if [[ $check_search -eq 1 ]]; then
        local -a curl_args=(-fsS --max-time 10)
        case "${SMART3W_SSL_VERIFY:-1}" in
            0|false|no|off) curl_args+=("-k") ;;
        esac
        if curl "${curl_args[@]}" "$SEARXNG_INSTANCE/search?q=smart3w&format=json" >/dev/null 2>&1; then
            _ok "SEARXNG_INSTANCE 可连通"
        else
            _err "SEARXNG_INSTANCE 不可连通"
            failed=1
        fi
    fi

    if [[ $failed -eq 0 ]]; then
        echo "✅ doctor 检查通过"
        return 0
    fi

    echo "❌ doctor 检查失败"
    return 1
}

# =============================================================================
# 内容压缩: readability-lxml 提取正文
# =============================================================================
_compress_html() {
    local input="$1"
    local output="$2"

    "$PYTHON_BIN" "$UTILS_PY" compress "$input" "$output"
}

# =============================================================================
# 内容压缩: 微信文章专用（BeautifulSoup + js_content）
# =============================================================================
_compress_wechat() {
    local input="$1"
    local output="$2"

    "$PYTHON_BIN" "$UTILS_PY" compress-wechat "$input" "$output"
}

# 内容输出收尾：压缩成功
_finalize_compressed_output() {
    local raw_file="$1"
    local compressed_file="$2"
    local output="$3"

    local raw_size compressed_size ratio
    raw_size=$(wc -c < "$raw_file")
    compressed_size=$(wc -c < "$compressed_file")
    ratio=0
    if (( raw_size > 0 )); then
        ratio=$(( compressed_size * 100 / raw_size ))
    fi

    if ! mv "$compressed_file" "$output"; then
        _err "写入输出文件失败: $output"
        rm -f "$raw_file" "$compressed_file"
        return 1
    fi
    rm -f "$raw_file"
    echo ""
    _ok "抓取完成 → $output"
    echo "   原始: ${raw_size}B | 压缩后: ${compressed_size}B | 保留: ${ratio}%"
    return 0
}

# 内容输出收尾：保留原始内容
_finalize_raw_output() {
    local raw_file="$1"
    local extra_file="$2"
    local output="$3"
    local warn_message="${4:-}"

    [[ -n "$warn_message" ]] && _warn "$warn_message"
    if ! mv "$raw_file" "$output"; then
        _err "写入输出文件失败: $output"
        rm -f "$raw_file" "$extra_file"
        return 1
    fi
    rm -f "$extra_file"
    echo ""
    _ok "抓取完成(原始) → $output"
    return 0
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
            local py_rc=0
            _fetch_by_python_http "$url" "$raw_file" || py_rc=$?
            [[ $py_rc -eq 2 ]] && return 2
            if [[ $py_rc -eq 0 ]]; then
                method_name="python-http"
            elif _fetch_by_curl "$url" "$raw_file"; then
                method_name="curl"
            fi
            ;;
        fetch)
            _fetch_by_scrapling "$url" "$raw_file" && method_name="scrapling-http"
            ;;
        stealthy)
            _fetch_by_stealthy "$url" "$raw_file" && method_name="scrapling-stealthy"
            ;;
        smart)
            local py_rc=0
            _fetch_by_python_http "$url" "$raw_file" || py_rc=$?
            [[ $py_rc -eq 2 ]] && return 2
            if [[ $py_rc -eq 0 ]]; then
                method_name="python-http"
            elif _fetch_by_curl "$url" "$raw_file"; then
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
    local dep_status=0

    _log "开始抓取[$mode]: $url"
    FETCH_CONTENT_TYPE=""

    # 输出目录不存在时直接创建，避免 mv 失败后仍误报成功
    local output_dir
    output_dir=$(dirname "$output")
    if ! mkdir -p "$output_dir"; then
        _err "无法创建输出目录: $output_dir"
        return 1
    fi

    # 临时文件
    local temp_raw temp_compressed
    temp_raw=$(_make_temp) || { _err "无法创建临时文件"; return 1; }
    temp_compressed=$(_make_temp) || {
        _err "无法创建临时文件"
        rm -f "$temp_raw"
        return 1
    }

    _policy_check_url "$url"
    if [[ $? -eq 2 ]]; then
        _fail_fetch "$temp_raw" "$temp_compressed" "URL 被安全策略拒绝（allowlist/SSRF）"
        return 1
    fi

    _run_fetch_mode "$mode" "$url" "$temp_raw" method_used
    run_status=$?

    if [[ $run_status -eq 2 ]]; then
        _fail_fetch "$temp_raw" "$temp_compressed" "URL 被安全策略拒绝（allowlist/SSRF/robots）"
        return 1
    fi

    if [[ $run_status -ne 0 ]]; then
        case "$mode" in
            get) _fail_fetch "$temp_raw" "$temp_compressed" "get 模式抓取失败" ;;
            fetch) _fail_fetch "$temp_raw" "$temp_compressed" "fetch 模式抓取失败" ;;
            stealthy) _fail_fetch "$temp_raw" "$temp_compressed" "stealthy 模式抓取失败" ;;
            smart) _fail_fetch "$temp_raw" "$temp_compressed" "smart 模式所有策略均失败" ;;
            *) _fail_fetch "$temp_raw" "$temp_compressed" "未知抓取模式: $mode" ;;
        esac
        return 1
    fi

    # 站点通过内容协商直接返回 Markdown 时，不要再用 BeautifulSoup/Readability 压缩
    if [[ $COMPRESS -eq 1 && "${FETCH_CONTENT_TYPE:-}" == text/markdown* ]]; then
        _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" "检测到 text/markdown，跳过 HTML 压缩" || return 1
        echo "   方法: $method_used"
        return 0
    fi

    # 内容压缩
    if [[ $COMPRESS -eq 1 ]]; then
        _check_compress_dependencies
        dep_status=$?
        if [[ $dep_status -eq 2 ]]; then
            _warn "找不到 Python 解释器: $PYTHON_BIN，跳过压缩并保留原始内容"
            _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" || return 1
            echo "   方法: $method_used"
            return 0
        fi
        if [[ $dep_status -ne 0 ]]; then
            _compress_dependency_hint
            _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" "压缩已跳过，保留原始内容" || return 1
            echo "   方法: $method_used"
            return 0
        fi

        # 检测是否为微信文章
        if [[ "$url" == *"mp.weixin.qq.com"* ]]; then
            _info "检测到微信文章，使用微信专用提取..."
            if _compress_wechat "$temp_raw" "$temp_compressed"; then
                _finalize_compressed_output "$temp_raw" "$temp_compressed" "$output" || return 1
            elif _compress_html "$temp_raw" "$temp_compressed"; then
                _finalize_compressed_output "$temp_raw" "$temp_compressed" "$output" || return 1
            else
                _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" "压缩失败，保留原始内容" || return 1
            fi
        else
            # 普通网页：先尝试 readability
            if _compress_html "$temp_raw" "$temp_compressed"; then
                _finalize_compressed_output "$temp_raw" "$temp_compressed" "$output" || return 1
            else
                _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" "压缩失败，保留原始内容" || return 1
            fi
        fi
    else
        _finalize_raw_output "$temp_raw" "$temp_compressed" "$output" || return 1
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
    local query="${1:-}"
    local count="${2:-10}"
    if (( $# >= 2 )); then shift 2; else shift $#; fi

    if [[ -z "$query" ]]; then
        echo "用法: $0 search <关键词> [数量] [--language zh-CN] [--time-range day|week|month|year] [--categories general,news] [--safesearch 0|1|2] [--engines google,bing]" >&2
        exit 1
    fi

    command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
        _err "找不到 Python 解释器: $PYTHON_BIN（可用 SMART3W_PYTHON 指定）"
        exit 1
    }
    _info "搜索: $query (SearXNG)"

    # 选项在前、-- 后的位置参数在后：兼容以 "-" 开头的关键词
    "$PYTHON_BIN" "$UTILS_PY" search "$@" -- "$query" "$count" "$SEARXNG_INSTANCE"
}

# =============================================================================
# Sitemap 解析
# =============================================================================
do_sitemap() {
    local sitemap_url="${1:-}"
    local max_urls="${2:-50}"
    if (( $# >= 2 )); then shift 2; else shift $#; fi

    if [[ -z "$sitemap_url" ]]; then
        echo "用法: $0 sitemap <sitemap_url> [最大条数] [--recursive]" >&2
        exit 1
    fi

    command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
        _err "找不到 Python 解释器: $PYTHON_BIN（可用 SMART3W_PYTHON 指定）"
        exit 1
    }
    _info "解析 Sitemap: $sitemap_url"

    "$PYTHON_BIN" "$UTILS_PY" sitemap "$sitemap_url" "$max_urls" "$@"
}

# =============================================================================
# 主入口
# =============================================================================

case "$ACTION" in
    search)
        query="${REMOTE_ARGS[0]:-}"
        if [[ "$query" == -* && $DASH_SEPARATOR -eq 0 ]]; then
            echo "search 的位置参数需放在选项之前，例如: $0 search 关键词 10 --language en-US" >&2
            echo "关键词本身以 - 开头时使用 -- 分隔，例如: $0 search -- -foo 10" >&2
            exit 1
        fi
        # 数量可省略；第二个参数若是选项，则使用默认数量 10
        if (( $# >= 2 )) && [[ "${REMOTE_ARGS[1]}" != -* ]]; then
            count="${REMOTE_ARGS[1]}"
            [[ "$count" =~ ^[1-9][0-9]*$ ]] || { echo "search 数量必须为正整数" >&2; exit 1; }
            shift 2
        else
            count=10
            (( $# >= 1 )) && shift 1
        fi
        do_search "$query" "$count" "$@"
        ;;

    sitemap)
        url="${REMOTE_ARGS[0]:-}"
        if (( $# >= 2 )) && [[ "${REMOTE_ARGS[1]}" != -* ]]; then
            max="${REMOTE_ARGS[1]}"
            [[ "$max" =~ ^[1-9][0-9]*$ ]] || { echo "sitemap 最大条数必须为正整数" >&2; exit 1; }
            shift 2
        else
            max=50
            (( $# >= 1 )) && shift 1
        fi
        do_sitemap "$url" "$max" "$@"
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
            echo "用法: $0 get|fetch|stealthy|smart <URL> [输出文件] [--no-compress] [--ua 'UA'] [--timeout N] [--retry/--attempts N]" >&2
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
        echo "         [--language zh-CN] [--time-range day|week|month|year]"
        echo "         [--categories general,news] [--safesearch 0|1|2] [--engines google,bing]"
        echo "  $0 get <URL> [输出文件]          轻量 HTTP（Python httpx，失败回退 curl）"
        echo "  $0 fetch <URL> [输出文件]        仅使用 scrapling HTTP/渲染型抓取"
        echo "  $0 stealthy <URL> [输出文件]    仅使用 scrapling stealthy 反爬抓取"
        echo "  $0 smart <URL> [输出文件]       自动按 Python HTTP → curl → scrapling HTTP → stealthy 降级"
        echo "  $0 sitemap <url> [最大条数] [--recursive]  Sitemap 解析（可选递归展开 Index）"
        echo "  $0 doctor [--check-search]       检查运行依赖与环境"
        echo "  $0 smoke                         最小抓取 smoke test"
        echo ""
        echo "参数:"
        echo "  --no-compress    跳过内容压缩，获取原始 HTML"
        echo "  --ua 'UA'        自定义 User-Agent"
        echo "  --timeout N      所有抓取模式的超时时间（秒）"
        echo "  --retry N        每个策略的最大尝试次数（正整数，默认 2；--attempts 为同义参数）"
        ;;

    *)
        echo "未知命令: $ACTION" >&2
        echo "使用 '$0 help' 查看帮助" >&2
        exit 1
        ;;
esac
