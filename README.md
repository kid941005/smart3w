# Smart Fetch - 智能网页抓取路由技能

[![AgentSkill](https://img.shields.io/badge/AgentSkill-1.0-blue)](https://agentskills.io)
[![OpenClaw](https://img.shields.io/badge/OpenClaw-Compatible-green)](https://github.com/openclaw/openclaw)

自动选择最佳方式抓取网页内容，无需手动判断。支持自动回退：先尝试简单 HTTP，失败则自动升级到隐身浏览器。

## 功能特点

- 🔄 **自动回退**：先 `scrapling get`，失败自动切换到 `stealthy-fetch`
- 🛡️ **反爬绕过**：自动处理 Cloudflare 等反爬保护
- ⚡ **智能选择**：根据网站特性自动选择最佳抓取方式
- 📝 **多格式输出**：支持 Markdown、纯文本、HTML

## 安装

```bash
# 克隆到技能目录
git clone https://github.com/qaz364/smart-fetch-skill.git ~/.openclaw/workspace/.agents/skills/smart-fetch
```

## 使用方法

### 在 OpenClaw 中使用

直接对 AI 说：
- "抓取 https://example.com"
- "帮我获取这个网页的内容"

AI 会自动：
1. 先尝试 `scrapling extract get`
2. 失败则切换到 `scrapling stealthy-fetch`

### 命令行使用

```bash
# 使用封装脚本
~/.openclaw/workspace/.agents/skills/smart-fetch/scripts/fetch.sh "https://example.com"

# 或手动执行
scrapling extract get "https://example.com" output.md
scrapling extract stealthy-fetch "https://protected-site.com" output.html --headless
```

## 工作流程

```
用户请求 URL
    ↓
┌─────────────────────┐
│ 1. scrapling get    │ (HTTP请求，快)
└──────────┬──────────┘
           ↓
    成功 → 返回内容
           │
    失败 ↓
┌─────────────────────┐
│ 2. stealthy-fetch   │ (无头浏览器)
└──────────┬──────────┘
           ↓
    成功 → 返回内容
           │
    失败 ↓
    提示用户
```

## 依赖

- Python 3.10+
- scrapling >= 0.4.2
- Playwright 浏览器

```bash
pip install "scrapling[all]>=0.4.2"
scrapling install --force
```

## 技能规范

本技能遵循 [AgentSkill](https://agentskills.io/specification) 规范，兼容 OpenClaw、Claude Code 等智能体工具。

## 许可证

MIT License
