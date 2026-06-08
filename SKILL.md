---
name: smart3w
description: 智能网页抓取路由 + SearXNG 搜索。支持 4 种明确语义的抓取方式：get 仅用 curl，fetch 使用 scrapling extract fetch + --real-chrome，stealthy 使用 scrapling stealthy-fetch + --real-chrome，smart 按 curl → fetch → stealthy 自动降级。默认输出尽量为 Markdown：普通网页提取正文并尽量保留图片为 Markdown 图片链接，微信文章按正文段落输出并保留插图。同时支持 SearXNG 网页搜索。
version: 2.2.1
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
```

**输出格式**：
```json
{
  "success": true,
  "query": "关键词",
  "results": [
    { "title": "标题", "url": "https://...", "snippet": "摘要..." }
  ],
  "result_count": 10
}
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

## 环境自检

```bash
./scripts/fetch.sh doctor
./scripts/fetch.sh doctor --check-search
```

用于检查 `curl`、`python3`、`scrapling`、Python 模块 `readability`、Python 模块 `bs4`，以及 `/opt/google/chrome/chrome` 是否可用。

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

## 环境变量

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--timeout N` | `15` | 所有抓取模式的超时时间（秒，正整数） |
| `--retry N` | `2` | 失败重试次数（正整数） |
| `SEARXNG_INSTANCE` | `https://searxng.hqgg.top:59826` | SearXNG 实例地址 |

## 注意事项

- **Token 节省**：默认压缩，重复请求可节省 50-80% token
- **降级机制**：压缩失败时自动回退为原始 HTML，确保不丢失内容
- SearXNG 搜索优先使用自建实例，隐私友好
- 大规模抓取考虑使用 Python Spider 框架
- 尊重网站 robots.txt 和服务条款
