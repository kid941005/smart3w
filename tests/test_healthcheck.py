"""容器健康检查端口归一化测试。"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("healthcheck", ROOT / "scripts" / "healthcheck.py")
assert _spec and _spec.loader
healthcheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(healthcheck)


def test_normalize_port_falls_back_and_clamps():
    assert healthcheck._normalize_port("abc") == healthcheck.DEFAULT_PORT
    assert healthcheck._normalize_port("70000") == 65535
    assert healthcheck._normalize_port("-3") == 1
    assert healthcheck._normalize_port("50999") == 50999


def test_main_probes_loopback_for_wildcard_host(monkeypatch):
    captured = {}

    class FakeSocket:
        def close(self):
            captured["closed"] = True

    def fake_create_connection(address, timeout):
        captured["address"] = address
        captured["timeout"] = timeout
        return FakeSocket()

    monkeypatch.setenv("SMART3W_HOST", "0.0.0.0")
    monkeypatch.setenv("SMART3W_PORT", "abc")
    monkeypatch.setattr(healthcheck.socket, "create_connection", fake_create_connection)

    assert healthcheck.main() == 0
    assert captured["address"] == ("127.0.0.1", healthcheck.DEFAULT_PORT)
    assert captured["closed"] is True


def test_main_returns_one_when_connection_fails(monkeypatch):
    monkeypatch.setattr(
        healthcheck.socket,
        "create_connection",
        lambda address, timeout: (_ for _ in ()).throw(OSError("refused")),
    )
    assert healthcheck.main() == 1
