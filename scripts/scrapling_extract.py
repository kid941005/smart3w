#!/usr/bin/env python3
"""Scrapling 浏览器抓取包装器（fetch / stealthy）。

保留 `scrapling extract fetch` / `stealthy-fetch` 的行为，但额外写出最终 URL
（effective URL）侧车文件。浏览器内部重定向无法在导航前逐跳拦截，因此由调用方
在抓取完成后对最终 URL 做安全复检，缩小 SSRF 风险窗口。

退出码：0=成功；1=抓取失败；2=参数/环境错误。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


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

    try:
        from scrapling.core.shell import Convertor
        from scrapling.fetchers import DynamicFetcher, StealthyFetcher
    except Exception as exc:  # noqa: BLE001
        print(f"scrapling 不可用: {exc}", file=sys.stderr)
        return 2

    kwargs = {
        "headless": bool(args.headless),
        "real_chrome": bool(args.real_chrome),
        "timeout": max(1, int(args.timeout_ms)),
        "disable_resources": False,
        "network_idle": False,
        "block_ads": False,
        "dns_over_https": False,
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
