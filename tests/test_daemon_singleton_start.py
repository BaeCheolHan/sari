from types import SimpleNamespace
import sari.mcp.cli.daemon as d


def test_start_auto_switches_to_free_port_when_target_busy(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "explicit_port": False,
        "registry": SimpleNamespace(find_free_port=lambda start_port: 47790),
    }

    monkeypatch.setattr("sari.mcp.cli.utils.is_port_in_use", lambda h, p: p == 47779)
    monkeypatch.setattr("sari.mcp.cli.daemon.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sari.mcp.cli.daemon.smart_kill_port_owner", lambda h, p: False)
    monkeypatch.delenv("SARI_DAEMON_PORT_STRATEGY", raising=False)

    rc = d.check_port_availability(params)

    assert rc is None
    assert params["port"] == 47790


def test_start_strict_keeps_requested_port_when_target_busy(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "explicit_port": True,
        "registry": SimpleNamespace(find_free_port=lambda start_port: 47790),
    }

    monkeypatch.setattr("sari.mcp.cli.utils.is_port_in_use", lambda h, p: p == 47779)
    monkeypatch.setattr("sari.mcp.cli.daemon.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sari.mcp.cli.daemon.smart_kill_port_owner", lambda h, p: False)
    monkeypatch.delenv("SARI_DAEMON_PORT_STRATEGY", raising=False)

    rc = d.check_port_availability(params)

    assert rc == 1
    assert params["port"] == 47779


def test_check_port_availability_tolerates_transient_busy(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "explicit_port": False,
        "registry": SimpleNamespace(find_free_port=lambda start_port: 47790),
    }

    states = iter([True, True, False])
    monkeypatch.setattr(
        "sari.mcp.cli.utils.is_port_in_use",
        lambda h, p: next(states),
    )
    monkeypatch.setattr("sari.mcp.cli.daemon.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sari.mcp.cli.daemon.smart_kill_port_owner", lambda h, p: False)

    rc = d.check_port_availability(params)
    assert rc is None


def test_check_port_availability_auto_kills_stale_owner_before_fallback(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "explicit_port": False,
        "registry": SimpleNamespace(find_free_port=lambda start_port: 47790),
    }

    calls = {"n": 0, "kill": 0}

    def _port_in_use(_h, _p):
        calls["n"] += 1
        return calls["n"] <= 8

    def _smart_kill(_h, _p):
        calls["kill"] += 1
        return True

    monkeypatch.setattr("sari.mcp.cli.utils.is_port_in_use", _port_in_use)
    monkeypatch.setattr("sari.mcp.cli.daemon.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sari.mcp.cli.daemon.smart_kill_port_owner", _smart_kill)
    monkeypatch.delenv("SARI_DAEMON_PORT_STRATEGY", raising=False)

    rc = d.check_port_availability(params)

    assert rc is None
    assert calls["kill"] == 1
    assert params["port"] == 47779
