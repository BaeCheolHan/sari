import argparse

import sari.mcp.cli.commands.daemon_commands as cmd


def test_daemon_refresh_pins_resolved_target_when_not_explicit(monkeypatch):
    captured = {}

    monkeypatch.setattr(cmd, "run_with_lifecycle_lock", lambda _name, fn: fn())
    monkeypatch.setattr(cmd, "get_daemon_address", lambda: ("127.0.0.1", 47779))

    def _stop(args):
        captured["stop_host"] = args.daemon_host
        captured["stop_port"] = args.daemon_port
        return 0

    def _start(args):
        captured["start_host"] = args.daemon_host
        captured["start_port"] = args.daemon_port
        captured["daemonize"] = args.daemonize
        return 0

    monkeypatch.setattr(cmd, "_cmd_daemon_stop_impl", _stop)
    monkeypatch.setattr(cmd, "_cmd_daemon_start_impl", _start)

    rc = cmd.cmd_daemon_refresh(argparse.Namespace(daemon_host=None, daemon_port=None))

    assert rc == 0
    assert captured["stop_host"] == "127.0.0.1"
    assert captured["stop_port"] == 47779
    assert captured["start_host"] == "127.0.0.1"
    assert captured["start_port"] == 47779
    assert captured["daemonize"] is True


def test_daemon_refresh_uses_explicit_target_when_provided(monkeypatch):
    captured = {}

    monkeypatch.setattr(cmd, "run_with_lifecycle_lock", lambda _name, fn: fn())
    monkeypatch.setattr(cmd, "get_daemon_address", lambda: ("127.0.0.1", 47779))

    def _stop(args):
        captured["stop_host"] = args.daemon_host
        captured["stop_port"] = args.daemon_port
        return 0

    def _start(args):
        captured["start_host"] = args.daemon_host
        captured["start_port"] = args.daemon_port
        return 0

    monkeypatch.setattr(cmd, "_cmd_daemon_stop_impl", _stop)
    monkeypatch.setattr(cmd, "_cmd_daemon_start_impl", _start)

    rc = cmd.cmd_daemon_refresh(
        argparse.Namespace(daemon_host="127.0.0.1", daemon_port=48888)
    )

    assert rc == 0
    assert captured["stop_host"] == "127.0.0.1"
    assert captured["stop_port"] == 48888
    assert captured["start_host"] == "127.0.0.1"
    assert captured["start_port"] == 48888
