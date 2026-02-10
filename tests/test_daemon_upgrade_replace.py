import argparse
import sari.mcp.cli.daemon as d


def test_version_mismatch_triggers_stop_then_restart(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "workspace_root": "/tmp/ws",
        "registry": type("R", (), {"resolve_workspace_daemon": lambda self, ws: None, "find_free_port": lambda self, start_port: 47790})(),
        "explicit_port": False,
        "force_start": False,
        "args": argparse.Namespace(),
    }
    calls = []

    ident_calls = {"n": 0}
    def _identify(_h, _p):
        ident_calls["n"] += 1
        if ident_calls["n"] == 1:
            return {"version": "0.6.9", "draining": False}
        return None

    monkeypatch.setattr(d, "identify_sari_daemon", _identify)
    monkeypatch.setattr("sari.mcp.cli.utils.get_local_version", lambda: "0.6.10")
    monkeypatch.setattr("sari.mcp.cli.cmd_daemon_stop", lambda a: calls.append((a.daemon_host, a.daemon_port)) or 0)

    rc = d.handle_existing_daemon(params)

    assert rc is None
    assert calls == [("127.0.0.1", 47779)]
    assert params["port"] == 47779
