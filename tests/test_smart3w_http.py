"""smart3w_http 轻量抓取器测试（本地 HTTP 服务，不访问外网）。"""

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import smart3w_http  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        if self.path == "/final":
            body = b"<html><body><h1>Hello</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/markdown":
            body = b"# Hello\n\nMarkdown body\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/to-private":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture()
def local_server(monkeypatch):
    monkeypatch.setenv("SMART3W_ALLOW_PRIVATE_IPS", "1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.delenv("SMART3W_ALLOWED_HOSTS", raising=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_fetch_follows_redirect_and_records_effective_url(local_server, tmp_path):
    output = tmp_path / "page.html"
    effective = tmp_path / "page.effective"
    content_type = smart3w_http.fetch_url(
        local_server + "/start",
        str(output),
        timeout=5,
        effective_url_path=str(effective),
    )

    assert output.read_text(encoding="utf-8") == "<html><body><h1>Hello</h1></body></html>"
    assert effective.read_text(encoding="utf-8").strip() == local_server + "/final"
    assert content_type.startswith("text/html")


def test_markdown_content_type_is_returned(local_server, tmp_path):
    output = tmp_path / "page.md"
    content_type = smart3w_http.fetch_url(local_server + "/markdown", str(output), timeout=5)

    assert output.read_text(encoding="utf-8") == "# Hello\n\nMarkdown body\n"
    assert content_type.startswith("text/markdown")


def test_redirect_to_private_address_is_blocked(local_server, tmp_path, monkeypatch):
    monkeypatch.delenv("SMART3W_ALLOW_PRIVATE_IPS", raising=False)
    output = tmp_path / "page.html"
    with pytest.raises(smart3w_http.BlockedURLError):
        smart3w_http.fetch_url(local_server + "/to-private", str(output), timeout=5)
    assert not output.exists()


def test_direct_private_url_is_blocked(tmp_path, monkeypatch):
    monkeypatch.delenv("SMART3W_ALLOW_PRIVATE_IPS", raising=False)
    with pytest.raises(smart3w_http.BlockedURLError):
        smart3w_http.fetch_url("http://169.254.169.254/latest", str(tmp_path / "x.html"), timeout=5)


def test_allowlist_blocks_non_listed_host(local_server, tmp_path, monkeypatch):
    monkeypatch.setenv("SMART3W_ALLOWED_HOSTS", "example.com")
    with pytest.raises(smart3w_http.BlockedURLError):
        smart3w_http.fetch_url(local_server + "/final", str(tmp_path / "x.html"), timeout=5)
