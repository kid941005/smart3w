#!/usr/bin/env python3
"""Scrapling 浏览器抓取包装器（fetch / stealthy）。

保留 `scrapling extract fetch` / `stealthy-fetch` 的行为，但额外写出最终 URL
（effective URL）侧车文件。除了调用方抓取后的最终 URL 复检，本包装器还会在
导航前安装 Playwright 路由守卫：首个请求与浏览器内每一次子请求/重定向都会
按 smart3w_url_utils.url_policy_error 校验，命中内网/保留地址或非法 scheme 时
直接 abort；WebSocket 连接同样先校验 wss/ws 目标再决定是否放行。

退出码：0=成功；1=抓取失败；2=参数/环境错误（含首跳 URL 未通过策略）。
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from smart3w_url_utils import url_policy_error  # noqa: E402

_ALLOWED_NON_HTTP_SCHEMES = ("data", "blob", "about")


def _check_url(url: str) -> str | None:
    """对 http(s) 与 ws(s) 目标执行统一策略校验；ws 映射为 http 以复用规则。"""
    raw = (url or "").strip()
    try:
        scheme = urllib.parse.urlsplit(raw).scheme.lower()
    except ValueError:
        return "URL 格式无效"
    if scheme in ("ws", "wss"):
        raw = ("https" if scheme == "wss" else "http") + raw[len(scheme):]
    return url_policy_error(raw)


def _make_guards():
    """返回 (request_guard, websocket_guard)，供 page_setup 安装。

    按 origin 缓存策略结果，避免同一站点的图片/脚本/字体等每个子请求都做 DNS；
    形状校验仍逐 URL 执行，防止同 origin 的后续 URL 绕过长度/控制字符检查。
    """
    origin_cache: dict[str, str | None] = {}

    def _blocked(url: str) -> str | None:
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            return "URL 格式无效"
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https", "ws", "wss"):
            if scheme in _ALLOWED_NON_HTTP_SCHEMES:
                return None
            return f"禁止的请求 scheme: {scheme or '(empty)'}"
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        if origin in origin_cache:
            return origin_cache[origin]
        error = _check_url(url)
        origin_cache[origin] = error
        return error

    async def request_guard(route, request):
        error = _blocked(getattr(request, "url", ""))
        if error:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def websocket_guard(ws):
        error = _blocked(getattr(ws, "url", ""))
        if error:
            await ws.close(code=1008, reason="blocked by smart3w url policy")
            return
        ws.connect_to_server()

    return request_guard, websocket_guard


def _write_effective(path: str | None, url: str) -> None:
    """原子写入最终 URL；失败不阻断抓取结果。"""
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(url.strip() + "\n", encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scrapling_extract", description="Scrapling fetch wrapper")
    parser.add_argument("kind", choices=("fetch", "stealthy"), help="fetch=DynamicFetcher, stealthy=StealthyFetcher")
    parser.add_argument("url", help="目标 URL")
    parser.add_argument("output", help="输出文件（.html/.md/.txt）")
    parser.add_argument("--effective-url", default=None, help="最终 URL 侧车文件")
    parser.add_argument("--proxy", default=None, help="代理地址")
    parser.add_argument("--real-chrome", action="store_true", help="使用本机 Chrome")
    parser.add_argument("--headless", action="store_true", default=True, help="无头模式（默认开启）")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="显示浏览器窗口")
    parser.add_argument("--solve-cloudflare", action="store_true", help="stealthy 模式破解 Cloudflare")
    parser.add_argument("--timeout-ms", type=int, default=30000, help="浏览器超时（毫秒）")
    parser.add_argument("--wait-ms", type=int, default=0, help="页面加载后额外等待（毫秒）")
    args = parser.parse_args(argv)

    first_hop_error = _check_url(args.url)
    if first_hop_error:
        print(f"目标 URL 未通过策略校验: {first_hop_error}", file=sys.stderr)
        return 2

    try:
        from scrapling.core.shell import Convertor
        from scrapling.fetchers import DynamicFetcher, StealthyFetcher
    except Exception as exc:  # noqa: BLE001
        print(f"scrapling 不可用: {exc}", file=sys.stderr)
        return 2

    request_guard, websocket_guard = _make_guards()

    def _page_setup(page) -> None:
        page.route("**/*", request_guard)
        try:
            page.route_web_socket("**/*", websocket_guard)
        except Exception:  # noqa: BLE001 - 老版本 Playwright 无 WS 路由时降级为仅 HTTP 守卫
            pass

    kwargs = {
        "headless": bool(args.headless),
        "real_chrome": bool(args.real_chrome),
        "timeout": max(1, int(args.timeout_ms)),
        "disable_resources": False,
        "network_idle": False,
        "block_ads": False,
        "dns_over_https": False,
        "page_setup": _page_setup,
    }
    if args.wait_ms > 0:
        kwargs["wait"] = int(args.wait_ms)
    if args.proxy:
        kwargs["proxy"] = args.proxy

    try:
        if args.kind == "stealthy":
            kwargs.update(
                {
                    "solve_cloudflare": bool(args.solve_cloudflare),
                    "block_webrtc": False,
                    "allow_webgl": True,
                    "hide_canvas": False,
                }
            )
            response = StealthyFetcher.fetch(args.url, **kwargs)
        else:
            response = DynamicFetcher.fetch(args.url, **kwargs)

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Convertor.write_content_to_file(response, str(output_path))
        _write_effective(args.effective_url, str(getattr(response, "url", "") or args.url))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"抓取失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
