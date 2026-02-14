from types import SimpleNamespace
from pathlib import Path
import sari.mcp.cli.daemon as d
import sari.mcp.cli.daemon_startup_ops as startup_ops
from sari.core.fallback_governance import reset_fallback_metrics_for_tests, snapshot_fallback_metrics


def test_start_auto_switches_to_free_port_when_target_busy(monkeypatch):
    reset_fallback_metrics_for_tests()
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
    rows = {row["fallback_id"]: row for row in snapshot_fallback_metrics()["rows"]}
    assert rows["port_auto_fallback"]["enter_count"] >= 1


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


def test_start_string_false_explicit_port_uses_auto_strategy(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "explicit_port": "false",
        "registry": SimpleNamespace(find_free_port=lambda start_port: 47790),
    }

    monkeypatch.setattr("sari.mcp.cli.utils.is_port_in_use", lambda h, p: p == 47779)
    monkeypatch.setattr("sari.mcp.cli.daemon.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sari.mcp.cli.daemon.smart_kill_port_owner", lambda h, p: False)
    monkeypatch.delenv("SARI_DAEMON_PORT_STRATEGY", raising=False)

    rc = d.check_port_availability(params)
    assert rc is None
    assert params["port"] == 47790


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


def test_prepare_daemon_environment_uses_resolved_port_not_stale_arg_port():
    args = SimpleNamespace(
        daemon_host="127.0.0.1",
        daemon_port=47779,
        http_host="",
        http_port=None,
    )
    params = {
        "args": args,
        "host": "127.0.0.1",
        "port": 47780,  # resolved fallback port
        "workspace_root": "/tmp/ws",
    }

    env = startup_ops.prepare_daemon_environment(
        params,
        get_arg=lambda obj, key: getattr(obj, key, None),
        runtime_host_key="SARI_DAEMON_HOST",
        runtime_port_key="SARI_DAEMON_PORT",
        environ={},
    )

    assert env["SARI_DAEMON_HOST"] == "127.0.0.1"
    assert env["SARI_DAEMON_PORT"] == "47780"


def test_start_daemon_in_foreground_uses_resolved_port_not_stale_arg_port():
    args = SimpleNamespace(
        daemon_host="127.0.0.1",
        daemon_port=47779,
        http_host="",
        http_port=None,
    )
    params = {
        "args": args,
        "host": "127.0.0.1",
        "port": 47780,  # resolved fallback port
        "workspace_root": "/tmp/ws",
        "repo_root": Path("/tmp/repo"),
    }
    env = {"PYTHONPATH": ""}

    async def _fake_daemon_main():
        return None

    rc = startup_ops.start_daemon_in_foreground(
        params,
        get_arg=lambda obj, key: getattr(obj, key, None),
        runtime_host_key="SARI_DAEMON_HOST",
        runtime_port_key="SARI_DAEMON_PORT",
        daemon_main_provider=lambda: _fake_daemon_main,
        environ=env,
    )

    assert rc == 0
    assert env["SARI_DAEMON_HOST"] == "127.0.0.1"
    assert env["SARI_DAEMON_PORT"] == "47780"
