---
name: smart3w
description: 智能网页抓取路由 + SearXNG 搜索。支持 4 种明确语义的抓取方式：get 仅用 curl，fetch 使用 scrapling extract fetch + --real-chrome，stealthy 使用 scrapling stealthy-fetch + --real-chrome，smart 按 curl → fetch → stealthy 自动降级。支持并发批量抓取、URL 去重缓存、robots.txt 检查与按域名限速；搜索支持多 SearXNG 实例故障转移与语言/时间/分类/安全搜索/引擎参数。默认输出尽量为 Markdown：普通网页提取正文并尽量保留图片为 Markdown 图片链接，微信文章按正文段落输出并保留插图。
version: 2.2.9
license: MIT
---

# Smart3W - 智能抓取路由 + 搜索

集 SearXNG 搜索与网页抓取于一体的智能工具。

## 工作流程

```
用户请求
    ├── 搜索模式：SearXNG 搜索 → 返回标题/URL/摘要
    └── 抓取模式：
            get      → 仅 curl
            fetch    → 仅 scrapling extract fetch + --real-chrome
            stealthy → 仅 scrapling stealthy-fetch + --real-chrome
            smart    → curl → scrapling extract fetch + --real-chrome → stealthy-fetch + --real-chrome

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
|------|------|
| `smart3w_search` | SearXNG 搜索（多实例 fallback、去重、TTL 缓存） |
| `smart3w_fetch` | 单 URL 抓取（smart/get/fetch/stealthy，缓存 + robots + 限速） |
| `smart3w_crawl` | 批量抓取（并发、去重、非法 URL 报告、结果保持输入顺序） |
| `smart3w_sitemap` | Sitemap / Sitemap Index 解析（可选 `recursive` 递归展开） |
| `smart3w_doctor` | 运行依赖与环境自检 |

> `smart3w_search` / `smart3w_sitemap` 直接调用 `smart3w_core.py`，不再经过 `fetch.sh`；
> `smart3w_fetch` / `smart3w_crawl` 仍由 `fetch.sh` 执行 curl/scrapling 策略降级。

### 批量抓取

```bash
# MCP 工具：smart3w_crawl(urls=[...], mode="get", concurrency=3, respect_robots=true)
# 支持 URL 去重、robots.txt 检查、按域名限速、429 指数退避与 TTL 缓存
```

### 智能抓取（默认，推荐）

```bash
# 自动按 curl → scrapling extract fetch + --real-chrome → stealthy-fetch + --real-chrome 降级
./scripts/fetch.sh smart "https://example.com" ./output.md

# 跳过压缩，获取原始 HTML
./scripts/fetch.sh smart "https://example.com" ./output.html --no-compress
```

### 快速抓取

```bash
# 仅使用 curl，最快最轻量
./scripts/fetch.sh get "https://example.com" ./output.md

# 获取原始 HTML（未压缩）
./scripts/fetch.sh get "https://example.com" ./output.html --no-compress
```

### 动态页面

```bash
# 仅使用 scrapling extract fetch + --real-chrome
./scripts/fetch.sh fetch "https://spa-website.com" ./output.md
```

### 反爬保护网站

```bash
# 仅使用 scrapling stealthy-fetch + --real-chrome
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

用于检查 `curl`、Python 解释器（`SMART3W_PYTHON`，默认 `python3`）、`scrapling`、Python 模块 `readability` / `bs4`，以及自动探测到的 Chrome 是否可用。

传入 `--check-search` 时，还会额外检查 `SEARXNG_INSTANCE` 的连通性；该检查会发起真实网络请求。

## 选择策略

| 场景 | 推荐方式 | 命令 |
|------|----------|------|
| 网页搜索 | SearXNG | `fetch.sh search "关键词"` |
| 普通静态网页、博客 | curl | `fetch.sh get <URL>` |
| 需要更强页面处理能力 | scrapling extract fetch + --real-chrome | `fetch.sh fetch <URL>` |
| Cloudflare 保护 | stealthy-fetch + --real-chrome | `fetch.sh stealthy <URL>` |
| 不确定站点类型 | 自动降级（curl → fetch → stealthy） | `fetch.sh smart <URL>` |
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
| `SEARXNG_INSTANCE` | `https://searxng.hqgg.top:59826` | SearXNG 实例地址 |
| `SEARXNG_INSTANCES` | 空 | 备用 SearXNG 实例（逗号分隔），主实例失败时自动兜底 |
| `SMART3W_SSL_VERIFY` | `1` | 是否校验 TLS 证书；自签证书实例可设为 `0`（不推荐） |
| `SMART3W_PYTHON` | 空（`python3`） | `fetch.sh` 调用的 Python 解释器；MCP 自动传入当前解释器 |
| `SMART3W_CHROME` | 空（自动探测） | Chrome 可执行文件路径 |
| `SMART3W_HOST` | `0.0.0.0` | MCP HTTP 监听地址 |
| `SMART3W_PORT` | `50826` | MCP HTTP 服务端口 |
| `SMART3W_TIMEOUT` | `30` | MCP 层默认请求超时（秒） |
| `SMART3W_CACHE_TTL` | `300` | 抓取结果缓存有效期（秒） |
| `SMART3W_CACHE_SIZE` | `64` | 抓取结果缓存最大条目数（LRU 淘汰） |
| `SMART3W_RESPECT_ROBOTS` | `0` | 单个 `smart3w_fetch` 是否默认遵守 robots.txt（`smart3w_crawl` 默认开启） |
| `SMART3W_ROBOTS_TTL` | `3600` | robots.txt 按 origin 缓存时间（秒） |
| `SMART3W_ROBOTS_UA` | Smart3W UA | 检查 robots.txt 时使用的 User-Agent |
| `SMART3W_MIN_INTERVAL` | `0.2` | 同一域名两次请求的最小间隔（秒） |
| `SMART3W_ALLOW_PRIVATE_IPS` | `0` | 是否放行内网/保留地址（SSRF 防护；`1` 关闭拦截，仅限内网可信环境） |
| `MCP_TRANSPORT` | `streamable-http` | MCP 传输方式（`stdio` / `streamable-http`） |
| `MCP_PATH` | `/mcp` | Streamable HTTP 端点路径 |

> Windows 下需要可用的 `bash`（Git Bash / Cygwin / WSL）；MCP 服务会把当前 Python 解释器通过
> `SMART3W_PYTHON` 传给 `fetch.sh`。

## 注意事项

- **Token 节省**：默认压缩，重复请求可节省 50-80% token
- **降级机制**：压缩失败时自动回退为原始 HTML，确保不丢失内容
- SearXNG 搜索优先使用自建实例，隐私友好
- SSRF 防护仅作用于 MCP 工具层（`smart3w_fetch` / `smart3w_crawl` / `smart3w_sitemap`）；
  直接调用 `scripts/fetch.sh` 时由调用方自行保证目标可信
- 大规模抓取考虑使用 Python Spider 框架
- 尊重网站 robots.txt 和服务条款
