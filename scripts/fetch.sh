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
SEARXNG_INSTANCE="${SEARXNG_INSTANCE:-https://searxng.example.com:59826}"

# 默认参数
COMPRESS=1
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PROXY_URL="${SMART3W_PROXY_URL:-}"
TIMEOUT_CURL=15
TIMEOUT_SCRAPLING=10
TIMEOUT_STEALTHY=20
MAX_ATTEMPTS=2
MAX_DOWNLOAD_BYTES="${SMART3W_MAX_DOWNLOAD_BYTES:-52428800}"
MAX_REDIRECTS="${SMART3W_MAX_REDIRECTS:-10}"
EFFECTIVE_URL_FILE=""
FETCH_CONTENT_TYPE=""
DASH_SEPARATOR=0

# 环境变量非法时回退默认值，避免 curl 因参数错误直接失败
[[ "$MAX_DOWNLOAD_BYTES" =~ ^[1-9][0-9]*$ ]] || MAX_DOWNLOAD_BYTES=52428800
[[ "$MAX_REDIRECTS" =~ ^[0-9]+$ ]] || MAX_REDIRECTS=10

# 解析参数
REMOTE_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-compress) COMPRESS=0; shift ;;
        --accept-html) export SMART3W_ACCEPT_HTML=1; shift ;;
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

# 抓取完成后复检最终 URL（浏览器内部重定向无法逐跳拦截，只能事后校验）。
# 未配置 --effective-url 或侧车为空时跳过；策略检查失败按安全拒绝返回 2。
_verify_effective_url() {
    [[ -n "$EFFECTIVE_URL_FILE" ]] || return 0
    [[ -s "$EFFECTIVE_URL_FILE" ]] || return 0
    local effective=""
    IFS= read -r effective < "$EFFECTIVE_URL_FILE" || true
    [[ -n "$effective" ]] || return 0
    local rc=0
    _policy_check_url "$effective" || rc=$?
    if [[ $rc -ne 0 ]]; then
        _warn "最终 URL 未通过安全策略检查: $effective"
    fi
    return $rc
}

# 在进入任何抓取策略前做一次统一 URL 策略预检（allowlist / SSRF）。
# 仅 rc=0 放行；rc=2 或辅助脚本执行异常（含 Python/httpx 不可用）一律拒绝，
# 避免安全检查失效时 fail-open 直接抓取。
_policy_check_url() {
    local url="$1"
    local rc=0
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        _warn "找不到 Python 解释器（$PYTHON_BIN），无法执行 URL 策略预检，已拒绝抓取"
        return 2
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/smart3w_http.py" --check-policy "$url" >/dev/null 2>&1 || rc=$?
    if [[ $rc -eq 0 ]]; then
        return 0
    fi
    if [[ $rc -eq 2 ]]; then
        return 2
    fi
    _warn "URL 策略预检异常（rc=$rc），按拒绝处理"
    return 2
}

# 入口统一 robots 检查：curl / scrapling 回退路径自身不查 robots.txt。
# 仅在 SMART3W_RESPECT_ROBOTS=1 时执行；未安装 Python 时给出警告而不是静默放行。
_robots_check_url() {
    local url="$1"
    [[ "${SMART3W_RESPECT_ROBOTS:-0}" == "1" ]] || return 0
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        _warn "找不到 Python 解释器，跳过 robots 检查"
        return 0
    fi
    local rc=0
    "$PYTHON_BIN" "$SCRIPT_DIR/smart3w_http.py" --check-robots "$url" >/dev/null 2>&1 || rc=$?
    if [[ $rc -eq 0 ]]; then
        return 0
    fi
    if [[ $rc -eq 2 ]]; then
        return 2
    fi
    _warn "robots 检查异常（rc=$rc），按不允许处理"
    return 2
}

# 解析 Location（支持相对地址）。没有 Python 时只接受绝对 URL。
_resolve_url() {
    local base="$1"
    local location="$2"
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        case "$location" in
            http://*|https://*) printf '%s' "$location"; return 0 ;;
            *) return 1 ;;
        esac
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/smart3w_http.py" --resolve-url "$base" "$location"
}

# 有 timeout 命令时加上超时限制，没有时直接执行（提升 macOS 等环境兼容性）
_run_with_timeout() {
    local seconds="$1"; shift
    # MSYS/Cygwin 的 timeout 是原生 Windows 程序，会把子进程参数按 Windows 规则重解析，
    # 导致 /tmp/... 这类 MSYS 路径在 Python 子进程里变成 D:\tmp\...。浏览器路径改为
    # 依赖 scrapling 自身的 --timeout-ms，避免侧车文件写错位置。
    case "${OSTYPE:-}" in
        msys*|cygwin*) "$@" ;;
        *)
            if command -v timeout >/dev/null 2>&1; then
                timeout "$seconds" "$@"
            else
                "$@"
            fi
            ;;
    esac
}

# =============================================================================
# 策略1a: Python HTTP 抓取（逐跳 SSRF/robots 校验 + Accept Markdown）
# =============================================================================
_fetch_by_python_http() {
    local url="$1"
    local output="$2"
    local attempt=1
    local rc=0
    local meta=""
    # 注意：MSYS bash 下 `local -a args=(...)` 会吞掉 Windows 路径中的反斜杠，
    # 必须先声明再赋值，否则 SMART3W_PYTHON 为绝对路径时会退化成 curl。
    local -a args
    args=(
        "$PYTHON_BIN" "$SCRIPT_DIR/smart3w_http.py" "$url" "$output"
        --timeout "$TIMEOUT_CURL" --ua "$USER_AGENT" --max-redirs "$MAX_REDIRECTS"
    )
    [[ -n "$EFFECTIVE_URL_FILE" ]] && args+=(--effective-url "$EFFECTIVE_URL_FILE")
    [[ "${SMART3W_RESPECT_ROBOTS:-0}" == "1" ]] && args+=(--respect-robots)

    _info "策略1: Python HTTP 抓取..."
    while [[ $attempt -le $MAX_ATTEMPTS ]]; do
        [[ $attempt -gt 1 ]] && _info "  第 $attempt/$MAX_ATTEMPTS 次尝试..."
        rc=0
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
        attempt=$((attempt + 1))
        [[ $attempt -le $MAX_ATTEMPTS ]] && sleep 1
    done
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
    local header_file=""
    local -a curl_args=(
        -f -s --compressed
        -A "$USER_AGENT"
        -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        -H "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8"
        -H "Connection: keep-alive"
        -H "Cache-Control: no-cache"
        --connect-timeout 10
        --max-filesize "$MAX_DOWNLOAD_BYTES"
        --proto "=http,https"
        -o "$output"
    )
    case "${SMART3W_SSL_VERIFY:-1}" in
        0|false|no|off) curl_args+=("-k") ;;
    esac
    [[ -n "$PROXY_URL" ]] && curl_args+=("--proxy" "$PROXY_URL")

    _info "策略1: curl 抓取..."

    while [[ $attempt -le $MAX_ATTEMPTS ]]; do
        [[ $attempt -gt 1 ]] && _info "  第 $attempt/$MAX_ATTEMPTS 次尝试..."
        rc=0
        header_file=$(_make_temp) || return 1
        local current="$url"
        local hop=0
        local deadline=$((SECONDS + TIMEOUT_CURL))

        # 手动逐跳处理重定向：每一跳都重新做 allowlist/SSRF 校验，
        # 不能使用 curl -L 一次性跟随，否则 302 可以绕过首跳预检。
        while :; do
            if ! _policy_check_url "$current"; then
                rc=2
                break
            fi
            local remaining=$((deadline - SECONDS))
            if [[ $remaining -lt 1 ]]; then
                rc=1
                break
            fi
            : > "$header_file"
            local code
            code=$(curl "${curl_args[@]}" --max-time "$remaining" -D "$header_file" \
                -w '%{http_code}' "$current" 2>/dev/null) || { rc=$?; break; }

            if [[ "$code" =~ ^2[0-9][0-9]$ ]] && _is_valid_file "$output"; then
                if [[ -n "$EFFECTIVE_URL_FILE" ]]; then
                    printf '%s\n' "$current" > "$EFFECTIVE_URL_FILE"
                fi
                rm -f "$header_file"
                header_file=""
                local size
                size=$(wc -c < "$output")
                _ok "  curl 成功 (${size}B)"
                return 0
            fi

            if [[ "$code" =~ ^3[0-9][0-9]$ ]]; then
                if [[ $hop -ge $MAX_REDIRECTS ]]; then
                    rc=1
                    break
                fi
                local location
                location=$(awk 'tolower($0) ~ /^location:/ { sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit }' "$header_file")
                if [[ -z "$location" ]]; then
                    rc=1
                    break
                fi
                current=$(_resolve_url "$current" "$location") || { rc=1; break; }
                hop=$((hop + 1))
                continue
            fi

            rc=1
            break
        done

        rm -f "$header_file"
        header_file=""
        [[ $rc -eq 2 ]] && return 2

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
    # 使用内置包装器：保留 DynamicFetcher 行为，并写出最终 URL 供抓后安全复检。
    local -a cmd=("$PYTHON_BIN" "$SCRIPT_DIR/scrapling_extract.py" fetch "$url" "$output" --real-chrome --timeout-ms "$((TIMEOUT_SCRAPLING * 1000))")
    [[ -n "$EFFECTIVE_URL_FILE" ]] && cmd+=(--effective-url "$EFFECTIVE_URL_FILE")
    [[ -n "$PROXY_URL" ]] && cmd+=(--proxy "$PROXY_URL")
    [[ -n "$EFFECTIVE_URL_FILE" ]] && rm -f "$EFFECTIVE_URL_FILE"

    while [[ $attempt -le $MAX_ATTEMPTS ]]; do
        [[ $attempt -gt 1 ]] && _info "  第 $attempt/$MAX_ATTEMPTS 次尝试..."

        if _run_with_timeout "$TIMEOUT_SCRAPLING" "${cmd[@]}" 2>/dev/null && \
           _is_valid_file "$output"; then
            local eff_rc=0
            _verify_effective_url || eff_rc=$?
            if [[ $eff_rc -ne 0 ]]; then
                rm -f "$output" "$EFFECTIVE_URL_FILE"
                return 2
            fi
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
    # 使用内置包装器：保留 StealthyFetcher 行为，并写出最终 URL 供抓后安全复检。
    local -a cmd=("$PYTHON_BIN" "$SCRIPT_DIR/scrapling_extract.py" stealthy "$url" "$output" --real-chrome --headless --solve-cloudflare --timeout-ms "$((TIMEOUT_STEALTHY * 1000))")
    [[ -n "$EFFECTIVE_URL_FILE" ]] && cmd+=(--effective-url "$EFFECTIVE_URL_FILE")
    [[ -n "$PROXY_URL" ]] && cmd+=(--proxy "$PROXY_URL")
    [[ -n "$EFFECTIVE_URL_FILE" ]] && rm -f "$EFFECTIVE_URL_FILE"

    while [[ $attempt -le $MAX_ATTEMPTS ]]; do
        [[ $attempt -gt 1 ]] && _info "  第 $attempt/$MAX_ATTEMPTS 次尝试..."

        if _run_with_timeout "$TIMEOUT_STEALTHY" "${cmd[@]}" 2>/dev/null && \
           _is_valid_file "$output"; then
            local eff_rc=0
            _verify_effective_url || eff_rc=$?
            if [[ $eff_rc -ne 0 ]]; then
                rm -f "$output" "$EFFECTIVE_URL_FILE"
                return 2
            fi
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
    _doctor_check_python_module scrapling || failed=1
    _doctor_check_python_module httpx || failed=1
    _doctor_check_python_module readability || failed=1
    _doctor_check_python_module bs4 || failed=1
    _doctor_check_python_module pypdf || failed=1
    _doctor_check_python_module docx || failed=1
    _doctor_check_python_module openpyxl || failed=1
    _doctor_check_python_module pptx || failed=1

    # Rust 后端是可选项：缺失时自动回退纯 Python，因此不计入 failed
    local project_root
    project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if PYTHONPATH="$project_root${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import smart3w_rust
raise SystemExit(0 if smart3w_rust.doctor().get("available") else 1)
PY
    then
        _ok "Rust 后端已启用（HTML/PDF/XLSX/CSV 加速）"
    else
        _ok "Rust 后端未启用（自动回退纯 Python）"
    fi

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

# =============================================================================
# 文档解析: PDF / DOCX / XLSX / PPTX
# =============================================================================
# 仅用轻量检查决定是否调用解析器，避免每个 HTML 页面都多起一个 Python 进程
_maybe_document() {
    local path="$1"
    local url="$2"
    local content_type="${3:-}"

    case "${content_type%%;*}" in
        application/pdf|application/vnd.openxmlformats-officedocument.*|application/msword|application/vnd.ms-excel|application/vnd.ms-powerpoint|text/csv|application/csv)
            return 0
            ;;
    esac

    local path_only="${url%%\?*}"
    case "$path_only" in
        *.pdf|*.PDF|*.docx|*.DOCX|*.xlsx|*.XLSX|*.pptx|*.PPTX|*.doc|*.DOC|*.xls|*.XLS|*.ppt|*.PPT|*.csv|*.CSV)
            return 0
            ;;
    esac

    [[ -r "$path" ]] || return 1
    if head -c 4 "$path" 2>/dev/null | grep -q '%PDF'; then
        return 0
    fi
    # 不再用 "PK" 魔数猜测 Office：任意 zip（crx/jar/普通压缩包）都符合 PK，
    # 误判后会把二进制当文档解析；仅依 Content-Type / 扩展名判定 Office。
    return 1
}

# 返回 0=已解析为文档，3=不是文档，1=文档解析失败
_extract_document() {
    local input="$1"
    local output="$2"
    local url="$3"
    local content_type="${4:-}"

    "$PYTHON_BIN" "$SCRIPT_DIR/document_extract.py" "$input" "$output" \
        --content-type "$content_type" --url "$url"
}

# 内容输出收尾：文档解析成功
_finalize_document_output() {
    local raw_file="$1"
    local document_file="$2"
    local output="$3"

    local raw_size document_size
    raw_size=$(wc -c < "$raw_file")
    document_size=$(wc -c < "$document_file")

    if ! mv "$document_file" "$output"; then
        _err "写入输出文件失败: $output"
        rm -f "$raw_file" "$document_file"
        return 1
    fi
    rm -f "$raw_file"
    echo ""
    _ok "文档解析完成 → $output"
    echo "   原始: ${raw_size}B | Markdown: ${document_size}B"
    return 0
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
            else
                local curl_rc=$?
                [[ $curl_rc -eq 2 ]] && return 2
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
            else
                local curl_rc=$?
                [[ $curl_rc -eq 2 ]] && return 2
            fi
            if [[ -z "$method_name" ]]; then
                if _fetch_by_scrapling "$url" "$raw_file"; then
                    method_name="scrapling-http"
                elif _fetch_by_stealthy "$url" "$raw_file"; then
                    method_name="scrapling-stealthy"
                fi
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

    # 统一在入口检查 robots：curl / scrapling 回退路径自身不查 robots.txt
    local robots_rc=0
    _robots_check_url "$url" || robots_rc=$?
    if [[ $robots_rc -eq 2 ]]; then
        _fail_fetch "$temp_raw" "$temp_compressed" "robots.txt 禁止抓取该 URL"
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

    # 文档解析：PDF/Office 直接转 Markdown，避免把二进制当 HTML 压缩
    if _maybe_document "$temp_raw" "$url" "${FETCH_CONTENT_TYPE:-}"; then
        local document_rc=0
        _info "检测到文档，尝试解析为 Markdown..."
        _extract_document "$temp_raw" "$temp_compressed" "$url" "${FETCH_CONTENT_TYPE:-}" || document_rc=$?
        if [[ $document_rc -eq 0 ]]; then
            _finalize_document_output "$temp_raw" "$temp_compressed" "$output" || return 1
            echo "   方法: $method_used"
            return 0
        fi
        if [[ $document_rc -ne 3 ]]; then
            _fail_fetch "$temp_raw" "$temp_compressed" "文档解析失败"
            return 1
        fi
        _warn "未能识别文档格式，按普通内容处理"
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
        echo "  --accept-html    请求 HTML（而非 Markdown），用于链接发现/结构化抽取"
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
