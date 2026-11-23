"""Playwright 真实浏览器集成测试。

没有可用的 Chrome/Chromium 时自动跳过；需要走本地 HTTP 服务，因此只在
设置 SMART3W_ALLOW_PRIVATE_IPS=1 的用例中放行 127.0.0.1。
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.sync_api")

from smart3w_interact import InteractionError, find_chrome, run_interaction


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
        body = b"""<!doctype html><html><head><title>Demo</title></head><body>
        <form action="/done" method="get">
          <input id="name" name="q" />
          <button id="go" type="submit">Go</button>
        </form></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默测试日志
        return


@pytest.fixture()
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _require_chrome():
    if not find_chrome():
        pytest.skip("本机没有 Chrome，跳过真实浏览器集成测试")


def test_browser_blocks_private_target_by_default(local_server, monkeypatch):
    _require_chrome()
    monkeypatch.delenv("SMART3W_ALLOW_PRIVATE_IPS", raising=False)
    with pytest.raises(InteractionError) as exc:
        run_interaction(local_server, timeout=10, wait_ms=0)
    assert exc.value.code == "SSRF_BLOCKED"


def test_browser_click_fill_and_navigate(local_server, monkeypatch):
    _require_chrome()
    monkeypatch.setenv("SMART3W_ALLOW_PRIVATE_IPS", "1")
    result = run_interaction(
        local_server,
        actions=[
            {"type": "fill", "selector": "#name", "value": "abc"},
            {"type": "click", "selector": "#go"},
            {"type": "wait_for_load"},
        ],
        timeout=15,
        wait_ms=100,
        content_format="text",
    )
    assert result["title"] == "Demo"
    assert result["final_url"].startswith(local_server + "done")
    assert "q=abc" in result["final_url"]
    assert result["action_count"] == 3
