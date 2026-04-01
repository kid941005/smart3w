# Smart3W - 智能网页搜索与抓取工具

[![OpenClaw](https://img.shields.io/badge/OpenClaw-Compatible-green)](https://github.com/openclaw/openclaw)
[![MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

集 **SearXNG 网页搜索** 与 **智能网页抓取** 于一体的工具，支持自动降级和多种抓取策略。

---

## 核心功能

| 功能 | 说明 |
|------|------|
| 🔍 **网页搜索** | 基于 SearXNG，支持自定义实例，隐私友好 |
| ⚡ **HTTP 抓取** | 最快方式抓取静态页面 |
| 🌐 **浏览器渲染** | 支持 SPA / JS 重度页面 |
| 🛡️ **反爬绕过** | 自动处理 Cloudflare 等反爬保护 |
| 🔄 **智能降级** | 自动尝试最优方式，失败后逐级降级 |

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

## 使用方法

### 命令行使用

```bash
# ── 网页搜索 ──
./scripts/fetch.sh search "关键词" [结果数量]

# ── 智能抓取（推荐）──
./scripts/fetch.sh smart "https://example.com" /tmp/output.md

# ── HTTP 抓取（静态页面）──
./scripts/fetch.sh get "https://example.com" /tmp/output.md

# ── 浏览器渲染（SPA / JS 页面）──
./scripts/fetch.sh fetch "https://spa-site.com" /tmp/output.md --headless

# ── 反爬保护网站 ──
./scripts/fetch.sh stealthy "https://protected-site.com" /tmp/output.html --solve-cloudflare
```

---

## 工作流程

```
用户请求
    │
    ├── search → SearXNG 搜索 → 返回标题/URL/摘要
    │
    └── fetch
            │
            1. scrapling extract get (HTTP 请求，最快)
            │      ✅ 成功 → 返回内容
            │      ❌ 失败
            2. scrapling stealthy-fetch (无头浏览器，绕过反爬)
                   ✅ 成功 → 返回内容
                   ❌ 失败 → 提示用户
```

---

## 输出格式

根据文件后缀自动选择：

| 后缀 | 格式 |
|------|------|
| `.md` | Markdown（推荐） |
| `.txt` | 纯文本 |
| `.html` | 原始 HTML |

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

## 在 OpenClaw Agent 中使用

直接对 AI 说：
- "帮我搜索 XXX 相关信息"
- "抓取 https://example.com 的内容"
- "获取这个网页的正文"

AI 会根据请求类型自动选择 search 或 fetch 模式。

---

## 依赖

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 运行环境 |
| scrapling | ≥ 0.4.2 | 网页抓取核心 |
| Playwright | - | 浏览器渲染支持 |

```bash
# 安装依赖
pip install "scrapling[all]>=0.4.2"
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
