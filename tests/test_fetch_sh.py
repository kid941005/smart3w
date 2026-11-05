"""fetch.sh CLI 回归测试（不依赖真实网络）。"""

import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BASH = shutil.which("bash")
ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not BASH, reason="需要 bash 才能测试 fetch.sh")


def _run(*args, extra_env=None):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [BASH, "scripts/fetch.sh", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


class _MarkdownHandler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        body = b"# Hello\n\nMarkdown body\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def markdown_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _MarkdownHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_script_has_valid_syntax():
    result = subprocess.run(
        [BASH, "-n", "scripts/fetch.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_help_lists_commands():
    result = _run("help")
    assert result.returncode == 0
    assert "search" in result.stdout
    assert "sitemap" in result.stdout


def test_get_without_url_fails():
    result = _run("get")
    assert result.returncode == 1
    assert "用法" in result.stderr


def test_search_without_query_fails():
    result = _run("search")
    assert result.returncode == 1
    assert "用法" in result.stderr


def test_search_invalid_count_exits_nonzero():
    result = _run("search", "q", "101")
    assert result.returncode == 1
    assert "count" in result.stdout


def test_recursive_only_for_sitemap():
    result = _run("get", "https://example.com", "--recursive")
    assert result.returncode == 1
    assert "仅用于 sitemap" in result.stderr


def test_get_uses_python_http_and_skips_markdown_compression(markdown_server, tmp_path):
    out = tmp_path / "page.md"
    result = _run(
        "get", markdown_server, str(out),
        extra_env={
            "SMART3W_PYTHON": sys.executable,
            "SMART3W_ALLOW_PRIVATE_IPS": "1",
            "NO_PROXY": "127.0.0.1,localhost",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "python-http" in result.stdout
    assert out.read_text(encoding="utf-8") == "# Hello\n\nMarkdown body\n"


def test_unknown_command_fails():
    result = _run("nope")
    assert result.returncode == 1
    assert "未知命令" in result.stderr


def test_retry_rejects_non_positive_value():
    result = _run("get", "https://example.com", "--retry", "0")
    assert result.returncode == 1
    assert "正整数" in result.stderr
