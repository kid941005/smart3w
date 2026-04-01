---
name: smart3w
description: 智能网页抓取路由 + SearXNG 搜索 + Sitemap 索引解析。支持自动降级：先 scrapling get，失败则回退到 stealthy-fetch。同时支持 SearXNG 网页搜索和 Sitemap 索引解析。用于需要可靠获取网页内容、搜索网页或解析站点地图的场景。
version: 1.3.0
license: MIT
---

# Smart3W - 智能抓取路由 + 搜索 + Sitemap

集 **SearXNG 网页搜索**、**Sitemap 索引解析** 与 **智能网页抓取** 于一体的工具。

## 工作流程

```
用户请求
    ├── search  → SearXNG 搜索 → 返回标题/URL/摘要
    ├── sitemap → 解析 Sitemap → 返回 URL 列表
    └── fetch
            1. scrapling extract get (HTTP)
            │      成功 → 返回内容
            │      失败
            2. scrapling stealthy-fetch (绕过反爬)
                   返回结果
```

## 使用方法

### 网页搜索

```bash
./scripts/fetch.sh search "关键词" [结果数量]
```

### Sitemap 索引解析

```bash
# 解析站点地图，返回 URL 列表（默认50条）
./scripts/fetch.sh sitemap "https://example.com/sitemap.xml"

# 指定最大条数
./scripts/fetch.sh sitemap "https://example.com/sitemap.xml" 100
```

**支持两种格式**：
- **Sitemap Index**（索引文件）→ 返回子 sitemap 文件 URL
- **URL Set**（页面列表）→ 返回具体页面 URL（含最后更新时间）

### 智能抓取（默认）

```bash
./scripts/fetch.sh smart "https://example.com" /tmp/output.md
```

### 快速抓取

```bash
./scripts/fetch.sh get "https://example.com" /tmp/output.md
```

### 动态页面

```bash
./scripts/fetch.sh fetch "https://spa-website.com" /tmp/output.md --headless
```

### 反爬保护网站

```bash
./scripts/fetch.sh stealthy "https://protected-site.com" /tmp/output.html
```

## 选择策略

| 场景 | 推荐方式 | 命令 |
|------|----------|------|
| 网页搜索 | search | `fetch.sh search "关键词"` |
| 站点地图解析 | sitemap | `fetch.sh sitemap <url>` |
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
