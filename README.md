# Smart3W - 智能网页搜索与抓取工具

[![OpenClaw](https://img.shields.io/badge/OpenClaw-Compatible-green)](https://github.com/openclaw/openclaw)
[![MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

集 **SearXNG 网页搜索**、**Sitemap 索引解析** 与 **智能网页抓取** 于一体的工具，支持并行竞争、内容压缩和 Cloudflare 智能检测。

---

## 核心功能

| 功能 | 说明 |
|------|------|
| 🔍 **网页搜索** | 基于 SearXNG，支持自定义实例，隐私友好 |
| 🗺️ **Sitemap 解析** | 自动解析 Sitemap Index 和 URL Set，返回完整 URL 列表 |
| ⚡ **HTTP 抓取** | 最快方式抓取静态页面 |
| 🌐 **浏览器渲染** | 支持 SPA / JS 重度页面 |
| 🛡️ **反爬绕过** | 自动处理 Cloudflare 等反爬保护 |
| 📦 **内容压缩** | readability-lxml 自动提取正文，节省 50-80% token |
| 🏁 **并行竞争** | smart 模式 HTTP 与 stealthy 同时跑，先完成者胜出 |
| 🧠 **CF 预检测** | 动态选择是否启用 --solve-cloudflare |

---

## 安装

### 方式一：克隆到 OpenClaw Skills 目录

```bash
git clone https://github.com/kid941005/smart3w.git ~/.openclaw/skills/smart3w
```

### 方式二：从其他 Skill 集成

```bash
# 复制脚本到你的 Skill 目录
cp -r scripts/ /path/to/your-skill/
```

---

## 命令行使用

### 🔍 网页搜索

```bash
# SearXNG 搜索，返回 JSON（标题/URL/摘要）
./scripts/fetch.sh search "关键词" [结果数量]

# 示例
./scripts/fetch.sh search "OpenClaw AI assistant" 5
```

**输出示例**：
```json
{
  "success": true,
  "query": "OpenClaw AI assistant",
  "results": [
    { "title": "OpenClaw — Personal AI Assistant", "url": "https://openclaw.ai/", "snippet": "..." }
  ],
  "result_count": 5
}
```

---

### 📦 智能抓取（推荐默认）

```bash
# 自动并行竞争 + 内容压缩（默认）
./scripts/fetch.sh smart "https://example.com" /tmp/output.md

# 获取原始 HTML（跳过压缩）
./scripts/fetch.sh smart "https://example.com" /tmp/output.html --no-compress
```

**工作流程**：
```
smart <URL>
  ├── [预检测] Cloudflare 防护检测（约0.5s）
  │
  ├── HTTP 抓取 (5s 超时)  ─┐
  │                        │ 并行竞速
  └── 隐身浏览器 (10-15s) ─┘
              ↓
       谁先完成谁胜出
              ↓
         readability-lxml 压缩
              ↓
         返回干净正文文本
```

---

### 🗺️ Sitemap 索引解析

```bash
# 解析站点地图，返回 URL 列表（默认50条）
./scripts/fetch.sh sitemap "https://example.com/sitemap.xml"

# 指定最大条数
./scripts/fetch.sh sitemap "https://example.com/sitemap.xml" 100
```

**支持两种格式**：
- **Sitemap Index**（索引文件）→ 返回子 sitemap 文件 URL
- **URL Set**（页面列表）→ 返回具体页面 URL + 最后更新时间

**输出示例**：
```
=== Sitemap 索引解析 ===
目标: https://www.cnblogs.com/sitemap.xml
✅ 共解析到 40231 个URL

    1. 📋 [index] https://www.cnblogs.com/xuexi2026/sitemap.xml
    2. 📋 [index] https://www.cnblogs.com/lnlidawei/sitemap.xml
    3. 📋 [index] https://www.cnblogs.com/zkry-msyg/sitemap.xml
...
```

---

### ⚡ HTTP 抓取（静态页面）

```bash
# 最快方式，适合普通网页、博客、文档
./scripts/fetch.sh get "https://example.com" /tmp/output.md

# 获取原始 HTML
./scripts/fetch.sh get "https://example.com" /tmp/output.html --no-compress
```

---

### 🌐 浏览器渲染抓取（SPA / JS 页面）

```bash
# 使用无头浏览器渲染，适合 React/Vue/Angular 等 SPA 应用
./scripts/fetch.sh fetch "https://spa-site.com" /tmp/output.md
```

---

### 🛡️ 隐身浏览器（绕过反爬）

```bash
# 绕过 Cloudflare 等反爬保护（自动选择参数）
./scripts/fetch.sh stealthy "https://protected-site.com" /tmp/output.html
```

---

## 内容压缩

默认启用 **readability-lxml** 提取正文，自动去除：
- 导航栏、侧边栏、页脚
- 广告、追踪脚本、CSS
- HTML 标签和多余空白

**压缩效果示例**：

| 页面 | 原始大小 | 压缩后 | 节省 |
|------|----------|--------|------|
| example.com | 512B | 126B | **76%** |
| 复杂文档页面 | 121KB | 18KB | **86%** |

**压缩汇报**：
```
✅ 抓取成功 → /tmp/output.md
   原始: 121439B | 压缩后: 18208B | 保留: 14%
```

**跳过压缩**：
```bash
./scripts/fetch.sh get "https://example.com" /tmp/output.html --no-compress
```

---

## Cloudflare 预检测

smart 模式启动前会快速检测目标是否有 Cloudflare 防护：

| 检测结果 | stealthy 参数 | 超时 |
|----------|---------------|------|
| 有 CF 防护 | `--headless --solve-cloudflare` | 15s |
| 无 CF / CDN缓存HIT | `--headless`（跳过解CF） | 10s |
| 两个都失败 + 有CF记录 | 最后强制 `--solve-cloudflare` | 20s |

---

## 场景选择指南

| 场景 | 推荐方式 | 命令 |
|------|----------|------|
| 网页搜索 | `search` | `fetch.sh search "关键词"` |
| 站点地图解析 | `sitemap` | `fetch.sh sitemap <url>` |
| 普通网页/博客 | `smart` | `fetch.sh smart <URL>`（推荐） |
| 普通网页（无压缩） | `smart` | `fetch.sh smart <URL> --no-compress` |
| SPA / JS 重度页面 | `fetch` | `fetch.sh fetch <URL>` |
| 反爬保护 / 登录页 | `stealthy` | `fetch.sh stealthy <URL>` |
| 需要原始 HTML | 任意 + `--no-compress` | `fetch.sh get <URL> --no-compress` |

---

## 在 OpenClaw Agent 中使用

直接对 AI 说：
- "帮我搜索 XXX 相关信息"
- "抓取 https://example.com 的内容"
- "获取这个网页的正文"
- "解析这个站点的 sitemap：https://example.com/sitemap.xml"

AI 会根据请求类型自动选择合适的模式。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SEARXNG_INSTANCE` | `https://searxng.example.com:59826` | SearXNG 实例地址 |

### 示例：使用自定义 SearXNG 实例

```bash
SEARXNG_INSTANCE="https://your-searxng.example.com" ./scripts/fetch.sh search "关键词"
```

---

## 依赖

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 运行环境 |
| scrapling | ≥ 0.4.2 | 网页抓取核心 |
| readability-lxml | ≥ 0.8.0 | 正文压缩（自动安装） |
| Playwright | - | 浏览器渲染支持 |

```bash
# 安装依赖
pip install "scrapling[all]>=0.4.2" "readability-lxml>=0.8.0"
scrapling install --force
```

---

## 项目结构

```
smart3w/
├── SKILL.md          # OpenClaw Skill 元数据
├── README.md         # 本文件
├── LICENSE           # MIT 许可证
└── scripts/
    ├── fetch.sh      # 统一入口脚本
    └── search.sh     # SearXNG 搜索脚本
```

---

## 许可证

MIT License
