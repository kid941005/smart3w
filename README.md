# Smart3W - 智能网页搜索与抓取工具

[![OpenClaw](https://img.shields.io/badge/OpenClaw-Compatible-green)](https://github.com/openclaw/openclaw)
[![MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)
[![Version](https://img.shields.io/badge/Version-2.1.0-blue)](https://github.com/kid941005/smart3w/releases)

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
scrapling install --force
```

---

## 快速开始

### 搜索
```bash
./scripts/fetch.sh search "关键词" [结果数量]
```

### 抓取
```bash
./scripts/fetch.sh get "https://example.com" /tmp/output.md
```

---

## 命令详解

### search - 网页搜索

```bash
./scripts/fetch.sh search "OpenClaw AI" 5
```

返回 JSON 格式结果（标题、URL、摘要）。

---

### get - HTTP 抓取（推荐）

**默认策略**：curl → scrapling → scrapling-stealthy

```bash
./scripts/fetch.sh get "https://example.com" /tmp/output.md
```

适用场景：普通网页、博客、文档、微信文章、知乎等。

---

### smart - 智能抓取

自动选择最佳策略，与 `get` 命令相同。

```bash
./scripts/fetch.sh smart "https://example.com" /tmp/output.md
```

---

### fetch - 浏览器渲染

使用无头浏览器渲染页面，适合 React/Vue/Angular 等 SPA 应用。

```bash
./scripts/fetch.sh fetch "https://spa-app.com" /tmp/output.md
```

---

### stealthy - 绕过反爬

处理 Cloudflare 等反爬保护网站。

```bash
./scripts/fetch.sh stealthy "https://protected-site.com" /tmp/output.html
```

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
| `--timeout N` | curl 超时时间（秒） | 15 |
| `--retry N` | 失败重试次数 | 2 |

**示例**：
```bash
./scripts/fetch.sh get "https://example.com" /tmp/output.md \
    --ua "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)" \
    --timeout 20 \
    --retry 3
```

---

## 工作流程

```
get/smart <URL>
    │
    ├─1─► curl ──────────────────────► [成功] ──┐
    │                                    │       │
    │                              内容压缩        │
    │                                 │        │
    └─2─► scrapling HTTP ──► [成功] ──┴────────┤
    │                                         │
    └─3─► scrapling stealthy ──► [成功] ──────┘
                                               │
                                         输出结果
```

---

## 微信文章提取

Smart3W 支持自动识别并优化提取微信公众号文章。

**工作原理**：
- URL 包含 `mp.weixin.qq.com` → 自动使用 BeautifulSoup 提取 `id='js_content'` 的正文
- 其他网站 → 使用 readability-lxml 进行通用提取

**压缩效果**：

| 页面类型 | 原始 | 压缩后 | 节省 |
|----------|------|--------|------|
| 普通网页 | 100KB | 15KB | 85% |
| 微信文章 | 2.8MB | 841B | 99%+ |

---

## 内容压缩

默认启用 readability-lxml 提取正文，自动去除：
- 导航栏、侧边栏、页脚
- 广告、追踪脚本、CSS
- HTML 标签和多余空白

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
| 普通网页/博客/微信/知乎 | `get` |
| SPA 应用（React/Vue） | `fetch` |
| 反爬保护网站 | `stealthy` |
| 网页搜索 | `search` |
| 解析站点地图 | `sitemap` |
| 获取原始 HTML | `get --no-compress` |

---

## 项目结构

```
smart3w/
├── SKILL.md          # OpenClaw Skill 元数据
├── README.md         # 本文件
├── LICENSE           # MIT 许可证
├── .gitignore        # Git 忽略文件
└── scripts/
    ├── fetch.sh      # 统一入口脚本
    └── search.sh     # SearXNG 搜索脚本
```

---

## 许可证

MIT License
