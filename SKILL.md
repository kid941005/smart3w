---
name: smart3w
description: 智能网页抓取路由 + SearXNG 搜索，支持 PDF/Office 文档解析、搜索即读、站点地图发现、递归抓取、CSS/XPath 结构化抽取与真实浏览器交互（点击/登录/翻页）。支持 4 种明确语义的抓取方式：get 使用 Python HTTP（逐跳 SSRF/robots，失败回退 curl），fetch 使用内置 scrapling 包装器（DynamicFetcher + --real-chrome），stealthy 使用内置 scrapling 包装器（StealthyFetcher + --real-chrome），抓取后都会复检最终 URL，smart 按 Python HTTP → curl → fetch → stealthy 自动降级。支持并发批量抓取、URL 去重缓存、robots.txt 检查与按域名限速；搜索支持多 SearXNG 实例故障转移与语言/时间/分类/安全搜索/引擎参数。默认输出尽量为 Markdown：普通网页提取正文并尽量保留图片为 Markdown 图片链接，微信文章按正文段落输出并保留插图。
version: 2.9.2
license: MIT
---

# Smart3W - 智能抓取路由 + 搜索

集 SearXNG 搜索与网页抓取于一体的智能工具。

## 工作流程

```
用户请求
    ├── 搜索模式：SearXNG 搜索 → 返回标题/URL/摘要
    └── 抓取模式：
            get      → Python HTTP（逐跳 SSRF/robots，失败回退 curl）
            fetch    → 仅内置 scrapling 包装器（DynamicFetcher + --real-chrome，抓后复检最终 URL）
            stealthy → 仅内置 scrapling 包装器（StealthyFetcher + --real-chrome，抓后复检最终 URL）
            smart    → Python HTTP → curl → scrapling fetch → scrapling stealthy

补充说明：
- 抓取成功后默认执行正文提取
- 默认输出尽量为 Markdown
- 普通网页会尽量保留正文图片为 Markdown 图片链接：`![](URL)`
- 微信文章会优先按正文段落输出，并保留正文插图
- 抓取成功但压缩失败时，回退为原始 HTML
- 所有抓取策略失败时，命令直接失败
```

## Token 压缩（默认启用）

默认执行正文提取，输出尽量为 Markdown：
- 普通网页使用 `readability-lxml` 提取正文
- 微信文章使用 BeautifulSoup 提取 `js_content` / `rich_media_content`
- 普通网页会尽量保留正文图片为 Markdown 图片链接：`![](URL)`
- 微信文章会按正文段落输出，并保留正文插图

自动去除：
- 导航栏、侧边栏、页脚
- 广告、追踪脚本、CSS
- 非正文噪音内容

**压缩效果示例**：原始 HTML 512B → 提取后 126B（保留 24%）

如需获取原始 HTML，使用 `--no-compress` 参数。

## Rust 加速后端（可选）

HTML→Markdown 抽取（普通网页 / 微信）、PDF 文本抽取、XLSX/CSV 表格渲染由独立 sidecar 二进制 `smart3w-rs` 加速；其余网络抓取仍由 Python 完成。二进制缺失、超时或返回 `ok:false` 时，`smart3w_rust.py` 自动回退纯 Python，功能不退化、不报错。

### 调用入口

- Python（`smart3w_rust.py`）：
  - `binary_path()` / `available()`：解析二进制路径 / 是否可用
  - `html_to_markdown(html)` / `wechat_to_markdown(html)` → `str | None`
  - `pdf_to_markdown(pdf_bytes)` → `(markdown, pages) | None`
  - `xlsx_to_markdown(xlsx_bytes)` → `(markdown, sheets) | None`
  - `csv_to_markdown(csv_bytes)` → `(markdown, rows) | None`
  - `doctor()` → `{available, path, mode, info?}`
- 集成路径（正常使用无需直接调用二进制）：
  - `smart3w_core.compress_html()` / `compress_wechat()` 先试 Rust，失败回退 readability-lxml / BeautifulSoup；
  - `smart3w_document.extract_document_file()` 对 `.pdf` / `.xlsx|.xls` / `.csv` 先试 Rust，失败回退 Python；
  - `smart3w_fetch`、`smart3w_search(read_top)`、`smart3w_crawl` 的正文压缩与文档解析自动受益。
- 二进制 CLI（调试用，stdin 读原始字节，stdout 输出 JSON 信封）：

```bash
smart3w-rs version              # 或 --version
smart3w-rs doctor               # {"ok":true,"version":"0.1.0","capabilities":["html2md",...]}
smart3w-rs html2md   < page.html
smart3w-rs wechat2md < article.html
smart3w-rs pdf2md    < file.pdf
smart3w-rs xlsx2md   < book.xlsx
smart3w-rs csv2md    < data.csv
```

### 参数与环境变量

开关与路径见下文「环境变量」表的 `SMART3W_RUST` / `SMART3W_RS_BIN`。自动探测顺序：`SMART3W_RS_BIN` → `<包目录>/smart3w_rs/` → `<包目录>/bin/` → `<包目录>/rust/target/release|debug/` → `PATH` 中的 `smart3w-rs`（Windows 为 `smart3w-rs.exe`）。

### 输入输出示例

```bash
$ printf '<h1>Hi</h1><p>Hello <b>world</b></p>' | smart3w-rs html2md
{"markdown":"Hi\n==========\n\nHello **world**","meta":{"chars":30,"title":""},"ok":true}
```

失败示例（调用方据此回退）：

```json
{"ok":false,"error":"no extractable text (scanned/image PDF?)"}
```

```python
from smart3w_rust import pdf_to_markdown

result = pdf_to_markdown(open("a.pdf", "rb").read())
if result:                      # None 表示应回退纯 Python
    markdown, pages = result
```

### 构建与运行前提

- 本机源码运行（需 Rust 工具链）：

```bash
cd rust
cargo build --release --locked
# 产物：rust/target/release/smart3w-rs（Windows 为 smart3w-rs.exe），smart3w_rust.py 会自动发现
```

- Docker：`Dockerfile` 多阶段构建（`rust:1-slim-bookworm` 编译 → `python:3.12-slim-bookworm` 运行），镜像自带 `/usr/local/bin/smart3w-rs`，无需额外卷或 env。
- `--locked` 依赖 `rust/Cargo.lock` 随源码一并提交；缺失时 Docker 构建会失败。
- DOCX / PPTX 不在本二进制范围，仍由 Python 解析。
- 关闭加速：设 `SMART3W_RUST=off`。

### 常见失败与排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| doctor 显示 Rust 未启用 / `rust.available=false` | 二进制不在候选路径或 `SMART3W_RUST=off` | 设 `SMART3W_RS_BIN`，或构建到 `rust/target/release/` |
| Docker 构建 `cargo build --release --locked` 失败 | 未提交 `rust/Cargo.lock`，或拉取 crate 失败 | 提交 lock 文件；检查构建网络/代理 |
| PDF 返回 `no extractable text (scanned/image PDF?)` | 扫描件无文本层 | 改用 OCR，或接受 Python 回退的同一错误 |
| 大文件超时 | 子进程超时 90s | 拆分/压缩输入；失败会自动回退纯 Python |
| 懒加载图片丢失 | Rust 产物不含 `![](` 且 HTML 有 data-src | 已自动回退纯 Python，无需处理 |
| doctor 的 `rust` 字段报错 | `smart3w_rust` 导入异常 | 查看 `rust.error`，确认文件随包安装 |

## 使用方法

### 最小抓取 smoke test

```bash
./scripts/fetch.sh smoke
```

用于验证最基本的抓取、落盘和输出非空是否正常。

### 网页搜索

```bash
# SearXNG 搜索，返回 JSON
./scripts/fetch.sh search "关键词" 10

# 进阶参数：语言 / 时间范围 / 分类 / 安全搜索 / 指定引擎
./scripts/fetch.sh search "关键词" 10 --language zh-CN --time-range week --categories news --safesearch 1 --engines google,bing
```

**输出格式**：
```json
{
  "success": true,
  "query": "关键词",
  "instance": "https://searxng.example.com",
  "results": [
    { "title": "标题", "url": "https://...", "snippet": "摘要...", "score": 0.9, "engines": ["google"], "positions": [1] }
  ],
  "result_count": 10
}
```

主实例不可用时，自动按 `SEARXNG_INSTANCES`（逗号分隔）中的备用实例依次兜底。

当 `count` 超过单页结果数时，会自动请求 `pageno` 后续页（最多 10 页）。

### MCP 工具

| 工具 | 说明 |
| --- | --- |
| `smart3w_search` | SearXNG 搜索（多实例 fallback、去重、TTL 缓存；可选 `read_top` 直接读正文） |
| `smart3w_fetch` | 单 URL 抓取（smart/get/fetch/stealthy，缓存 + robots + 限速；自动解析 PDF/Office；可选 `query` 相关段落筛选与 `max_age_seconds` 缓存新鲜度） |
| `smart3w_crawl` | 批量抓取显式 URL，或用 `seed_url` + `max_depth`/`max_pages` 递归 BFS 抓取；`run_async=true` 提交后台任务 |
| `smart3w_map` | 站点 URL 发现（sitemap + 首页链接 + 可选搜索） |
| `smart3w_extract` | 按 CSS / XPath schema 结构化抽取字段 |
| `smart3w_job` | 查询或取消后台 crawl 任务 |
| `smart3w_interact` | 真实浏览器交互：点击 / 填写 / 登录 / 翻页，支持 Cookie 与持久化 profile |
| `smart3w_sitemap` | Sitemap / Sitemap Index 解析（可选 `recursive` 递归展开） |
| `smart3w_doctor` | 运行依赖与环境自检（含 Rust 加速后端状态） |

> `smart3w_search` / `smart3w_sitemap` 直接调用 `smart3w_core.py`；
> `smart3w_map` / `smart3w_extract` 与递归 crawl 的链接发现复用 `smart3w_discovery.py`；
> `smart3w_fetch` / `smart3w_crawl` 的页面抓取仍由 `fetch.sh` 执行 Python HTTP/curl/scrapling 策略降级；
> `smart3w_fetch` 的相关段落筛选复用 `smart3w_content.py`；
> `smart3w_interact` 使用真实 Chrome 执行声明式动作，不执行任意 JavaScript。

**搜索即读**：`smart3w_search(read_top=2, content_max_chars=5000)` 会在搜索后直接抓取前 2 条结果正文；每条结果增加 `content`、`content_truncated`、`content_total_length`、`effective_url`、`read_error`、`read_error_code`，顶层增加 `read_top`、`read_succeeded`、`read_failed`。

**文档解析**：`smart3w_fetch` 自动把 PDF、DOCX、XLSX、PPTX 解析为 Markdown；结果增加 `format`、`pages`、`sheets`、`slides`、`paragraphs`、`tables`、`chars`、`document`。旧版 Office 文件和扫描版 PDF 会返回明确错误。PDF / XLSX / CSV 在 Rust 后端可用时优先由 `smart3w-rs` 解析，失败自动回退纯 Python。

**正文筛选与缓存新鲜度**：`smart3w_fetch(query="关键词", max_tokens=2000, max_age_seconds=60)` 只保留最相关段落并控制上下文预算（4 字符≈1 token），同时限制缓存年龄；`max_age_seconds=0` 强制跳过缓存，命中时返回 `cache_age`。

**站点发现**：`smart3w_map(url, limit=200, search="", include_sitemap=True, include_homepage=True, same_domain=True, path_include="", path_exclude="")` 返回 `urls: [{url, source}]` 与来源计数。成功语义固定为「至少发现 1 个 URL」：`success = count > 0`；只有 `success=true` 且所有来源无错误、未截断时 `complete=true`；有结果但部分来源失败或截断时 `partial=true`、`complete=false`；一个 URL 都没发现时 `success=false`、`complete=false`、`error_code=MAP_FAILED`。

**递归抓取与后台任务**：`smart3w_crawl(seed_url="https://example.com/", max_depth=2, max_pages=20, same_domain=True)` 进入 BFS 站点抓取；加 `run_async=true` 返回 `job_id`，用 `smart3w_job(job_id)` 查询、`smart3w_job(job_id, cancel=true)` 取消。任务仅存在于当前 MCP 进程。

**结构化抽取**：`smart3w_extract(urls=[...], schema={"title": "h1", "links": {"selector": "a", "attr": "href", "all": true}}, mode="css")` 返回每条 URL 的 `data` 与 `missing`。

**浏览器交互**：`smart3w_interact(url="https://example.com/login", actions=[{"type": "fill", "selector": "#user", "value": "alice"}, {"type": "fill", "selector": "#pass", "value": "..."}, {"type": "click", "selector": "button[type=submit]"}], profile="demo")` 在真实 Chrome 中执行动作并返回最终页面内容；再次使用 `profile="demo"` 可复用登录状态。支持 `return_cookies=true` 导出 Cookie。

### 批量抓取

```bash
# MCP 工具：smart3w_crawl(urls=[...], mode="get", concurrency=3, respect_robots=true)
# 支持 URL 去重、robots.txt 检查、按域名限速、429 指数退避与 TTL 缓存
```

### 智能抓取（默认，推荐）

```bash
# 自动按 Python HTTP → curl → scrapling fetch → scrapling stealthy 降级
./scripts/fetch.sh smart "https://example.com" ./output.md

# 跳过压缩，获取原始 HTML
./scripts/fetch.sh smart "https://example.com" ./output.html --no-compress
```

### 快速抓取

```bash
# Python HTTP（逐跳 SSRF/robots，优先 Accept Markdown），失败回退 curl
./scripts/fetch.sh get "https://example.com" ./output.md

# 获取原始 HTML（未压缩）
./scripts/fetch.sh get "https://example.com" ./output.html --no-compress
```

### 动态页面

```bash
# 仅使用内置 scrapling 包装器（DynamicFetcher + --real-chrome，抓后复检最终 URL）
./scripts/fetch.sh fetch "https://spa-website.com" ./output.md
```

### 反爬保护网站

```bash
# 仅使用内置 scrapling 包装器（StealthyFetcher + --real-chrome，抓后复检最终 URL）
./scripts/fetch.sh stealthy "https://protected-site.com" ./output.html
```

### Sitemap 解析

```bash
./scripts/fetch.sh sitemap "https://example.com/sitemap.xml" 50
# 递归展开 Sitemap Index（最多 2 层）
./scripts/fetch.sh sitemap "https://example.com/sitemap.xml" 50 --recursive
```

## 环境自检

```bash
./scripts/fetch.sh doctor
./scripts/fetch.sh doctor --check-search
```

用于检查 `curl`、Python 解释器（`SMART3W_PYTHON`，默认 `python3`）、`scrapling`、Python 模块 `httpx` / `readability` / `bs4`，以及自动探测到的 Chrome、Rust 加速后端（`smart3w-rs`）是否可用。

传入 `--check-search` 时，还会额外检查 `SEARXNG_INSTANCE` 的连通性；该检查会发起真实网络请求。

## 选择策略

| 场景 | 推荐方式 | 命令 |
| --- | --- | --- |
| 网页搜索 | SearXNG | `fetch.sh search "关键词"` |
| 普通静态网页、博客 | Python HTTP（回退 curl） | `fetch.sh get <URL>` |
| 需要更强页面处理能力 | 内置 scrapling 包装器（DynamicFetcher，抓后复检最终 URL） | `fetch.sh fetch <URL>` |
| Cloudflare 保护 | 内置 scrapling 包装器（StealthyFetcher，抓后复检最终 URL） | `fetch.sh stealthy <URL>` |
| 不确定站点类型 | 自动降级（Python HTTP → curl → fetch → stealthy） | `fetch.sh smart <URL>` |
| 需要原始 HTML | 任意 + --no-compress | `fetch.sh get <URL> --no-compress` |

## CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--timeout N` | `15` | 所有抓取模式的超时时间（秒，正整数；MCP 工具默认为 30） |
| `--retry N` | `2` | 每个策略的最大尝试次数（正整数；`--attempts` 同义） |
| `--no-compress` | 关闭 | 跳过正文压缩，输出原始 HTML |
| `--ua 'UA'` | Chrome UA | 自定义 User-Agent |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SEARXNG_INSTANCE` | `https://searxng.example.com:59826` | SearXNG 实例地址（示例域名为占位） |
| `SEARXNG_INSTANCES` | 空 | 备用 SearXNG 实例（逗号分隔），主实例失败时自动兜底 |
| `SMART3W_SSL_VERIFY` | `1` | 是否校验 TLS 证书；自签证书实例可设为 `0`（不推荐） |
| `SMART3W_MAX_DOWNLOAD_BYTES` | `52428800` | 单次下载上限（字节）；Python HTTP 与 curl 生效，scrapling/stealthy 无法强制中断 |
| `SMART3W_MAX_REDIRECTS` | `10` | curl 回退时手动跟随的最大重定向次数；每跳重新做策略校验 |
| `SMART3W_PYTHON` | 空（`python3`） | `fetch.sh` 调用的 Python 解释器；MCP 自动传入当前解释器 |
| `SMART3W_CHROME` | 空（自动探测） | `fetch` / `stealthy` 使用的 Chrome 可执行文件路径 |
| `SMART3W_CHROME_PATH` | 空（自动探测） | `smart3w_interact` 使用的 Chrome 路径 |
| `SMART3W_PROFILE_DIR` | `~/.smart3w/profiles` | 浏览器登录态 profile 存放目录 |
| `SMART3W_BROWSER_HEADLESS` | `1` | 浏览器是否无头运行 |
| `SMART3W_BROWSER_NO_SANDBOX` | `0` | 是否给 Chrome 加 `--no-sandbox`；root 容器自动启用 |
| `SMART3W_HOST` | `0.0.0.0` | MCP HTTP 监听地址 |
| `SMART3W_PORT` | `50826` | MCP HTTP 服务端口 |
| `SMART3W_TIMEOUT` | `30` | MCP 层默认请求超时（秒） |
| `SMART3W_RUST` | `auto` | Rust 加速后端开关：`auto` / `on` / `off` |
| `SMART3W_RS_BIN` | 空（自动探测） | Rust 二进制路径；Docker 镜像为 `/usr/local/bin/smart3w-rs` |
| `SMART3W_CACHE_TTL` | `300` | 抓取结果缓存有效期（秒） |
| `SMART3W_CACHE_SIZE` | `64` | 抓取结果缓存最大条目数（LRU 淘汰） |
| `SMART3W_RESPECT_ROBOTS` | `0` | 单个 `smart3w_fetch` 是否默认遵守 robots.txt（`smart3w_crawl` 默认开启） |
| `SMART3W_ROBOTS_TTL` | `3600` | robots.txt 按 origin 缓存时间（秒） |
| `SMART3W_ROBOTS_UA` | Smart3W UA | 检查 robots.txt 时使用的 User-Agent |
| `SMART3W_MIN_INTERVAL` | `0.2` | 同一域名两次请求的最小间隔（秒） |
| `SMART3W_ALLOW_PRIVATE_IPS` | `0` | 是否放行内网/保留地址（SSRF 防护；`1` 关闭拦截，仅限内网可信环境） |
| `SMART3W_ALLOWED_HOSTS` | 空 | 可选主机白名单，逗号分隔；支持精确域名、IP 和 `*.example.com` |
| `SMART3W_PROXY_URL` | 空 | 显式出口代理，供 Python HTTP、curl 与 scrapling 使用 |
| `SMART3W_TRUST_PROXY_DNS` | `0` | 设为 `1` 且配置代理时，主机名交由代理侧解析；代理必须限制出口 |
| `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` | 空 | 标准代理变量，供 `urllib` 搜索/Sitemap 与 curl 使用 |
| `NO_PROXY` | 空 | 不走代理的主机；使用代理时包含内部服务地址 |
| `MCP_TRANSPORT` | `streamable-http` | MCP 传输方式（`stdio` / `streamable-http`） |
| `MCP_PATH` | `/mcp` | Streamable HTTP 端点路径 |

> Windows 下需要可用的 `bash`（Git Bash / Cygwin / WSL）；MCP 服务会把当前 Python 解释器通过
> `SMART3W_PYTHON` 传给 `fetch.sh`。

## 注意事项

- **Token 节省**：默认压缩，重复请求可节省 50-80% token
- **降级机制**：压缩失败时自动回退为原始 HTML，确保不丢失内容
- SearXNG 搜索优先使用自建实例，隐私友好
- SSRF / allowlist 防护作用于 MCP 工具层与 `scripts/fetch.sh` 的抓取预检；
  `fetch` / `stealthy` 会在抓取完成后读取 `scripts/scrapling_extract.py` 写出的最终 URL 并复检，
  但浏览器内部重定向无法逐跳拦截，仍需叠加出站网络策略
- 大规模抓取考虑使用 Python Spider 框架
- 尊重网站 robots.txt 和服务条款
