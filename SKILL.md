---
name: smart-fetch
description: 智能网页抓取路由。自动尝试多种方式抓取网页内容：先 scrapling get，失败则自动回退到 stealthy-fetch。用于需要可靠获取网页内容的场景，包括普通网页、反爬保护网站、动态JS页面等。当用户要求抓取、爬取、获取网页内容，或 web_fetch 失败时使用。
version: 1.0.0
license: MIT
---

# Smart Fetch - 智能抓取路由

自动选择最佳方式抓取网页内容，无需手动判断。

## 工作流程

```
用户请求 URL
    ↓
1. 尝试 scrapling extract get (HTTP请求)
    ↓
   成功 → 返回内容
   失败 ↓
2. 尝试 scrapling stealthy-fetch (绕过反爬)
    ↓
   返回结果
```

## 使用方法

### 快速抓取（推荐）

```bash
# 输出到 Markdown 文件
scrapling extract get "https://example.com" /tmp/output.md

# 输出到纯文本
scrapling extract get "https://example.com" /tmp/output.txt

# 使用 CSS 选择器提取特定内容
scrapling extract get "https://example.com" /tmp/output.md --css-selector 'article.content'
```

### 动态页面

```bash
# 使用浏览器渲染
scrapling extract fetch "https://spa-website.com" /tmp/output.md

# 无头模式（后台运行）
scrapling extract fetch "https://example.com" /tmp/output.md --headless
```

### 反爬保护网站

```bash
# 绕过 Cloudflare 等
scrapling extract stealthy-fetch "https://protected-site.com" /tmp/output.html --solve-cloudflare

# 指定 CSS 选择器
scrapling extract stealthy-fetch "https://example.com" /tmp/output.md --css-selector 'main' --headless
```

## 选择策略

| 场景 | 推荐方式 | 命令 |
|------|----------|------|
| 静态网页、博客 | extract get | `scrapling extract get` |
| SPA / 重度JS | extract fetch | `scrapling extract fetch --headless` |
| Cloudflare 保护 | stealthy-fetch | `scrapling extract stealthy-fetch --solve-cloudflare` |
| 登录态页面 | Python + StealthySession | 需要写脚本 |

## 输出格式

根据文件后缀自动选择：
- `.md` → Markdown 格式
- `.txt` → 纯文本
- `.html` → 原始 HTML

## 注意事项

- 使用 `--headless` 后台运行浏览器（推荐）
- 大规模抓取考虑使用 Python Spider 框架
- 尊重网站 robots.txt 和服务条款
