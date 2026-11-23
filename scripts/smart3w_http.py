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
except ImportError:  # 延迟到实际抓取时再提示，--check-policy 仍可用
    httpx = None  # type: ignore[assignment]

from smart3w_url_utils import env_flag, host_bypasses_proxy, proxy_url, url_policy_error  # noqa: E402

try:
    from smart3w_fetch_utils import RobotsChecker  # noqa: E402
except ImportError:  # pragma: no cover - 极端环境缺少依赖时只关闭 robots 检查
    RobotsChecker = None  # type: ignore[assignment]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MARKDOWN_ACCEPT = "text/markdown, text/html;q=0.9, */*;q=0.8"
HTML_ACCEPT = "text/html, application/xhtml+xml, */*;q=0.8"


class BlockedURLError(RuntimeError):
    """URL 被 allowlist、SSRF 或 robots 规则拒绝。"""


class HTTPXUnavailable(RuntimeError):
    """运行环境未安装 httpx；fetch.sh 会回退到 curl。"""


class DownloadTooLarge(RuntimeError):
    """响应体超过下载上限，避免恶意大文件写满磁盘。"""


DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _max_download_bytes() -> int:
    """读取下载上限；非正整数回退 50MB。"""
    raw = os.environ.get("SMART3W_MAX_DOWNLOAD_BYTES")
    if raw is None:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_DOWNLOAD_BYTES
    return value if value > 0 else DEFAULT_MAX_DOWNLOAD_BYTES


def _url_policy_error(url: str) -> str | None:
    """兼容内部调用名；实际校验逻辑统一在 smart3w_url_utils。"""
    return url_policy_error(url)


def _httpx_proxy_url() -> str | None:
    """返回 httpx 可接受的显式代理地址；socks5h 转为 socks5。"""
    raw = proxy_url()
    if not raw:
        return None
    lower = raw.lower()
    if lower.startswith("socks5h://"):
        return "socks5://" + raw[len("socks5h://") :]
    if lower.startswith("socks://"):
        return "socks5://" + raw[len("socks://") :]
    return raw


def _env_proxy_url(url: str) -> str | None:
    """按目标 scheme 读取环境代理；不修改 os.environ，避免污染同进程其他 httpx 客户端。"""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    keys = (
        ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
        if scheme == "https"
        else ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
    )
    for key in keys:
        value = (os.environ.get(key) or "").strip()
        if value:
            lower = value.lower()
            if lower.startswith("socks5h://"):
                return "socks5://" + value[len("socks5h://") :]
            if lower.startswith("socks://"):
                return "socks5://" + value[len("socks://") :]
            return value
    return None


def _proxy_for(url: str) -> str | None:
    """显式配置优先；命中 NO_PROXY 的目标直连。"""
    host = urllib.parse.urlsplit(url).hostname or ""
    if host_bypasses_proxy(host):
        return None
    return _httpx_proxy_url() or _env_proxy_url(url)


def _write_effective(path: str | None, url: str) -> None:
    if not path:
        return
    try:
        Path(path).write_text(url.rstrip("\n") + "\n", encoding="utf-8")
    except OSError:
        # 侧车文件只是给上层复查用，写失败不影响正文抓取
        pass


def _write_body(output: str, chunks, max_bytes: int | None = None) -> None:
    """先写 .part 再原子替换，避免异常时留下被误判为成功的半截文件。"""
    part = output + ".part"
    try:
        with open(part, "wb") as fh:
            total = 0
            for chunk in chunks:
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise DownloadTooLarge(f"响应体超过下载上限 {max_bytes} 字节")
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
    blocked = _url_policy_error(url)
    if blocked:
        raise BlockedURLError(blocked)
    if httpx is None:
        raise HTTPXUnavailable("httpx 未安装，无法使用 Python HTTP 抓取")
    timeout = max(1, int(timeout))
    max_redirects = max(0, int(max_redirects))
    checker = RobotsChecker() if respect_robots and RobotsChecker is not None else None
    headers = {
        "User-Agent": user_agent,
        "Accept": HTML_ACCEPT if env_flag("SMART3W_ACCEPT_HTML", False) else MARKDOWN_ACCEPT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }

    current = url
    for _ in range(max_redirects + 1):
        blocked = _url_policy_error(current)
        if blocked:
            raise BlockedURLError(blocked)
        if checker is not None and not checker.allows(current):
            raise BlockedURLError(f"robots.txt 禁止抓取: {current}")

        # 每跳单独建客户端：重定向可能改变 host，NO_PROXY/代理选择要随之变化；
        # trust_env=False 避免 httpx 解析 NO_PROXY 中的 [::1] 时报 Invalid port。
        with httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
            proxy=_proxy_for(current),
            trust_env=False,
            verify=env_flag("SMART3W_SSL_VERIFY", True),
            headers=headers,
        ) as client:
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
                max_bytes = _max_download_bytes()
                declared_length = response.headers.get("content-length")
                if declared_length:
                    try:
                        if int(declared_length) > max_bytes:
                            raise DownloadTooLarge(f"响应体超过下载上限 {max_bytes} 字节")
                    except ValueError:
                        pass
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                _write_body(output, response.iter_bytes(), max_bytes)
                _write_effective(effective_url_path, str(response.url))
                return response.headers.get("content-type", "")

    raise BlockedURLError(f"重定向次数超过上限 {max_redirects}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smart3W lightweight HTTP fetcher")
    parser.add_argument("url", nargs="?")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--ua", default=DEFAULT_USER_AGENT)
    parser.add_argument("--max-redirs", type=int, default=10)
    parser.add_argument("--effective-url", default=None)
    parser.add_argument("--respect-robots", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--check-policy", action="store_true")
    parser.add_argument("--check-robots", action="store_true")
    parser.add_argument("--resolve-url", nargs=2, metavar=("BASE", "LOCATION"))
    args = parser.parse_args(argv)

    if args.resolve_url:
        print(urllib.parse.urljoin(args.resolve_url[0], args.resolve_url[1]))
        return 0
    if not args.url:
        parser.error("url 不能为空")

    if args.check_policy:
        error = _url_policy_error(args.url)
        if error:
            print(f"URL 被拒绝: {error}", file=sys.stderr)
            return 2
        return 0

    if args.check_robots:
        error = _url_policy_error(args.url)
        if error:
            print(f"URL 被拒绝: {error}", file=sys.stderr)
            return 2
        if RobotsChecker is not None and not RobotsChecker().allows(args.url):
            print(f"robots.txt 禁止抓取: {args.url}", file=sys.stderr)
            return 2
        return 0

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
    except HTTPXUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except DownloadTooLarge as exc:
        print(f"抓取失败: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if httpx is not None and isinstance(exc, httpx.TimeoutException):
            print(f"抓取超时: {exc}", file=sys.stderr)
        elif httpx is not None and isinstance(exc, httpx.HTTPError):
            print(f"HTTP 抓取失败: {exc}", file=sys.stderr)
        else:
            print(f"抓取失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
