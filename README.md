# Smart3W - 智能网页搜索与抓取工具

[![OpenClaw](https://img.shields.io/badge/OpenClaw-Compatible-green)](https://github.com/openclaw/openclaw)
[![MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)
[![Version](https://img.shields.io/badge/Version-2.9.2-blue)](https://github.com/kid941005/smart3w/releases)

集 **SearXNG 网页搜索**、**Sitemap 解析** 与 **智能网页抓取** 于一体。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 🔍 **网页搜索** | SearXNG 驱动，隐私友好 |
| 📄 **网页抓取** | 分层策略架构，稳定可靠 |
| 📚 **文档解析** | PDF / Word / Excel / PowerPoint / CSV 自动转 Markdown |
| ⚡ **Rust 加速** | 可选 sidecar 二进制加速 HTML→Markdown / PDF / XLSX / CSV，缺失或失败自动回退纯 Python |
| 🧾 **搜索即读** | 可选直接读取前 N 条搜索结果的正文 |
| 🚀 **批量/递归抓取** | `smart3w_crawl` 并发抓取显式 URL，或从 seed_url 按深度 BFS 递归抓取 |
| 🗺️ **站点发现** | `smart3w_map` 合并 sitemap、首页链接与可选搜索结果，快速盘点站点 URL |
| 🧩 **结构化抽取** | `smart3w_extract` 用 CSS / XPath schema 抽取字段 |
| 🧵 **后台任务** | `smart3w_crawl(run_async=true)` + `smart3w_job` 查询/取消长任务 |
| 🖱️ **浏览器交互** | `smart3w_interact` 在真实 Chrome 中点击、填写、登录、翻页并保存/复用登录状态 |
| ✂️ **正文筛选** | `smart3w_fetch(query=..., max_tokens=...)` 只保留相关段落并控制上下文长度 |
| 🕒 **缓存新鲜度** | `smart3w_fetch(max_age_seconds=...)` 控制缓存年龄，`0` 强制刷新 |
| 🔁 **多实例容灾** | 搜索主实例失败自动切换备用 SearXNG 实例 |
| 🧠 **去重缓存** | URL 规范化 + TTL 缓存，重复请求秒回 |
| 🗃️ **搜索缓存** | 搜索结果 TTL 缓存 + 透传 answers/corrections/suggestions/infobox |
| 🤖 **Robots/限速** | 可选遵守 robots.txt，按域名限速防封 |
| 📱 **微信专取** | 自动识别微信文章，精准提取正文 |
| 📦 **内容压缩** | readability-lxml 提取正文，节省 50-80% token |
| 🗺️ **Sitemap** | 支持 Index 和 URL Set 格式 |
| 🛡️ **SSRF 防护** | MCP 工具层默认拦截内网/保留/特殊用途地址，防服务端请求伪造 |
| ⚙️ **可定制** | 支持自定义 UA、超时、重试次数 |

---

## 安装

要求 Python 3.10+。

```bash
git clone https://github.com/kid941005/smart3w.git ~/.openclaw/skills/smart3w
cd ~/.openclaw/skills/smart3w
```

**依赖**（推荐使用项目声明的依赖，包含 MCP SDK）：
```bash
pip install -e .
```

只安装运行依赖时：
```bash
pip install "mcp>=2.3.0,<3" "scrapling[all]>=0.4.2" "playwright>=1.40" "readability-lxml>=0.8.0" beautifulsoup4 \
  "pypdf>=4.0.0" "python-docx>=1.1.0" "openpyxl>=3.1.0" "python-pptx>=0.6.23"
```

**压缩依赖检查**：
- 默认压缩依赖 `readability-lxml` 和 `beautifulsoup4`
- 如果缺失，脚本会提示：`压缩依赖缺失，需安装: readability-lxml beautifulsoup4`
- 缺失时不会中断抓取，只会跳过压缩并保留原始 HTML

**浏览器依赖（`fetch` / `stealthy` / `smart3w_interact` 必需）**：

- `get` 默认使用 Python HTTP（`httpx`），失败时回退 `curl`
- `fetch` / `stealthy` 默认依赖 `Google Chrome Stable`，并使用 `--real-chrome`
- 启动时会自动探测常见安装路径（Linux / macOS / Windows），也可用 `SMART3W_CHROME` 显式指定
- 探测顺序：`SMART3W_CHROME` → 常见安装路径 → `PATH` 中的 `google-chrome` / `chromium` / `chrome`

```bash
# 自动探测 Chrome（doctor 会打印实际路径）
./scripts/fetch.sh doctor

# 也可显式指定路径后确认
export SMART3W_CHROME="/path/to/chrome"
"$SMART3W_CHROME" --version
```

**说明**：
- `scrapling[all]` 是当前推荐安装方式
- 安装后可先运行 `./scripts/fetch.sh doctor` 做环境自检
- 如需同时验证搜索连通性，可运行 `./scripts/fetch.sh doctor --check-search`（该命令会向 `SEARXNG_INSTANCE` 发起真实网络请求）
- Windows 下需要可用的 `bash`（Git Bash / Cygwin / WSL），MCP 服务会把当前 Python 解释器通过 `SMART3W_PYTHON` 传给 `fetch.sh`
- 在当前项目实现中，`fetch` = 内置 `scripts/scrapling_extract.py`（DynamicFetcher，等价 `scrapling extract fetch + --real-chrome`），抓取后会把最终 URL 写入侧车文件并做安全复检
- `stealthy` = 内置 `scripts/scrapling_extract.py`（StealthyFetcher，等价 `scrapling extract stealthy-fetch + --real-chrome`），同样支持最终 URL 复检
- `smart` 会按 `Python HTTP → curl → fetch → stealthy` 自动降级
- 默认输出尽量为 Markdown
- 普通网页会提取正文并尽量保留图片为 Markdown 图片链接：`![](URL)`
- 微信文章会优先按正文段落输出 Markdown，并保留正文插图

---

## 快速开始

### 最小抓取 smoke test
```bash
./scripts/fetch.sh smoke
```

用于验证最基本的抓取、落盘和输出非空是否正常。

### 搜索
```bash
./scripts/fetch.sh search "关键词" [结果数量]
# 进阶：语言 / 时间范围 / 分类 / 安全搜索 / 指定引擎
./scripts/fetch.sh search "关键词" 10 --language zh-CN --time-range week --categories news --safesearch 1 --engines google,bing
```

### 抓取
```bash
./scripts/fetch.sh get "https://example.com" ./output.md
```

### 作为 MCP 服务运行

```bash
# stdio（适合本地 MCP 客户端）
smart3w-mcp --transport stdio
# Streamable HTTP（默认监听 0.0.0.0:50826，端点 /mcp）
smart3w-mcp
```

启动时会在 stderr 打印一行 `INFO` 日志，包含版本号与构建信息，便于确认线上实际运行的版本：

```text
2026-11-23 15:00:00 INFO smart3w: starting smart3w-mcp version=2.9.2 commit=unknown build_time=unknown transport=streamable-http endpoint=http://0.0.0.0:50826/mcp python=3.12.11
```

`commit` / `build_time` 可在构建镜像时通过 `SMART3W_BUILD_COMMIT` / `SMART3W_BUILD_TIME` 构建参数注入；官方 CI 在 tag 构建时会自动注入当前 commit 与构建时间。
未注入时显示 `unknown`，不会导致启动失败。

| MCP 工具 | 说明 |
| --- | --- |
| `smart3w_search` | SearXNG 搜索（多实例 fallback、结果去重、TTL 缓存；可选 `read_top` 直接读正文） |
| `smart3w_fetch` | 单 URL 抓取（smart/get/fetch/stealthy，缓存 + robots + 限速；自动解析 PDF/Office；可选 `query` 相关段落筛选与 `max_age_seconds` 缓存新鲜度） |
| `smart3w_crawl` | 批量抓取显式 URL，或用 `seed_url` + `max_depth`/`max_pages` 递归 BFS 抓取；`run_async=true` 提交后台任务 |
| `smart3w_map` | 站点 URL 发现（sitemap + 首页链接 + 可选搜索） |
| `smart3w_extract` | 按 CSS / XPath schema 结构化抽取字段 |
| `smart3w_job` | 查询或取消 `smart3w_crawl(run_async=true)` 的后台任务 |
| `smart3w_interact` | 真实浏览器交互：点击 / 填写 / 登录 / 翻页，支持 Cookie 与持久化 profile |
| `smart3w_sitemap` | Sitemap / Sitemap Index 解析（可选 `recursive` 递归展开） |
| `smart3w_doctor` | 运行依赖与环境自检 |

**关键参数**

- `smart3w_search` 新增 `read_top`（0-5，默认 0）与 `content_max_chars`（0-200000，默认 5000）。设置 `read_top>0` 时会直接抓取前 N 条结果正文，返回每条 `content` / `content_truncated` / `content_total_length` / `effective_url` / `read_error` / `read_error_code`，并在顶层返回 `read_top` / `read_succeeded` / `read_failed`。
- `smart3w_fetch` 会按 Content-Type、URL 扩展名和文件头自动识别 PDF / DOCX / XLSX / PPTX，并解析为 Markdown；成功结果增加 `format` / `pages` / `sheets` / `slides` / `paragraphs` / `tables` / `chars` / `document`。旧版 `.doc/.xls/.ppt` 会明确报错，扫描版 PDF 会提示需要 OCR。
- `smart3w_map` 支持 `limit` / `search` / `include_sitemap` / `include_homepage` / `same_domain` / `path_include` / `path_exclude`；返回 `urls: [{url, source}]` 与各来源计数。
- `smart3w_map` 的 `success` 固定为「至少发现 1 个 URL」：`count > 0` 才是 true；只有 `success=true` 且所有来源无错误、未截断时 `complete=true`；有结果但部分来源失败或结果被截断时 `partial=true`、`complete=false`；一个 URL 都没发现时 `success=false`、`complete=false`、`error_code=MAP_FAILED`。错误来源分别放在 `sitemap_errors` / `homepage_error` / `search_error`。
- `smart3w_crawl` 递归模式使用 `seed_url`、`max_depth`（1-5）、`max_pages`（1-500）、`same_domain` 与路径过滤；`run_async=true` 返回 `job_id`，用 `smart3w_job(job_id)` 查询、`smart3w_job(job_id, cancel=true)` 取消。后台任务只保存在当前 MCP 进程内，重启后丢失。
- `smart3w_extract` 接受 1-20 个 URL 与非空 `schema`，`mode=css|xpath`；schema 值可为选择器字符串或 `{selector|css|xpath, attr, all}` 对象，返回 `data` 与 `missing`。
- `smart3w_fetch` 支持 `query`（只保留最相关段落，返回 `filter` 统计与 `original_length`）、`max_tokens`（按 4 字符≈1 token 估算上下文预算）、`max_age_seconds`（-1 跟随全局 TTL，0 强制跳过缓存，>0 限制缓存年龄；命中时返回 `cached` / `cache_age`）。
- `smart3w_interact` 接受 `url` 与声明式 `actions` 数组（goto/click/click_text/fill/press/select/check/uncheck/wait/wait_for_load/scroll，最多 50 个）；每个动作支持 `wait_ms`；`profile` 保存登录状态，`cookies` 注入初始 Cookie，`return_cookies=true` 返回最终 Cookie；所有浏览器请求逐跳通过 SSRF/主机白名单，`goto` 会做策略预检，`respect_robots` 检查起始 URL。浏览器不可用时返回 `BROWSER_UNAVAILABLE`。

### MCP 客户端配置

> 下文的 `example.com` 只是占位符，请替换为你自己的部署域名。

通用 MCP 客户端（Streamable HTTP）：

```json
{
  "mcpServers": {
    "smart3w": {
      "type": "streamable-http",
      "url": "https://smart3w.example.com:59826/mcp"
    }
  }
}
```

the agent harness（DSH）的 `cordis.patch.yml` 需要显式写 `transport`，不能只写 `url`：

```yaml
- insert:
    - id: mcp-smart3w
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: smart3w
        transport: streamable-http
        url: https://smart3w.example.com:59826/mcp
        toolCallTimeoutMs: 120000
```

> DSH 的 `dsh-mcp-client` 缺少 `transport` 时会报
> `Cannot read properties of undefined (reading 'sessionId')`；这不是 Smart3W 服务端错误。

---

## 命令详解

### search - 网页搜索

```bash
./scripts/fetch.sh search "OpenClaw AI" 5
```

返回 JSON 格式结果（标题、URL、摘要、评分、引擎、位置）。当 SearXNG 返回扩展元数据时，还会透传
`answers` / `corrections` / `suggestions` / `infoboxes`。支持：

- `--language zh-CN|en-US|...`：语言（留空表示不指定）
- `--time-range day|week|month|year`：时间范围
- `--categories general,news,images`：搜索分类
- `--safesearch 0|1|2`：安全搜索（0=关闭 1=中等 2=严格）
- `--engines google,bing`：指定引擎

多实例容灾：主 `SEARXNG_INSTANCE` 失败时，会自动按 `SEARXNG_INSTANCES`（逗号分隔）中的备用实例依次兜底。

当请求的 `count` 超过单页结果数时，会自动请求 `pageno` 后续页（最多 10 页），直到够数或没有新增结果。

CLI 的 `search` 在 `success=false`（参数错误、无可用实例等）时返回非零退出码，便于脚本判断。

---

### get - HTTP 抓取（轻量）

**策略**：Python HTTP（逐跳 SSRF/robots；失败回退 curl）

```bash
./scripts/fetch.sh get "https://example.com" ./output.md
```

适用场景：普通静态网页、博客、文档，优先追求速度和轻量依赖。

---

### smart - 智能抓取

**自动降级策略**：Python HTTP → curl → scrapling fetch → scrapling stealthy

```bash
./scripts/fetch.sh smart "https://example.com" ./output.md
```

适用场景：不确定站点类型时，优先使用该命令。

---

### fetch - 渲染型抓取

**仅使用策略**：内置 scrapling 包装器（DynamicFetcher + `--real-chrome`），抓取后复检最终 URL

适合需要比纯 curl 更强页面处理能力的页面。

```bash
./scripts/fetch.sh fetch "https://spa-app.com" ./output.md
```

---

### stealthy - 反爬抓取

**仅使用策略**：内置 scrapling 包装器（StealthyFetcher + `--real-chrome`），抓取后复检最终 URL

处理 Cloudflare 等反爬保护网站。

```bash
./scripts/fetch.sh stealthy "https://protected-site.com" ./output.html
```

---

### doctor - 环境自检

```bash
./scripts/fetch.sh doctor
./scripts/fetch.sh doctor --check-search
```

用于检查 `curl`、Python 解释器（`SMART3W_PYTHON`，默认 `python3`）、`scrapling`、Python 模块 `httpx` / `readability` / `bs4`，以及自动探测到的 Chrome 是否可用。

传入 `--check-search` 时，还会额外检查 `SEARXNG_INSTANCE` 的连通性；该检查会发起真实网络请求。

---

### sitemap - Sitemap 解析

```bash
./scripts/fetch.sh sitemap "https://example.com/sitemap.xml" [最大条数] [--recursive]
```

支持 Sitemap Index 和 URL Set 两种格式；加 `--recursive` 会递归展开 Sitemap Index
（最多 2 层），MCP 工具对应参数为 `recursive=true`。

---

## 参数选项

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--no-compress` | 跳过压缩，获取原始 HTML | - |
| `--ua 'UA'` | 自定义 User-Agent | Chrome |
| `--timeout N` | 所有抓取模式的超时时间（秒，正整数；MCP 工具 `smart3w_fetch` 默认为 30） | 15 |
| `--retry N` | 每个策略的最大尝试次数（正整数；`--attempts` 为同义参数） | 2 |
| `search <关键词> [数量]` | 搜索结果数量（正整数） | 10 |
| `search --language <lang>` | 搜索语言，如 zh-CN / en-US（留空表示不指定） | zh-CN |
| `search --time-range <范围>` | 时间范围：day/week/month/year | 不限 |
| `search --categories <分类>` | 搜索分类，逗号分隔，如 general,news | 默认 |
| `search --safesearch <0|1|2>` | 安全搜索：0=关闭 1=中等 2=严格 | 0 |
| `search --engines <引擎>` | 指定引擎，逗号分隔，如 google,bing | SearXNG 默认 |
| `sitemap <url> [最大条数]` | Sitemap 输出条数（正整数） | 50 |

**示例**：
```bash
./scripts/fetch.sh get "https://example.com" ./output.md \
    --ua "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)" \
    --timeout 20 \
    --retry 3
```

---

## 工作流程

```
get <URL>
    └─► Python HTTP（逐跳 SSRF/robots，优先 Accept Markdown）
         └─► 失败时回退 curl ─► 成功后内容压缩（默认） ─► 输出结果

fetch <URL>
    └─► scrapling_extract.py fetch（DynamicFetcher + --real-chrome）
         └─► 抓后复检最终 URL ─► 成功后内容压缩（默认） ─► 输出结果

stealthy <URL>
    └─► scrapling_extract.py stealthy（StealthyFetcher + --real-chrome）
         └─► 抓后复检最终 URL ─► 成功后内容压缩（默认） ─► 输出结果

smart <URL>
    ├─1─► Python HTTP（逐跳 SSRF/robots） ─► [成功] ──┐
    ├─2─► curl ──────────────────────────► [成功] ──┤
    ├─3─► scrapling extract fetch + --real-chrome ─► [成功] ──┼─► 内容压缩（默认） ─► 输出结果
    └─4─► scrapling extract stealthy-fetch + --real-chrome ─► [成功] ─┘

补充：
- 抓取成功但压缩失败时，保留原始 HTML
- 全部抓取策略失败时，命令直接失败
```

---

## 微信文章提取

Smart3W 支持自动识别并优化提取微信公众号文章。

**工作原理**：
- URL 包含 `mp.weixin.qq.com` → 自动使用 BeautifulSoup 提取 `id='js_content'` 的正文
- 输出优先按正文段落组织为 Markdown
- 正文中的图片会保留为 Markdown 图片链接：`![](URL)`
- 其他网站 → 使用 readability-lxml 进行通用提取

**压缩效果**：

| 页面类型 | 原始 | 压缩后 | 节省 |
|----------|------|--------|------|
| 普通网页 | 100KB | 15KB | 85% |
| 微信文章 | 2.8MB | 841B | 99%+ |

---

## 内容压缩

默认启用正文提取，输出尽量为 Markdown，自动去除：
- 导航栏、侧边栏、页脚
- 广告、追踪脚本、CSS
- 非正文噪音内容

输出补充：
- 普通网页会尽量保留正文图片为 `![](URL)`
- 微信文章会按正文段落输出，并保留正文插图

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SMART3W_HOST` | `0.0.0.0` | MCP HTTP 监听地址（仅 streamable-http 生效） |
| `SMART3W_PYTHON` | 空（使用 `python3`） | `fetch.sh` 调用的 Python 解释器；MCP 服务会自动传入当前解释器 |
| `SMART3W_CHROME` | 空（自动探测） | `fetch` / `stealthy` 使用的 Chrome 可执行文件路径 |
| `SMART3W_CHROME_PATH` | 空（自动探测） | `smart3w_interact` 使用的 Chrome 路径；未设置时自动探测 |
| `SMART3W_PROFILE_DIR` | `~/.smart3w/profiles` | 浏览器登录态 profile 存放目录；容器部署建议挂载持久卷 |
| `SMART3W_BROWSER_HEADLESS` | `1` | 浏览器是否无头运行（`1` / `0`） |
| `SMART3W_BROWSER_NO_SANDBOX` | `0` | 是否给 Chrome 加 `--no-sandbox`；root 容器会自动启用 |
| `SEARXNG_INSTANCE` | `https://searxng.example.com:59826` | SearXNG 地址（主实例；示例域名为占位） |
| `SEARXNG_INSTANCES` | 空 | 备用 SearXNG 实例（逗号分隔），主实例失败时自动兜底 |
| `SMART3W_SSL_VERIFY` | `1` | 是否校验 TLS 证书；自签证书实例可设为 `0`（不推荐） |
| `SMART3W_MAX_DOWNLOAD_BYTES` | `52428800` | 单次下载上限（字节，默认 50MB）；Python HTTP 与 curl 生效，scrapling/stealthy 无法强制中断 |
| `SMART3W_MAX_REDIRECTS` | `10` | curl 回退时手动跟随的最大重定向次数；每一跳都会重新做策略校验 |
| `SMART3W_PORT` | `50826` | MCP HTTP 服务端口 |
| `SMART3W_TIMEOUT` | `30` | MCP 层默认请求超时（秒） |
| `SMART3W_BUILD_COMMIT` | `unknown` | 启动日志显示的构建 commit；镜像构建时通过 `--build-arg SMART3W_BUILD_COMMIT=...` 注入 |
| `SMART3W_BUILD_TIME` | `unknown` | 启动日志显示的构建时间（ISO8601）；镜像构建时通过 `--build-arg SMART3W_BUILD_TIME=...` 注入 |
| `SMART3W_CACHE_TTL` | `300` | 抓取结果缓存有效期（秒） |
| `SMART3W_CACHE_SIZE` | `64` | 抓取结果缓存最大条目数（LRU 淘汰） |
| `SMART3W_RESPECT_ROBOTS` | `0` | 单个 `smart3w_fetch` 是否默认遵守 robots.txt（`1` 开启；`smart3w_crawl` 默认开启） |
| `SMART3W_ROBOTS_TTL` | `3600` | robots.txt 按 origin 缓存时间（秒） |
| `SMART3W_ROBOTS_UA` | Smart3W UA | 检查 robots.txt 时使用的 User-Agent |
| `SMART3W_MIN_INTERVAL` | `0.2` | 同一域名两次请求的最小间隔（秒） |
| `SMART3W_ALLOW_PRIVATE_IPS` | `0` | 是否放行内网/保留地址（SSRF 防护；`1` 关闭拦截，仅限内网可信环境） |
| `SMART3W_ALLOWED_HOSTS` | 空 | 可选主机白名单，逗号分隔；支持精确域名、IP 和 `*.example.com`。设置后只允许白名单主机 |
| `SMART3W_PROXY_URL` | 空 | 显式出口代理；同时用于 Python HTTP、curl 与 scrapling。支持 `http://`、`https://`、`socks5h://` |
| `SMART3W_TRUST_PROXY_DNS` | `0` | 设为 `1` 时，未被 `NO_PROXY` 命中的主机名交由代理侧解析；必须同时配置 `SMART3W_PROXY_URL`，且代理必须实施出口白名单 |
| `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` | 空 | 标准代理变量；用于 `urllib` 搜索/Sitemap 与 curl。`socks://` 在 Python HTTP 路径中会规范为 `socks5://` |
| `NO_PROXY` | 空 | 不走代理的主机列表；使用代理时应包含 `localhost,127.0.0.1,::1,searxng` 等内部目标 |
| `MCP_TRANSPORT` | `streamable-http` | MCP 传输方式（`stdio` / `streamable-http`） |
| `MCP_PATH` | `/mcp` | Streamable HTTP 端点路径 |

```bash
SEARXNG_INSTANCE="https://your-searxng.com" ./scripts/fetch.sh search "关键词"
```

Docker Compose 代理示例（代理运行在宿主机）：

```yaml
services:
  smart3w-mcp:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      SMART3W_PROXY_URL: http://host.docker.internal:7890
      SMART3W_TRUST_PROXY_DNS: "1"
      HTTP_PROXY: http://host.docker.internal:7890
      HTTPS_PROXY: http://host.docker.internal:7890
      ALL_PROXY: http://host.docker.internal:7890
      NO_PROXY: localhost,127.0.0.1,::1,searxng
```

浏览器交互 profile 持久化示例（登录态在容器重建后仍保留）：

```yaml
services:
  smart3w-mcp:
    shm_size: "1gb"
    environment:
      SMART3W_PROFILE_DIR: /data/profiles
      SMART3W_CHROME_PATH: /opt/google/chrome/chrome
    volumes:
      # 宿主机当前目录下的 smart3w_profiles/，与 docker-compose.yml 一致
      - ./smart3w_profiles:/data/profiles
```

首次部署前建议先创建目录：`mkdir -p smart3w_profiles`。

#### host 网络配置

`docker-compose.yml` 使用 `network_mode: host`，容器与宿主机共享网络栈，不配置 `ports` 端口映射。服务直接监听 `SMART3W_HOST:SMART3W_PORT`：

| 场景 | 配置 | 访问地址 |
| --- | --- | --- |
| 默认，允许其他主机访问 | `SMART3W_HOST=0.0.0.0`、`SMART3W_PORT=50826` | `http://<宿主机IP>:50826/mcp` |
| 仅本机反向代理访问 | `SMART3W_HOST=127.0.0.1`、`SMART3W_PORT=50826` | `http://127.0.0.1:50826/mcp` |
| 仅本机且自定义端口 | `SMART3W_HOST=127.0.0.1`、`SMART3W_PORT=50999` | `http://127.0.0.1:50999/mcp` |

可直接在命令前覆盖，或写入同目录 `.env`（compose 会自动读取）：

```bash
SMART3W_HOST=127.0.0.1 SMART3W_PORT=50999 docker compose up -d
```

```dotenv
# .env
SMART3W_IMAGE_TAG=v2.9.2
SMART3W_HOST=127.0.0.1
SMART3W_PORT=50999
```

```bash
docker compose up -d
```

说明：`SMART3W_HOST` 是监听地址，不是对外域名；`0.0.0.0` 监听所有网卡，`127.0.0.1` 仅本机可访问。`SMART3W_PORT` 同时被容器健康检查使用，修改后健康检查会自动使用新端口。host 模式下容器内的 `127.0.0.1` 就是宿主机，便于访问同机 SearXNG 或代理；如需改回 bridge 模式，删除 `network_mode: host` 并自行增加 `ports`。

`docker-compose.yml` 的镜像 tag 支持 `SMART3W_IMAGE_TAG`，默认 `latest`；生产环境建议固定版本，避免自动拉到不兼容的新版本：

```bash
SMART3W_IMAGE_TAG=v2.9.2 docker compose pull
SMART3W_IMAGE_TAG=v2.9.2 docker compose up -d
```

> **安全说明**：MCP 服务默认监听 `0.0.0.0:50826` 且无内置鉴权，部署到公网时请务必放在带认证/限流的
> 反向代理后面。MCP 工具层默认开启 SSRF 防护：抓取目标解析到内网/保留地址时会被拦截
> （`SMART3W_ALLOW_PRIVATE_IPS=1` 可关闭，仅限内网可信环境）；设置 `SMART3W_ALLOWED_HOSTS` 后还会限制可访问主机。
> `SMART3W_TRUST_PROXY_DNS=1` 会把主机名解析交给代理，属于安全边界转移：代理必须限制可访问的出口目标，
> 否则无法防御“公网域名经代理解析到内网”的 SSRF。直接 IP 字面量和 localhost 仍会被拦截。
> `SMART3W_SSL_VERIFY=0` 会关闭 TLS 证书校验（存在中间人风险），仅应在自建实例使用自签证书且无法更换证书时临时开启。
>
> 重定向 SSRF：`get` / `smart` 的 Python HTTP 路径、curl 回退路径与 `smart3w_sitemap` 均已逐跳校验每个 redirect hop
> （SSRF + 可选 robots）；curl 回退不再使用 `-L`，而是手动读取 `Location` 并在每一跳重新校验。
> `fetch` / `stealthy` 的浏览器内部跳转无法关闭自动重定向，当前实现会在抓取完成后读取最终 URL
> （`scripts/scrapling_extract.py` 写入的 effective-url 侧车）并再次执行 allowlist/SSRF 校验；
> MCP 层命中时返回 `SSRF_BLOCKED`。这会把风险窗口缩短为“一次已经发出的请求”，但无法阻止该请求本身，
> 高安全场景仍应叠加出站网络策略（只允许公网出口、代理白名单或 egress firewall），不要仅依赖应用层校验。
>
> 下载上限：`SMART3W_MAX_DOWNLOAD_BYTES` 对 Python HTTP 与 curl 生效；scrapling/stealthy 由第三方库驱动，
> 无法强制中断超大响应，生产环境仍需出站流量/磁盘配额兜底。
>
> 环境变量中的非法数值会回退默认值，不会导致服务启动失败；但生产环境仍建议显式设置并纳入配置管理。

---

### 公网部署：反向代理 + API Key

MCP 服务本身无内置账号体系，公网部署建议只暴露反向代理，由代理做 API Key 校验与限流。
服务在 host 网络下直接监听宿主机 `SMART3W_PORT`（默认 50826）；若代理与应用同机，建议设置
`SMART3W_HOST=127.0.0.1` 仅绑定回环，或通过防火墙限制该端口只允许代理访问，避免绕过代理直连。

Nginx 示例（静态 Token 用 `map` 比较，生产可换成 auth_request / JWT 校验）：

```nginx
map $http_authorization $smart3w_ok {
    default 0;
    "Bearer <TOKEN>" 1;
}

server {
    listen 443 ssl;
    server_name smart3w.example.com;

    location /mcp {
        if ($smart3w_ok = 0) { return 401; }
        proxy_pass http://127.0.0.1:50826;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;          # MCP Streamable HTTP 需要流式转发
        proxy_read_timeout 300s;
    }
}
```

客户端配置需带同名请求头（不同 MCP 客户端字段名可能不同，下例为常见 `headers` 写法）：

```json
{
  "mcpServers": {
    "smart3w": {
      "type": "streamable-http",
      "url": "https://smart3w.example.com/mcp",
      "headers": { "Authorization": "Bearer <TOKEN>" }
    }
  }
}
```

上线前请同时：启用 TLS、限制请求体大小、按 IP/Token 限流，并保留代理访问日志用于审计。

---

## 场景选择指南

| 场景 | 推荐命令 |
|------|----------|
| 普通静态网页/博客/文档 | `get` |
| 需要更强页面处理能力 | `fetch` |
| 反爬保护网站 | `stealthy` |
| 不确定站点类型 | `smart` |
| 网页搜索 | `search` |
| 解析站点地图 | `sitemap` |
| 获取原始 HTML | `get --no-compress` |

---

## 项目结构

```
smart3w/
├── README.md               # 本文件
├── SKILL.md                # OpenClaw Skill 元数据
├── LICENSE                 # MIT 许可证
├── pyproject.toml          # 项目元数据 / 依赖 / 打包配置
├── MANIFEST.in             # sdist 文件清单（含 scripts/）
├── uv.lock                 # 依赖锁文件（钉死版本，防上游破坏性升级）
├── requirements.txt        # Python 依赖（Docker 构建使用）
├── requirements-dev.txt    # 开发依赖（测试 / lint）
├── Dockerfile              # Docker 镜像构建（MCP 服务）
├── docker-compose.yml      # Compose 部署示例
├── mcp_server.py           # MCP 服务入口（MCPServer + Streamable HTTP）
├── smart3w_core.py         # 搜索 / Sitemap / 正文渲染核心（MCP 与 CLI 共用）
├── smart3w_discovery.py    # 站点链接发现 / 路径过滤 / CSS-XPath 结构化抽取
├── smart3w_content.py      # 正文相关性筛选：按查询词选段、控制上下文预算
├── smart3w_document.py     # PDF / DOCX / XLSX / PPTX → Markdown
├── smart3w_interact.py     # 真实浏览器交互：动作白名单 / Cookie / profile / SSRF 请求拦截
├── smart3w_fetch_utils.py  # 抓取治理：TTL 缓存 / robots / 限速
├── smart3w_url_utils.py    # 公共 URL 工具：规范化 / 追踪参数 / SSRF 检查
├── smart3w_rust.py         # Rust 后端桥接（子进程 + JSON 信封，失败回退纯 Python）
├── rust/                   # Rust sidecar 源码（HTML→Markdown / PDF / XLSX / CSV）
├── .github/workflows/      # CI（lint + 测试 + 打包）与 Docker 发布
├── tests/                  # 单元测试（pytest，213 例）
└── scripts/
    ├── fetch.sh            # 统一入口脚本（搜索 / 抓取 / Sitemap）
    ├── document_extract.py # 文档解析 CLI（PDF / Office → Markdown）
    ├── smart3w_http.py     # 轻量 HTTP 抓取：逐跳 SSRF/robots + Accept Markdown + 代理
    ├── scrapling_extract.py # scrapling 浏览器抓取包装器（写出最终 URL 供抓后复检）
    └── smart3w_utils.py    # 共享 Python 逻辑（搜索 / Sitemap / 正文压缩）
```

---

## Rust 加速后端（可选）

HTML→Markdown 抽取、PDF 文本抽取、XLSX/CSV 表格渲染是少数 CPU 密集环节，仓库内置一个
独立 Rust sidecar 二进制 `smart3w-rs` 来加速它们；其余网络抓取仍由 Python 完成。

- **调用方式**：`smart3w_rust.py` 通过子进程 + JSON 信封调用二进制，**二进制缺失、超时或返回失败时自动回退纯 Python**，功能不退化、不报错。
- **覆盖范围**：`html2md`（readability / 微信正文）、`pdf2md`、`xlsx2md`、`csv2md`、`doctor`、`version`。DOCX/PPTX 仍走 Python。
- **环境变量**：
  - `SMART3W_RUST`：`auto`（默认，有二进制就用）/ `on` / `off`（强制纯 Python）。
  - `SMART3W_RS_BIN`：显式指定二进制路径（Docker 中已设为 `/usr/local/bin/smart3w-rs`）。
- **构建**（需要 Rust 工具链，`rustup` 安装）：

```bash
cd rust
cargo build --release
# 产物：rust/target/release/smart3w-rs（Windows 为 smart3w-rs.exe）
```

- **Docker**：`Dockerfile` 使用多阶段构建（`rust:1-slim-bookworm` 编译 → `python:3.12-slim-bookworm` 运行），最终镜像自动带上二进制。
- **自检**：`smart3w_doctor()` 的返回新增 `rust` 字段，显示后端是否可用、路径与版本信息。

---

## 开发

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv build
```

CI 会在 Python 3.10 / 3.12 上运行 lint、单元测试，并校验 wheel 中是否包含
`scripts/fetch.sh` 与 `scripts/smart3w_utils.py`。

版本号唯一来源为 `smart3w_version.py`；`pyproject.toml` 使用 setuptools 动态版本读取该模块，README badge、SKILL.md 与 User-Agent 由 `tests/test_version.py` 校验一致性。

架构上，MCP 的 `smart3w_search` / `smart3w_sitemap` 直接调用 `smart3w_core.py`，
`smart3w_map` / `smart3w_crawl`（链接发现）与 `smart3w_extract`（字段抽取）复用
`smart3w_discovery.py`；`smart3w_fetch` / `smart3w_crawl` 的页面抓取仍由 `fetch.sh` 执行
Python HTTP/curl/scrapling 策略降级，`smart3w_interact` 使用 Playwright 驱动系统 Chrome 执行声明式动作，`smart3w_doctor` 仍需要 shell 环境检查。

---

## 📝 更新日志

### 2.9.2

- **版本号单一来源**：新增 `smart3w_version.py`，运行时、`pyproject.toml` 动态版本、User-Agent 与文档副本统一以该模块为准；README badge、SKILL.md 与版本号一致性由 `tests/test_version.py` 校验。
- **启动日志**：MCP 服务启动时向 stderr 打印一行 INFO 日志，包含 `version`、`commit`、`build_time`、`transport`、`endpoint`、`python`；未注入构建信息时显示 `unknown`，不阻塞启动。
- **镜像构建信息**：Dockerfile 支持 `SMART3W_BUILD_COMMIT` / `SMART3W_BUILD_TIME` 构建参数；CI 在 tag 构建时自动注入，并为镜像写入 OCI revision/created 标签。
- **CI 校验**：容器冒烟测试新增断言，镜像内 `_build_info()['commit']` 不得为 `unknown`。
- **部署配置**：`docker-compose.yml` 镜像 tag 支持 `SMART3W_IMAGE_TAG`，默认 `latest`，生产环境可固定版本。
- **文档**：README 补充启动日志、构建信息注入、镜像 tag 固定与版本号单一来源说明。
- **测试**：新增启动日志、构建信息与版本一致性测试，共 221 例通过。

### 2.9.1

- **安全（scrapling 最终 URL 复检）**：`fetch` / `stealthy` 改走内置 `scripts/scrapling_extract.py` 包装器，抓取完成后把最终 URL 写入侧车并由 `fetch.sh` 按同一套 allowlist/SSRF 策略复检；命中私网或非法目标时删除结果并返回安全拒绝（`SSRF_BLOCKED`），不再把重定向后的内网地址当普通抓取结果返回。仍无法阻止已经发出的请求，公网部署必须同时配置出站网络策略。
- **安全（SearXNG 实例地址）**：搜索实例地址和重定向目标改为统一的 URL 形状/协议校验（长度、控制字符、端口、userinfo、scheme），不拦私网实例；非法形状或重定向直接拒绝。
- **站点发现（`smart3w_map` 成功语义固定）**：`success` 统一为 `count > 0`；新增 `complete` / `partial`，来源错误或截断时 `partial=true`、`complete=false`；无任何 URL 时 `success=false`、`error_code=MAP_FAILED`；搜索错误从 `sitemap_errors` 拆到独立 `search_error`。
- **修复（Windows/MSYS 侧车路径）**：MSYS/Cygwin 下不再使用原生 `timeout.exe` 包裹浏览器抓取，避免 `/tmp/...` 被重解析为 `D:\tmp\...` 导致最终 URL 侧车丢失；改由 scrapling 自身 `--timeout-ms` 控制。
- **文档**：新增反向代理 + API Key 部署示例；同步更新 `SKILL.md` 的 fetch/stealthy 与 map 语义说明。
- **测试**：新增 scrapling 包装器侧车、搜索重定向形状、map success/partial/complete 回归测试，共 213 例通过。

### 2.9.0

- **新增（Rust 加速后端）**：内置独立 Rust sidecar 二进制 `smart3w-rs`，用于 CPU 密集环节：
  - `html2md` / `wechat2md`：scraper 选主内容容器 + html2md 渲染，替代纯 Python readability 路径
  - `pdf2md`：pdf-extract 抽取文本 + lopdf 统计页数
  - `xlsx2md` / `csv2md`：calamine 读表格、csv 解析，统一渲染 Markdown 表格
- **零风险降级**：新增 `smart3w_rust.py` 桥接层（子进程 + JSON 信封），二进制缺失/超时/失败时自动回退纯 Python；`SMART3W_RUST=auto|on|off` 与 `SMART3W_RS_BIN` 控制
- **CSV 支持**：`smart3w_document` 新增 CSV 识别与解析（`text/csv`、`application/csv`、`.csv`），`fetch.sh` 文档路径同步支持
- **Docker**：`Dockerfile` 改为多阶段构建（Rust 编译阶段 → Python 运行阶段），镜像自带二进制
- **自检**：`smart3w_doctor` 返回新增 `rust` 字段（可用性 / 路径 / 版本）
- **测试**：新增 `tests/test_smart3w_rust.py` 与 `tests/test_document_csv.py`

### 2.8.1

- **安全（curl 重定向）**：curl 回退移除 `-L`，改为手动逐跳读取 `Location` 并在每一跳重新执行 allowlist/SSRF 校验；策略拒绝不再被降级为普通抓取失败
- **安全（URL 形状）**：新增 `url_shape_error`，对每个 URL 校验长度、控制字符、端口、userinfo 与协议；`smart3w_map` 与 `smart3w_interact` 的同 origin 策略缓存不再跳过这些检查
- **安全（下载上限）**：新增 `SMART3W_MAX_DOWNLOAD_BYTES`（默认 50MB），Python HTTP 检查 `Content-Length` 并流式限流，curl 使用 `--max-filesize`；超限返回明确错误
- **修复（参数校验）**：fetch/map/crawl/extract/job/interact/sitemap 的布尔参数统一校验，字符串 `"false"` 不再被当作真值；`smart3w_map` 在三个来源全部关闭时返回 `INVALID_ARGUMENT`
- **修复（错误码）**：递归 crawl 未取得任何页面时补 `CRAWL_FAILED`；crawl 早期参数校验（`max_depth`/`max_pages`/`timeout`/`max_length`/`concurrency` 等）与 sitemap 非法 `max_urls` 统一补 `INVALID_ARGUMENT`
- **修复（启动参数）**：`MCP_TRANSPORT` 非法值直接报错，`MCP_PATH` 为空时回退 `/mcp`
- **修复（文档解析）**：加密 PDF 空密码解密失败时返回“PDF 已加密”，不再误报扫描件；`document_extract` 写输出失败返回 1 而非 traceback
- **测试/模块**：新增 URL 形状、策略缓存、curl 逐跳、下载上限、布尔参数、加密 PDF 等回归测试，共 193 例通过

### 2.8.0

- **平台升级**：MCP Python SDK 从 1.x 升级到 2.3，服务端从 `FastMCP` 迁移到 `MCPServer`（`from mcp.server import MCPServer`）；9 个工具的名称、参数与返回结构保持不变
- **启动参数**：`host` / `port` / `streamable_http_path` 从构造函数移到 `mcp.run(transport="streamable-http", ...)`；stdio 模式行为不变
- **依赖**：`mcp>=2.3.0,<3`（撤销 2.7.3 的 `<2` 上限），`uv.lock` 与 `requirements*.txt` 同步更新；新增 3 个迁移回归测试，共 167 例通过

### 2.7.3

- **安全（SSRF）**：`smart3w_map` 与 `smart3w_interact` 的逐跳策略缓存从 hostname 改为 `(scheme, netloc)`，并在缓存前完整校验端口、userinfo 与协议，避免同一主机上的非法 URL 复用已通过的校验结果
- **安全（重定向）**：robots.txt 加载改用 `PolicyRedirectHandler`，每次重定向先做 SSRF/主机白名单校验；Sitemap 重定向处理复用同一基类
- **修复（crawl/extract）**：先 `strip()` 再校验 URL，避免带首尾空格的合法 URL 被误判为非法；重复计数只统计有效 URL
- **修复（任务状态）**：`FetchCancelledError` 现在记为 `cancelled` 而不是 `failed`；任务表增加上限（保留 50 个），超出返回 `JOB_LIMIT`
- **修复（内容协商）**：`compress=false`、链接发现与结构化抽取请求 HTML，避免站点返回 Markdown 导致选择器失效；`compress=true` 仍允许 Markdown
- **修复（文档识别）**：HTML 响应不再因 Content-Type/扩展名被误判为 PDF
- **修复（交互）**：macOS 滚动改用 `Meta+ArrowUp/Down`；移除 `_require_text` 冗余返回
- **修复（参数/错误码）**：`sitemap(recursive)` 与 `doctor(check_search)` 增加布尔校验；crawl/extract 全部失败时返回 `CRAWL_FAILED` / `EXTRACT_FAILED`
- **测试/模块**：新增 13 个回归测试，共 164 例通过

### 2.7.2

- **后台任务**：修复 `smart3w_crawl(run_async=true)` 取消不及时的问题；取消信号现在会中断正在进行的单页抓取，并终止抓取子进程树（POSIX 进程组 / Windows `taskkill /T /F`）
- **测试/模块**：新增真实子进程取消与异步 crawl 取消测试，共 151 例通过

### 2.7.1

- **隐私**：将 README、Dockerfile、compose 与默认配置中的私有部署域名替换为 `example.com` 占位；`.env` 加入 `.gitignore`
- **部署**：默认 `SEARXNG_INSTANCE` 改为占位值，部署时请通过 `.env` 或环境变量设置真实地址

### 2.7.0

- **浏览器交互（P2）**：新增 `smart3w_interact`，使用真实 Chrome 执行 goto / click / click_text / fill / press / select / check / uncheck / wait / wait_for_load / scroll 声明式动作，不执行任意 JavaScript
- **登录状态**：支持 `profile` 持久化 Cookie/localStorage、`cookies` 注入与 `return_cookies` 导出，可完成登录后继续抓取
- **安全**：每个浏览器请求逐跳复用 SSRF/主机白名单检查，`goto` 预检，robots 可选；未知动作、超长选择器与非法 profile 直接拒绝
- **测试/模块**：新增 `smart3w_interact.py` 与 11 个测试，共 149 例通过

### 2.6.1

- **站点发现修复**：`smart3w_map` 始终把站点根 URL 计入结果，避免没有 sitemap/站内链接的站点返回空结果

### 2.6.0

- **正文筛选（P2）**：`smart3w_fetch` 新增 `query` 与 `max_tokens`，按查询词对正文段落打分筛选，返回 `filter` 统计与 `original_length`，避免把整篇长文塞入上下文
- **缓存新鲜度（P2）**：`smart3w_fetch` 新增 `max_age_seconds`，`0` 强制跳过缓存、`>0` 限制缓存年龄；命中缓存时返回 `cache_age`
- **测试/模块**：新增 `smart3w_content.py` 与 10 个测试，共 138 例通过

### 2.5.0

- **站点发现（P1）**：新增 `smart3w_map`，合并 robots.txt/sitemap、首页链接与可选搜索结果，支持同站过滤、路径包含/排除与来源统计
- **递归抓取（P1）**：`smart3w_crawl` 新增 `seed_url` + `max_depth`/`max_pages` BFS 站点抓取，保留原显式 URL 列表模式；所有发现链接均复用 `url_policy_error` 做 SSRF/allowlist 校验
- **结构化抽取（P1）**：新增 `smart3w_extract`，按 CSS / XPath schema 并发抽取字段，返回 `data` / `missing`
- **异步任务（P1）**：`smart3w_crawl(run_async=true)` 返回 `job_id`；新增 `smart3w_job` 查询进度、结果与取消（进程内任务，重启丢失）
- **测试/模块**：新增 `smart3w_discovery.py` 与 14 个测试，共 128 例通过

### 2.4.0

- **搜索读正文**：`smart3w_search` 新增 `read_top`（0-5）与 `content_max_chars`（0-200000），可直接读取前 N 条搜索结果正文，返回 `content` / `content_truncated` / `content_total_length` / `effective_url` / `read_error` / `read_error_code`，顶层 `read_top` / `read_succeeded` / `read_failed`，避免 Agent 多次往返抓取；基础搜索结果缓存仍只存无正文版本
- **文档解析**：`smart3w_fetch` 自动识别并解析 PDF / DOCX / XLSX / PPTX 为 Markdown，返回 `format` / `pages` / `sheets` / `slides` / `paragraphs` / `tables` / `chars` / `document`；旧版 `.doc/.xls/.ppt` 明确报错，扫描件提示需要 OCR
- **Windows 兼容**：MCP 调用 bash 时显式解析 Git/MSYS2 bash，避免命中 System32 WSL；修复 MSYS bash 数组吞 Windows 路径反斜杠
- **依赖/自检**：新增 pypdf、python-docx、openpyxl、python-pptx 依赖；doctor 增加文档解析模块检查

### 2.3.2

- **代理出口**：新增 `SMART3W_PROXY_URL`，Python HTTP、curl、scrapling 统一使用显式代理
- **代理侧 DNS**：新增 `SMART3W_TRUST_PROXY_DNS=1`，在配置可信代理时跳过主机名本地 DNS 检查；直接 IP 与 localhost 仍拦截
- **SOCKS 支持**：`httpx[socks]` 依赖，支持 SOCKS 代理；`socks5h` 在 Python 路径规范为 `socks5`
- **NO_PROXY**：命中 `NO_PROXY` 的目标仍执行严格本地 SSRF 检查
- **Compose**：增加代理变量、`NO_PROXY` 与 `host.docker.internal` 示例

### 2.3.1（开发中）

- **SSRF 地址分类**：对齐官方 fetch，补充 `::/96`（IPv4-compatible IPv6）、`64:ff9b:1::/48`（NAT64 本地前缀）、`2001::/23` 与 IANA 公共例外
- **NAT64 处理**：well-known `64:ff9b::/96` 按嵌入 IPv4 判断，避免误拦公网 NAT64 地址
- **集成文档**：补充 MCP 客户端与 DSH 的 Streamable HTTP 配置；DSH 必须显式设置 `transport: streamable-http`

### 2.3.0（开发中）

- **抓取安全**：`get` / `smart` 默认改用 Python HTTP 抓取器，逐跳检查重定向的 SSRF 与 robots.txt；失败时回退 curl
- **Sitemap 安全**：Sitemap 下载增加逐跳重定向校验，避免先请求内网再拦截
- **主机白名单**：新增 `SMART3W_ALLOWED_HOSTS`，支持精确域名、IP 与 `*.example.com`
- **内容协商**：请求优先发送 `Accept: text/markdown`，站点直接返回 Markdown 时跳过 HTML 压缩
- **代理兼容**：Python 抓取器读取标准 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`，并规范 `socks://` 为 `socks5://`
- **robots 语义**：401/403/5xx 视为完全禁止，其他 4xx 视为无规则

### 2.2.9 (2026-11-05)

- **安全（P0）**：修复 IPv4-mapped IPv6（`::ffff:127.0.0.1`）与 NAT64（`64:ff9b::/96`）地址绕过 SSRF 拦截；补齐 6to4 / Teredo / site-local 等特殊地址段
- **修复（P1）**：Sitemap Index 的类型标签错位（子 sitemap 被误标为 `[index]`），并兼容无 namespace 的 sitemap
- **修复（P1）**：正文压缩对 `<li><p>`、`<td><p>` 与嵌套列表重复输出；重写为递归渲染，嵌套列表正确缩进
- **修复（P1）**：MCP 子进程超时 / 缺少 bash 时抛出未捕获异常；现在返回结构化 `FETCH_TIMEOUT` / `FETCH_UNAVAILABLE`
- **修复（P1）**：批量抓取把非法 URL 计入“重复”，且结果顺序不稳定；新增 `invalid` / `invalid_count`，结果保持输入顺序
- **修复（P1）**：curl 仅按输出文件非空判定成功，可能把半截内容当成成功；改为同时校验 curl 退出码
- **修复（P1）**：输出目录不存在时 `mv` 失败仍返回成功；现在自动创建目录并在写入失败时返回非零
- **修复（P1）**：Windows/venv 下硬编码 `python3` 导致压缩/搜索不可用；新增 `SMART3W_PYTHON`，MCP 自动传入当前解释器
- **修复（P2）**：robots.txt 检查在持有锁时发起网络请求，慢站点会阻塞其他域名；改为锁外加载 + 失败短 TTL 缓存
- **修复（P2）**：`normalize_url` 丢失 IPv6 方括号；`ssrf_blocked` 处理 zone id / 控制字符 / 非法端口
- **架构（P1）**：新增 `smart3w_core.py`，MCP 的 `smart3w_search` / `smart3w_sitemap` 改为直接调用核心模块，去掉“MCP → bash → Python”双重子进程；`fetch.sh` 仅保留抓取策略
- **功能（P1）**：Sitemap 支持 `--recursive` / `recursive=true` 递归展开 Index（最多 2 层）；搜索支持 `pageno` 自动翻页（最多 10 页）
- **健壮性（P2）**：限速器域名表增加容量上限；robots UA 支持 `SMART3W_ROBOTS_UA` 覆盖；`--attempts` 作为 `--retry` 同义参数
- **健壮性（P2）**：核心函数增加非字符串/非整数入参保护；修复混合类型 `engines` 排序崩溃；CLI `search` 失败返回非零退出码；Sitemap 子项失败改为跳过并提示；doctor 在解释器缺失时给出明确提示
- **构建**：修复 buildx 同时设置 `push` 与 `load` 导致的 CI 失败；新增 CI（lint + 测试 + 打包校验）；wheel 现在包含 `scripts/`，新增 `smart3w-mcp` 入口
- **文档**：补齐 MCP 工具表、开发流程、环境变量与安全边界说明

### 2.2.8 (2026-09-07)

- **安全（P0）**：修复 SSRF 重定向绕过——curl 抓取限制最大重定向数（10）并捕获最终 URL，`smart3w_fetch` 对重定向目标做二次 SSRF 校验，命中内网/保留地址时**不返回任何内容**（`error_code: SSRF_BLOCKED`）；`smart3w_sitemap` 同样校验重定向后的最终 URL
- **架构（P1）**：抽取公共 `smart3w_url_utils.py`，统一 URL 规范化、追踪参数过滤与 SSRF 检查，消除 `smart3w_fetch_utils.py` 与 `scripts/smart3w_utils.py` 的重复实现
- **可观测性**：`smart3w_fetch` 成功结果新增 `effective_url` 字段（跟随重定向后的最终 URL）
- **测试**：新增重定向 SSRF 拦截、最终 URL 捕获与公共模块复用测试

### 2.2.7 (2026-09-07)

- **安全（P0）**：新增 SSRF 防护，`smart3w_fetch` / `smart3w_crawl` / `smart3w_sitemap` 默认拦截解析到内网/保留/特殊用途地址的目标（`SMART3W_ALLOW_PRIVATE_IPS=1` 可关闭，仅限内网可信环境）
- **MCP 结构化输出（P1）**：全部工具返回值从 JSON 字符串改为结构化 `dict`，MCP Schema 自动生成真实 JSON 字段定义，LLM 调用更可靠
- **搜索缓存（P1）**：`smart3w_search` 新增 TTL 结果缓存，相同查询/参数组合 300 秒内秒回（命中时带 `cached: true`）
- **搜索元数据（P1）**：透传 SearXNG 的 `answers` / `corrections` / `suggestions` / `infoboxes`，仅在存在时附加
- **Sitemap 错误语义**：`cmd_sitemap` 解析失败时错误走 stderr 并以非零退出码返回，MCP 层可正确返回结构化错误

### 2.2.6 (2026-09-06)

- **修复**：Docker 镜像漏复制 `smart3w_fetch_utils.py` 导致容器启动报 `ModuleNotFoundError`，现已修复
- **CI 增强**：Docker 构建后新增容器启动 smoke test（`import mcp_server`），防止此类"构建通过但启动失败"的问题再次漏网

### 2.2.5 (2026-09-06)

- **搜索增强**：`smart3w_search` 新增 `language/time_range/categories/safesearch/engines` 参数；支持 `SEARXNG_INSTANCES` 多实例故障转移；结果按 URL 去重并补充 `score/engines/positions` 字段
- **批量抓取**：新增 `smart3w_crawl` 工具，支持并发、URL 去重、robots.txt 检查、按域名限速、429 指数退避
- **抓取治理**：`smart3w_fetch` 新增 TTL 去重缓存（`use_cache`）与 robots 选项（`respect_robots`），URL 规范化自动去除追踪参数
- **依赖锁定**：新增 `pyproject.toml` + `uv.lock`，`requirements*.txt` 由锁文件导出（钉死版本），杜绝 mcp 2.x 类事故重演；CI 增加 `uv lock --check` 与 `ruff` 静态检查
- **运维**：Dockerfile / docker-compose 增加 HEALTHCHECK 健康检查
- **测试**：新增抓取治理与搜索增强单元测试（共 23 例）

### 2.2.4 (2026-09-05)

- **MCP 结构化输出**：`smart3w_fetch` 返回 JSON（`success/url/mode/method/content/total_length/chars_returned/truncated/log`），新增 `max_length`、`start_index` 参数
- **内容保真**：正文压缩保留链接 `[text](href)`、列表、表格、代码块与行内格式
- **质量保障**：新增 `tests/` 单元测试（压缩、搜索、SSL 上下文），CI 构建前自动运行

### 2.2.3 (2026-09-05)

- **修复**：锁定 `mcp>=1.6.0,<2`，避免 pip 安装 mcp 2.x 导致 `mcp.server.fastmcp` 缺失、MCP 服务启动崩溃

### 2.2.2 (2026-09-05)

- **修复**：抓取全部失败时不再误报成功，正确返回失败退出码
- **修复**：MCP 子进程超时与 smart 降级链路时长匹配（重试/降级不再被提前杀死）
- **修复**：日志改走 stderr，`search` 输出恢复为纯 JSON
- **安全**：默认恢复 TLS 证书校验（`SMART3W_SSL_VERIFY=0` 可关闭）；curl 增加 `-f` 校验 HTTP 状态码
- **重构**：搜索 / Sitemap / 正文压缩逻辑抽离为 `scripts/smart3w_utils.py`，修复微信正文段落重复输出
- **工程**：MCP 层增加 URL 与参数校验；修复 Docker 中 Chrome 符号链接环；CI 仅 tag push 时更新 `latest`

### 2.2.1 (2026-06-08)

- **MCP 传输修复**：从 SSE 切换为 Streamable HTTP，修复远程部署下 session 丢失问题
- **端点更新**：Hermes 通过 `/mcp` 端点接入
- **Compose 更新**：默认使用 `kid941005/smart3w-mcp:latest`

### 2.2.0 (2026-06-08)

- **Docker + MCP 封装**：新增 `Dockerfile`、`mcp_server.py`、`requirements.txt`
- **暴露 5 个 MCP 工具**：`smart3w_search`、`smart3w_fetch`、`smart3w_crawl`、`smart3w_sitemap`、`smart3w_doctor`
- **Hermes 可直接调用**：通过 Streamable HTTP `/mcp` 端点暴露，配置 `mcp_servers.smart3w` 即可接入
- 镜像推送至 `kid941005/smart3w-mcp` 和 `ghcr.io/kid941005/smart3w-mcp`

---

## 许可证

MIT License
