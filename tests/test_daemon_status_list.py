import argparse
import sari.mcp.cli.legacy_cli as cli
import sari.mcp.cli.daemon as daemon_mod


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


def test_list_registry_daemons_skips_malformed_entries(monkeypatch):
    class _Reg:
        def _load(self):
            return {
                "daemons": {
                    "bad": 123,
                    "good": {"host": "127.0.0.1", "port": 47779, "pid": 9999, "last_seen_ts": 1.0},
                }
            }

    monkeypatch.setattr(daemon_mod, "ServerRegistry", lambda: _Reg())
    monkeypatch.setattr(daemon_mod.os, "kill", lambda _pid, _sig: None)

    rows = daemon_mod.list_registry_daemons()
    assert len(rows) == 1
    assert rows[0]["boot_id"] == "good"
