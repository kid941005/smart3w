"""fetch.sh CLI 回归测试（不依赖真实网络）。"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BASH = shutil.which("bash")
ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not BASH, reason="需要 bash 才能测试 fetch.sh")


def _run(*args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
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


def test_unknown_command_fails():
    result = _run("nope")
    assert result.returncode == 1
    assert "未知命令" in result.stderr


def test_retry_rejects_non_positive_value():
    result = _run("get", "https://example.com", "--retry", "0")
    assert result.returncode == 1
    assert "正整数" in result.stderr
