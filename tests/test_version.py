"""版本号单一来源与文档副本一致性测试。"""

from pathlib import Path

import mcp_server
import smart3w_fetch_utils
import smart3w_version

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_modules_use_version_module():
    assert mcp_server.__version__ == smart3w_version.__version__
    assert f"Smart3W/{smart3w_version.__version__}" in smart3w_fetch_utils.USER_AGENT


def test_readme_badge_matches_version():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Version-{smart3w_version.__version__}-blue" in text


def test_skill_version_matches():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert f"version: {smart3w_version.__version__}" in text


def test_pyproject_uses_dynamic_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in text
    assert 'version = {attr = "smart3w_version.__version__"}' in text
