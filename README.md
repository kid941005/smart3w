# Smart3W - 智能网页搜索与抓取工具

[![OpenClaw](https://img.shields.io/badge/OpenClaw-Compatible-green)](https://github.com/openclaw/openclaw)
[![MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)
[![Version](https://img.shields.io/badge/Version-2.1.5-blue)](https://github.com/kid941005/smart3w/releases)

集 **SearXNG 网页搜索**、**Sitemap 解析** 与 **智能网页抓取** 于一体。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 🔍 **网页搜索** | SearXNG 驱动，隐私友好 |
| 📄 **网页抓取** | 分层策略架构，稳定可靠 |
| 📱 **微信专取** | 自动识别微信文章，精准提取正文 |
| 📦 **内容压缩** | readability-lxml 提取正文，节省 50-80% token |
| 🗺️ **Sitemap** | 支持 Index 和 URL Set 格式 |
| ⚙️ **可定制** | 支持自定义 UA、超时、重试次数 |

---

## 安装

```bash
git clone https://github.com/kid941005/smart3w.git ~/.openclaw/skills/smart3w
```

**依赖**：
```bash
pip install "scrapling[all]>=0.4.2" "readability-lxml>=0.8.0" beautifulsoup4
```

**压缩依赖检查**：
- 默认压缩依赖 `readability-lxml` 和 `beautifulsoup4`
- 如果缺失，脚本会提示：`压缩依赖缺失，需安装: readability-lxml beautifulsoup4`
- 缺失时不会中断抓取，只会跳过压缩并保留原始 HTML

**浏览器依赖（`fetch` / `stealthy` 必需）**：

- `get` 仅依赖 `curl`
- `fetch` / `stealthy` 默认依赖 `Google Chrome Stable`
- 当前实现默认使用 `--real-chrome`
- 需要确保以下路径存在：`/opt/google/chrome/chrome`

```bash
/opt/google/chrome/chrome --version
```

**说明**：
- `scrapling[all]` 是当前推荐安装方式
- 安装后可先运行 `./scripts/fetch.sh doctor` 做环境自检
- 如需同时验证搜索连通性，可运行 `./scripts/fetch.sh doctor --check-search`（该命令会向 `SEARXNG_INSTANCE` 发起真实网络请求）
- 在当前项目实现中，`fetch` = `scrapling extract fetch + --real-chrome`
- `stealthy` = `scrapling extract stealthy-fetch + --real-chrome`
- `smart` 会按 `curl → fetch → stealthy` 自动降级
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
```

### 抓取
```bash
./scripts/fetch.sh get "https://example.com" ./output.md
```

---

## 命令详解

### search - 网页搜索

```bash
./scripts/fetch.sh search "OpenClaw AI" 5
```

返回 JSON 格式结果（标题、URL、摘要）。

---

### get - HTTP 抓取（轻量）

**仅使用策略**：curl

```bash
./scripts/fetch.sh get "https://example.com" ./output.md
```

适用场景：普通静态网页、博客、文档，优先追求速度和轻量依赖。

---

### smart - 智能抓取

**自动降级策略**：curl → scrapling extract fetch → scrapling extract stealthy-fetch

```bash
./scripts/fetch.sh smart "https://example.com" ./output.md
```

适用场景：不确定站点类型时，优先使用该命令。

---

### fetch - 渲染型抓取

**仅使用策略**：scrapling extract fetch + `--real-chrome`

适合需要比纯 curl 更强页面处理能力的页面。

```bash
./scripts/fetch.sh fetch "https://spa-app.com" ./output.md
```

---

### stealthy - 反爬抓取

**仅使用策略**：scrapling extract stealthy-fetch + `--real-chrome`

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

用于检查 `curl`、`python3`、`scrapling`、Python 模块 `readability`、Python 模块 `bs4`，以及 `/opt/google/chrome/chrome` 是否可用。

传入 `--check-search` 时，还会额外检查 `SEARXNG_INSTANCE` 的连通性；该检查会发起真实网络请求。

---

### sitemap - Sitemap 解析

```bash
./scripts/fetch.sh sitemap "https://example.com/sitemap.xml" [最大条数]
```

支持 Sitemap Index 和 URL Set 两种格式。

---

## 参数选项

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--no-compress` | 跳过压缩，获取原始 HTML | - |
| `--ua 'UA'` | 自定义 User-Agent | Chrome |
| `--timeout N` | 所有抓取模式的超时时间（秒，正整数） | 15 |
| `--retry N` | 失败重试次数（正整数） | 2 |
| `search <关键词> [数量]` | 搜索结果数量（正整数） | 10 |
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
    └─► curl ─► 成功后内容压缩（默认） ─► 输出结果

fetch <URL>
    └─► scrapling extract fetch + --real-chrome ─► 成功后内容压缩（默认） ─► 输出结果

stealthy <URL>
    └─► scrapling extract stealthy-fetch + --real-chrome ─► 成功后内容压缩（默认） ─► 输出结果

smart <URL>
    ├─1─► curl ──────────────────────► [成功] ──┐
    ├─2─► scrapling extract fetch + --real-chrome ─► [成功] ──┼─► 内容压缩（默认） ─► 输出结果
    └─3─► scrapling extract stealthy-fetch + --real-chrome ─► [成功] ─┘

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
| `SEARXNG_INSTANCE` | `https://searxng.hqgg.top:59826` | SearXNG 地址 |

```bash
SEARXNG_INSTANCE="https://your-searxng.com" ./scripts/fetch.sh search "关键词"
```

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
├── README.md         # 本文件
├── SKILL.md          # OpenClaw Skill 元数据
├── LICENSE           # MIT 许可证
├── .gitignore        # Git 忽略文件
└── scripts/
    └── fetch.sh      # 统一入口脚本（搜索 / 抓取 / Sitemap）
```

---

## 许可证

MIT License
