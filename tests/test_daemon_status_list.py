import argparse
import sari.mcp.cli.legacy_cli as cli


def test_daemon_status_lists_all_daemons(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 47779))
    monkeypatch.setattr("sari.mcp.cli.daemon.list_registry_daemons", lambda: [
        {"host": "127.0.0.1", "port": 47779, "pid": 1001, "version": "0.6.10"},
        {"host": "127.0.0.1", "port": 47790, "pid": 1002, "version": "0.6.9"},
    ])

    rc = cli.cmd_daemon_status(argparse.Namespace())
    out = capsys.readouterr().out

    assert rc == 0
    assert "47779" in out and "47790" in out
