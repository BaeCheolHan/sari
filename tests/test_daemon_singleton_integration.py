from unittest.mock import MagicMock
import sari.mcp.cli.smart_daemon as sd


def test_two_client_ensure_paths_reuse_single_daemon(monkeypatch):
    monkeypatch.setattr(sd, "identify_sari_daemon", lambda h, p: {"name": "sari", "version": "0.6.10", "draining": False})
    monkeypatch.setattr(sd, "get_local_version", lambda: "0.6.10")
    ensure_calls = []
    monkeypatch.setattr(sd, "ensure_workspace_http", lambda h, p, w: ensure_calls.append((h, p, w)) or True)

    popen_mock = MagicMock()
    monkeypatch.setattr(sd.subprocess, "Popen", popen_mock)

    h1, p1 = sd.ensure_smart_daemon("127.0.0.1", 47779, "/tmp/codex")
    h2, p2 = sd.ensure_smart_daemon("127.0.0.1", 47779, "/tmp/gemini")

    assert (h1, p1) == ("127.0.0.1", 47779)
    assert (h2, p2) == ("127.0.0.1", 47779)
    assert len(ensure_calls) == 2
    popen_mock.assert_not_called()


def test_upgrade_path_replaces_daemon_in_place(monkeypatch):
    calls = {"n": 0}

    def _identify(_h, _p):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"name": "sari", "version": "0.6.9", "draining": False}
        return None

    monkeypatch.setattr(sd, "identify_sari_daemon", _identify)
    monkeypatch.setattr(sd, "get_local_version", lambda: "0.6.10")

    stop_calls = []
    monkeypatch.setattr("sari.mcp.cli.cmd_daemon_stop", lambda a: stop_calls.append((a.daemon_host, a.daemon_port)) or 0)
    monkeypatch.setattr(sd, "is_port_in_use", lambda h, p: False)
    monkeypatch.setattr(sd, "probe_sari_daemon", lambda h, p, timeout=1.0: True)

    popen_mock = MagicMock()
    monkeypatch.setattr(sd.subprocess, "Popen", popen_mock)
    ensure_calls = []
    monkeypatch.setattr(sd, "ensure_workspace_http", lambda h, p, w: ensure_calls.append((h, p, w)) or True)

    h, p = sd.ensure_smart_daemon("127.0.0.1", 47779, "/tmp/ws")

    assert (h, p) == ("127.0.0.1", 47779)
    assert stop_calls == [("127.0.0.1", 47779)]
    popen_mock.assert_called_once()
    assert ensure_calls == [("127.0.0.1", 47779, "/tmp/ws")]
