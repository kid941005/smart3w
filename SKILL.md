---
name: smart3w
description: 智能网页抓取路由 + SearXNG 搜索。自动尝试多种方式抓取网页内容，先 scrapling get，失败则自动回退到 stealthy-fetch。内置 readability-lxml 正文压缩（默认启用），去除导航/侧边栏/广告，节省 50-80% token。同时支持 SearXNG 网页搜索。
version: 1.4.0
license: MIT
---

# Smart3W - 智能抓取路由 + 搜索

集 SearXNG 搜索与网页抓取于一体的智能工具。

## 工作流程

```
用户请求
    ├── 搜索模式：SearXNG 搜索 → 返回标题/URL/摘要
    └── 抓取模式：
            1. scrapling extract get (HTTP)
            │      成功 → readability-lxml 压缩 → 返回正文文本
            │      失败
            2. scrapling stealthy-fetch (绕过反爬)
            │      成功 → readability-lxml 压缩 → 返回正文文本
            │      失败
            3. 所有方式均失败 → 返回原始 HTML（降级保底）
```

## Token 压缩（默认启用）

使用 `readability-lxml` 提取网页正文，自动去除：
- 导航栏、侧边栏、页脚
- 广告、追踪脚本、CSS
- HTML 标签和多余空白

**压缩效果示例**：原始 HTML 512B → 压缩后 126B（保留 24%）

如需获取原始 HTML，使用 `--no-compress` 参数。

## 使用方法

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
# 自动选择最佳方式 + 内容压缩
./scripts/fetch.sh smart "https://example.com" /tmp/output.md

# 跳过压缩，获取原始 HTML
./scripts/fetch.sh smart "https://example.com" /tmp/output.html --no-compress
```

### 快速抓取

```bash
# HTTP 请求，最快
./scripts/fetch.sh get "https://example.com" /tmp/output.md

# 获取原始 HTML（未压缩）
./scripts/fetch.sh get "https://example.com" /tmp/output.html --no-compress
```

### 动态页面

```bash
# 浏览器渲染（SPA / JS 重度页面）
./scripts/fetch.sh fetch "https://spa-website.com" /tmp/output.md
```

### 反爬保护网站

```bash
# 绕过 Cloudflare 等
./scripts/fetch.sh stealthy "https://protected-site.com" /tmp/output.html
```

## 选择策略

| 场景 | 推荐方式 | 命令 |
|------|----------|------|
| 网页搜索 | SearXNG | `fetch.sh search "关键词"` |
| 静态网页、博客 | extract get + 压缩 | `fetch.sh get <URL>`（默认压缩） |
| SPA / 重度JS | extract fetch | `fetch.sh fetch <URL>` |
| Cloudflare 保护 | stealthy-fetch | `fetch.sh stealthy <URL>` |
| 通用（自动降级） | smart | `fetch.sh smart <URL>` |
| 需要原始 HTML | 任意 + --no-compress | `fetch.sh get <URL> --no-compress` |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SEARXNG_INSTANCE` | `https://searxng.example.com:59826` | SearXNG 实例地址 |

## 注意事项

- **Token 节省**：默认压缩，重复请求可节省 50-80% token
- **降级机制**：压缩失败时自动回退为原始 HTML，确保不丢失内容
- SearXNG 搜索优先使用自建实例，隐私友好
- 大规模抓取考虑使用 Python Spider 框架
- 尊重网站 robots.txt 和服务条款
