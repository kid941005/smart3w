import asyncio
import sys

import pytest

import mcp_server
import smart3w_interact as interact


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.page.calls.append(("click", self.selector, timeout))

    def fill(self, value, timeout=None):
        self.page.calls.append(("fill", self.selector, value, timeout))

    def press(self, key, timeout=None):
        self.page.calls.append(("press", self.selector, key, timeout))

    def select_option(self, value, timeout=None):
        self.page.calls.append(("select", self.selector, value, timeout))

    def check(self, timeout=None):
        self.page.calls.append(("check", self.selector, timeout))

    def uncheck(self, timeout=None):
        self.page.calls.append(("uncheck", self.selector, timeout))

    def wait_for(self, state="visible", timeout=None):
        self.page.calls.append(("wait_for", self.selector, state, timeout))


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        self.page.calls.append(("keyboard", key))


class FakeMouse:
    def __init__(self, page):
        self.page = page

    def wheel(self, x, y):
        self.page.calls.append(("wheel", x, y))


class FakePage:
    def __init__(self):
        self.calls = []
        self.keyboard = FakeKeyboard(self)
        self.mouse = FakeMouse(self)

    def locator(self, selector):
        return FakeLocator(self, selector)

    def get_by_text(self, text, exact=False):
        self.calls.append(("get_by_text", text, exact))
        return FakeLocator(self, f"text={text}")

    def goto(self, url, wait_until=None, timeout=None):
        self.calls.append(("goto", url, wait_until, timeout))

    def wait_for_timeout(self, ms):
        self.calls.append(("wait_for_timeout", ms))

    def wait_for_load_state(self, state, timeout=None):
        self.calls.append(("wait_for_load_state", state, timeout))


def test_normalize_actions_accepts_whitelist():
    actions = interact.normalize_actions([
        {"type": "click", "selector": "#login"},
        {"action": "fill", "selector": "input[name=q]", "value": "hello"},
        {"type": "click_text", "text": "提交", "exact": True},
        {"type": "press", "key": "Enter"},
        {"type": "select", "selector": "#page", "value": "2"},
        {"type": "wait", "ms": 100},
        {"type": "wait_for_load", "state": "networkidle"},
        {"type": "scroll", "to": "bottom"},
        {"type": "goto", "url": "https://example.com/next"},
    ])
    assert [item["type"] for item in actions] == [
        "click", "fill", "click_text", "press", "select", "wait",
        "wait_for_load", "scroll", "goto",
    ]
    assert actions[1]["value"] == "hello"


def test_normalize_actions_rejects_unknown_field_and_type():
    with pytest.raises(interact.InteractionError) as exc:
        interact.normalize_actions([{"type": "click", "selector": "#a", "js": "alert(1)"}])
    assert exc.value.code == "INVALID_ARGUMENT"

    with pytest.raises(interact.InteractionError) as exc:
        interact.normalize_actions([{"type": "evaluate", "script": "1"}])
    assert exc.value.code == "INVALID_ARGUMENT"


def test_normalize_actions_rejects_too_many_and_missing_fields():
    with pytest.raises(interact.InteractionError):
        interact.normalize_actions([{"type": "wait", "ms": 1}] * (interact.MAX_ACTIONS + 1))
    with pytest.raises(interact.InteractionError):
        interact.normalize_actions([{"type": "wait"}])
    with pytest.raises(interact.InteractionError):
        interact.normalize_actions([{"type": "click"}])


def test_normalize_actions_checks_goto_policy(monkeypatch):
    monkeypatch.setattr(interact, "ssrf_blocked", lambda url: "目标主机解析到内网地址")
    with pytest.raises(interact.InteractionError) as exc:
        interact.normalize_actions([{"type": "goto", "url": "http://127.0.0.1/"}])
    assert exc.value.code == "SSRF_BLOCKED"


def test_execute_actions_drives_playwright_surface():
    page = FakePage()
    actions = interact.normalize_actions([
        {"type": "goto", "url": "https://example.com/login"},
        {"type": "fill", "selector": "input[name=user]", "value": "alice", "wait_ms": 0},
        {"type": "press", "key": "Tab", "wait_ms": 0},
        {"type": "click", "selector": "button[type=submit]", "wait_ms": 0},
        {"type": "click_text", "text": "下一页", "wait_ms": 0},
        {"type": "select", "selector": "#page", "value": "2", "wait_ms": 0},
        {"type": "check", "selector": "#remember", "wait_ms": 0},
        {"type": "uncheck", "selector": "#remember", "wait_ms": 0},
        {"type": "wait", "selector": ".result", "wait_ms": 0},
        {"type": "scroll", "y": 800, "wait_ms": 0},
        {"type": "wait_for_load", "state": "load", "wait_ms": 0},
    ])
    done = interact._execute_actions(page, actions, default_wait_ms=0)
    assert done == [item["type"] for item in actions]
    assert ("goto", "https://example.com/login", "domcontentloaded", None) in page.calls
    assert ("fill", "input[name=user]", "alice", None) in page.calls
    assert ("keyboard", "Tab") in page.calls
    assert ("get_by_text", "下一页", False) in page.calls
    assert ("select", "#page", "2", None) in page.calls
    assert ("wait_for", ".result", "visible", None) in page.calls
    assert ("wheel", 0, 800) in page.calls


def test_execute_actions_wraps_failure():
    class BrokenPage(FakePage):
        def goto(self, *args, **kwargs):
            raise RuntimeError("boom")

    with pytest.raises(interact.InteractionError) as exc:
        interact._execute_actions(BrokenPage(), [{"type": "goto", "url": "https://example.com"}], 0)
    assert exc.value.code == "ACTION_FAILED"
    assert "actions[0]" in str(exc.value)


def test_profile_validation_and_path(monkeypatch, tmp_path):
    assert interact.validate_profile("") == ""
    assert interact.validate_profile("my-profile_1") == "my-profile_1"
    with pytest.raises(interact.InteractionError):
        interact.validate_profile("../etc/passwd")
    monkeypatch.setenv("SMART3W_PROFILE_DIR", str(tmp_path))
    path = interact.profile_path("alice")
    assert path == tmp_path / "alice.json"


def test_normalize_cookies():
    cookies = interact.normalize_cookies([
        {"name": "sid", "value": "abc", "url": "https://example.com/"},
        {"name": "theme", "value": "dark", "domain": "example.com", "path": "/"},
    ])
    assert cookies[0]["name"] == "sid"
    assert cookies[1]["domain"] == "example.com"
    with pytest.raises(interact.InteractionError):
        interact.normalize_cookies([{"name": "x", "value": "y"}])


def test_run_interaction_reports_missing_playwright(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    with pytest.raises(interact.InteractionError) as exc:
        interact.run_interaction("https://example.com/")
    assert exc.value.code == "BROWSER_UNAVAILABLE"


def test_mcp_interact_wraps_success(monkeypatch):
    captured = {}

    def fake_run(url, actions, **kwargs):
        captured.update({"url": url, "actions": actions, **kwargs})
        return {
            "url": url,
            "final_url": "https://example.com/done",
            "title": "Done",
            "content": "hello",
            "content_format": "markdown",
            "content_total_length": 5,
            "content_truncated": False,
            "actions": [item["type"] for item in actions],
            "action_count": len(actions),
            "profile": kwargs.get("profile", ""),
            "duration_ms": 1,
        }

    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    monkeypatch.setattr(mcp_server, "ROBOTS_CHECKER", type("R", (), {"allows": lambda self, url: True})())
    monkeypatch.setattr(mcp_server, "run_interaction", fake_run)
    result = asyncio.run(
        mcp_server.smart3w_interact(
            "https://example.com/",
            [{"type": "click", "selector": "#go"}],
            profile="demo",
        )
    )
    assert result["success"] is True
    assert result["action_count"] == 1
    assert captured["url"] == "https://example.com/"
    assert captured["profile"] == "demo"


def test_mcp_interact_validates_and_rejects(monkeypatch):
    monkeypatch.setattr(mcp_server, "_validate_http_url", lambda url: None)
    called = []
    monkeypatch.setattr(mcp_server, "run_interaction", lambda *a, **k: called.append(1) or {})
    result = asyncio.run(
        mcp_server.smart3w_interact("https://example.com/", [{"type": "evaluate", "script": "1"}])
    )
    assert result["success"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert not called

    monkeypatch.setattr(mcp_server, "ROBOTS_CHECKER", type("R", (), {"allows": lambda self, url: False})())
    result = asyncio.run(mcp_server.smart3w_interact("https://example.com/"))
    assert result["error_code"] == "ROBOTS_DENIED"
