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


class _RedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        if self.path == "/final":
            body = b"REDIRECTED"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/to-bad":
            self.send_response(302)
            self.send_header("Location", "ftp://127.0.0.1/evil")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture()
def redirect_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _curl_only_python(tmp_path):
    """包装 Python：策略/URL 解析透传真实解释器，普通抓取失败以触发 curl 回退。"""
    wrapper = tmp_path / "python-curl-only.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'case " $* " in\n'
        '  *" --check-policy "*|*" --resolve-url "*|*" --engine curl "*) '
        f'exec "{Path(sys.executable).as_posix()}" "$@" ;;\n'
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


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


def test_curl_fallback_follows_redirect_with_per_hop_policy(redirect_server, tmp_path):
    out = tmp_path / "page.txt"
    result = _run(
        "get", redirect_server + "/start", str(out), "--no-compress",
        extra_env={
            "SMART3W_PYTHON": str(_curl_only_python(tmp_path)),
            "SMART3W_ALLOW_PRIVATE_IPS": "1",
            "NO_PROXY": "127.0.0.1,localhost",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "curl" in result.stdout
    assert out.read_text(encoding="utf-8") == "REDIRECTED"


def test_curl_fallback_blocks_redirect_to_disallowed_scheme(redirect_server, tmp_path):
    out = tmp_path / "page.txt"
    result = _run(
        "get", redirect_server + "/to-bad", str(out), "--no-compress",
        extra_env={
            "SMART3W_PYTHON": str(_curl_only_python(tmp_path)),
            "SMART3W_ALLOW_PRIVATE_IPS": "1",
            "NO_PROXY": "127.0.0.1,localhost",
        },
    )

    assert result.returncode == 1
    assert "安全策略" in result.stderr
    assert not out.exists()


def test_unknown_command_fails():
    result = _run("nope")
    assert result.returncode == 1
    assert "未知命令" in result.stderr


def test_retry_rejects_non_positive_value():
    result = _run("get", "https://example.com", "--retry", "0")
    assert result.returncode == 1
    assert "正整数" in result.stderr
