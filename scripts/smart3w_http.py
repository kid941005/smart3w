#!/usr/bin/env python3
"""轻量 HTTP 抓取器：逐跳校验重定向、SSRF/robots，并优先接受 Markdown。

实现参考 modelcontextprotocol/servers/src/fetch 的核心语义：
- 每个 redirect hop 都做 URL 安全校验（SMART3W_ALLOWED_HOSTS + 私网 SSRF）
- 可选地在每个 hop 检查 robots.txt
- 请求头优先 Accept: text/markdown，站点支持时直接返回 Markdown
- 支持标准 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 环境变量
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from pathlib import Path

# 从 scripts/ 运行时也能 import 仓库根目录的公共模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import httpx
except ImportError:  # 由 fetch.sh 捕获并回退到 curl
    print("httpx 未安装，无法使用 Python HTTP 抓取", file=sys.stderr)
    sys.exit(3)

from smart3w_url_utils import env_flag, ssrf_blocked  # noqa: E402

try:
    from smart3w_fetch_utils import RobotsChecker  # noqa: E402
except Exception:  # pragma: no cover - 极端环境缺少依赖时只关闭 robots 检查
    RobotsChecker = None  # type: ignore[assignment]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MARKDOWN_ACCEPT = "text/markdown, text/html;q=0.9, */*;q=0.8"
PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)


class BlockedURLError(RuntimeError):
    """URL 被 allowlist、SSRF 或 robots 规则拒绝。"""


def _normalize_proxy_env() -> None:
    """httpx 不接受 socks://，统一改写为 socks5://；同时清理 NO_PROXY 的方括号。"""
    for name in PROXY_ENV_VARS:
        value = os.environ.get(name)
        if value and value.lower().startswith("socks://"):
            os.environ[name] = "socks5://" + value[len("socks://") :]
    for name in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(name)
        if not value:
            continue
        parts = []
        for part in value.split(","):
            item = part.strip()
            if item.startswith("[") and item.endswith("]"):
                item = item[1:-1]
            if item:
                parts.append(item)
        os.environ[name] = ",".join(parts)


def _write_effective(path: str | None, url: str) -> None:
    if not path:
        return
    try:
        Path(path).write_text(url.rstrip("\n") + "\n", encoding="utf-8")
    except OSError:
        # 侧车文件只是给上层复查用，写失败不影响正文抓取
        pass


def _write_body(output: str, chunks) -> None:
    """先写 .part 再原子替换，避免异常时留下被误判为成功的半截文件。"""
    part = output + ".part"
    try:
        with open(part, "wb") as fh:
            for chunk in chunks:
                fh.write(chunk)
        os.replace(part, output)
    except Exception:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise


def fetch_url(
    url: str,
    output: str | None = None,
    *,
    timeout: int = 15,
    user_agent: str = DEFAULT_USER_AGENT,
    max_redirects: int = 10,
    effective_url_path: str | None = None,
    respect_robots: bool = False,
    validate_only: bool = False,
) -> str:
    """抓取 URL，返回最终响应的 Content-Type；失败抛异常。"""
    # 先做首跳校验：既避免私网请求，也避免在代理配置异常时先触发 httpx 报错
    blocked = ssrf_blocked(url)
    if blocked:
        raise BlockedURLError(blocked)
    _normalize_proxy_env()
    timeout = max(1, int(timeout))
    max_redirects = max(0, int(max_redirects))
    checker = RobotsChecker() if respect_robots and RobotsChecker is not None else None
    headers = {
        "User-Agent": user_agent,
        "Accept": MARKDOWN_ACCEPT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }

    current = url
    with httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
        trust_env=True,
        headers=headers,
    ) as client:
        for _ in range(max_redirects + 1):
            blocked = ssrf_blocked(current)
            if blocked:
                raise BlockedURLError(blocked)
            if checker is not None and not checker.allows(current):
                raise BlockedURLError(f"robots.txt 禁止抓取: {current}")

            with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise httpx.HTTPError(f"重定向缺少 Location: {current}")
                    current = urllib.parse.urljoin(str(response.url), location)
                    continue

                response.raise_for_status()
                if validate_only:
                    _write_effective(effective_url_path, str(response.url))
                    return ""

                if not output:
                    raise ValueError("output 不能为空")
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                _write_body(output, response.iter_bytes())
                _write_effective(effective_url_path, str(response.url))
                return response.headers.get("content-type", "")

    raise BlockedURLError(f"重定向次数超过上限 {max_redirects}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smart3W lightweight HTTP fetcher")
    parser.add_argument("url")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--ua", default=DEFAULT_USER_AGENT)
    parser.add_argument("--max-redirs", type=int, default=10)
    parser.add_argument("--effective-url", default=None)
    parser.add_argument("--respect-robots", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    if not args.validate_only and not args.output:
        print("output 不能为空", file=sys.stderr)
        return 2

    respect_robots = args.respect_robots or env_flag("SMART3W_RESPECT_ROBOTS", False)
    try:
        content_type = fetch_url(
            args.url,
            args.output,
            timeout=args.timeout,
            user_agent=args.ua,
            max_redirects=args.max_redirs,
            effective_url_path=args.effective_url,
            respect_robots=respect_robots,
            validate_only=args.validate_only,
        )
        if content_type:
            print(content_type)
        return 0
    except BlockedURLError as exc:
        print(f"URL 被拒绝: {exc}", file=sys.stderr)
        return 2
    except httpx.TimeoutException as exc:
        print(f"抓取超时: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"HTTP 抓取失败: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"抓取失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
