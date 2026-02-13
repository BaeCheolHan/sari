import argparse

import sari.mcp.cli as cli_pkg
import sari.mcp.cli.daemon as daemon_mod


def test_handle_existing_daemon_delegates_to_orchestration_impl(monkeypatch):
    monkeypatch.setattr(cli_pkg, "cmd_daemon_stop", lambda _args: 0, raising=False)
    monkeypatch.setattr(daemon_mod, "_handle_existing_daemon_impl", lambda *_a, **_k: 7)
    rc = daemon_mod.handle_existing_daemon(
        {
            "host": "127.0.0.1",
            "port": 47779,
            "workspace_root": "/tmp/ws",
            "registry": object(),
            "explicit_port": False,
            "force_start": False,
            "args": argparse.Namespace(),
        }
    )
    assert rc == 7


def test_start_daemon_in_background_delegates_to_startup_impl(monkeypatch):
    monkeypatch.setattr(daemon_mod, "_start_daemon_in_background_impl", lambda *_a, **_k: 3)
    rc = daemon_mod.start_daemon_in_background({"host": "127.0.0.1", "port": 47779, "env": {}, "repo_root": None})
    assert rc == 3


def test_stop_daemon_process_delegates_to_process_ops_impl(monkeypatch):
    monkeypatch.setattr(daemon_mod, "_stop_daemon_process_impl", lambda *_a, **_k: 5)
    rc = daemon_mod.stop_daemon_process({"host": None, "port": None, "all": True})
    assert rc == 5
