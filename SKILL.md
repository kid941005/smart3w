---
name: smart3w
description: 智能网页抓取路由 + SearXNG 搜索。自动尝试多种方式抓取网页内容：先 scrapling get，失败则自动回退到 stealthy-fetch。同时支持 SearXNG 网页搜索。用于需要可靠获取网页内容或搜索网页的场景。
version: 1.2.0
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
            │      成功 → 返回内容
            │      失败
            2. scrapling stealthy-fetch (绕过反爬)
                   返回结果
```

## 使用方法

### 网页搜索（新增）

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

### 智能抓取（默认）

```bash
# 自动选择最佳方式抓取（推荐）
./scripts/fetch.sh smart "https://example.com" /tmp/output.md
```

### 快速抓取

```bash
# HTTP 请求，最快
./scripts/fetch.sh get "https://example.com" /tmp/output.md
```

### 动态页面

```bash
# 浏览器渲染（SPA / JS 重度页面）
./scripts/fetch.sh fetch "https://spa-website.com" /tmp/output.md --headless
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
| 静态网页、博客 | extract get | `fetch.sh get <URL>` |
| SPA / 重度JS | extract fetch | `fetch.sh fetch <URL>` |
| Cloudflare 保护 | stealthy-fetch | `fetch.sh stealthy <URL>` |
| 通用（自动降级） | smart | `fetch.sh smart <URL>` |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SEARXNG_INSTANCE` | `https://searxng.example.com:59826` | SearXNG 实例地址 |

## 注意事项

- SearXNG 搜索优先使用自建实例，隐私友好
- 使用 `--headless` 后台运行浏览器（推荐）
- 大规模抓取考虑使用 Python Spider 框架
- 尊重网站 robots.txt 和服务条款
