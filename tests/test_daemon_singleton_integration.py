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


def test_bg_upgrade_switches_to_candidate_without_stopping_old(monkeypatch):
    monkeypatch.setenv("SARI_BG_DEPLOY", "1")
    monkeypatch.setattr(sd, "get_local_version", lambda: "0.6.10")

    id_calls = {"n": 0}

    def _identify(_h, p):
        id_calls["n"] += 1
        if p == 47779:
            return {"name": "sari", "version": "0.6.9", "draining": False, "bootId": "boot-old"}
        if p == 47811:
            return {"name": "sari", "version": "0.6.10", "draining": False, "bootId": "boot-new"}
        return None

    monkeypatch.setattr(sd, "identify_sari_daemon", _identify)
    monkeypatch.setattr(sd, "probe_sari_daemon", lambda _h, _p, timeout=1.0: True)

    ensure_calls = []
    monkeypatch.setattr(sd, "ensure_workspace_http", lambda h, p, w: ensure_calls.append((h, p, w)) or True)

    class _Reg:
        def __init__(self):
            self.called = []

        def resolve_daemon_by_endpoint(self, _h, _p):
            return {"boot_id": "boot-old"}

        def find_free_port(self, host="127.0.0.1", start_port=0):
            return 47811

        def begin_deploy(self, candidate_boot_id, expected_active_boot_id=None):
            self.called.append(("begin", candidate_boot_id, expected_active_boot_id))
            return {"generation": 1}

        def mark_candidate_healthy(self, generation, candidate_boot_id):
            self.called.append(("healthy", generation, candidate_boot_id))
            return {"generation": generation}

        def switch_active(self, generation, candidate_boot_id):
            self.called.append(("switch", generation, candidate_boot_id))
            return {"active_boot_id": candidate_boot_id}

        def set_daemon_draining(self, boot_id, draining=True):
            self.called.append(("drain", boot_id, draining))

        def record_health_failure(self, generation, candidate_boot_id, reason=""):
            self.called.append(("fail", generation, candidate_boot_id, reason))
            return {"generation": generation}

        def rollback_active(self, generation, restore_boot_id, reason=""):
            self.called.append(("rollback", generation, restore_boot_id, reason))
            return {"active_boot_id": restore_boot_id}

    reg = _Reg()
    monkeypatch.setattr(sd, "ServerRegistry", lambda: reg)

    popen_mock = MagicMock()
    monkeypatch.setattr(sd.subprocess, "Popen", popen_mock)
    stop_calls = []
    monkeypatch.setattr("sari.mcp.cli.cmd_daemon_stop", lambda a: stop_calls.append((a.daemon_host, a.daemon_port)) or 0)

    h, p = sd.ensure_smart_daemon("127.0.0.1", 47779, "/tmp/ws")

    assert (h, p) == ("127.0.0.1", 47811)
    assert stop_calls == []
    assert popen_mock.called
    assert ("switch", 1, "boot-new") in reg.called
    assert ("drain", "boot-old", True) in reg.called
    assert ensure_calls[-1] == ("127.0.0.1", 47811, "/tmp/ws")


def test_bg_upgrade_rolls_back_after_three_probe_failures(monkeypatch):
    monkeypatch.setenv("SARI_BG_DEPLOY", "1")
    monkeypatch.setattr(sd, "get_local_version", lambda: "0.6.10")

    def _identify(_h, p):
        if p == 47779:
            return {"name": "sari", "version": "0.6.9", "draining": False, "bootId": "boot-old"}
        if p == 47811:
            return {"name": "sari", "version": "0.6.10", "draining": False, "bootId": "boot-new"}
        return None

    monkeypatch.setattr(sd, "identify_sari_daemon", _identify)
    monkeypatch.setattr(sd, "_launch_daemon", lambda _h, _p, _w: True)
    monkeypatch.setattr(sd, "probe_sari_daemon", lambda _h, p, timeout=1.0: p == 47779)
    monkeypatch.setattr(sd, "ensure_workspace_http", lambda *_a, **_k: True)

    class _Reg:
        def __init__(self):
            self.called = []

        def resolve_daemon_by_endpoint(self, _h, _p):
            return {"boot_id": "boot-old"}

        def find_free_port(self, host="127.0.0.1", start_port=0):
            return 47811

        def begin_deploy(self, candidate_boot_id, expected_active_boot_id=None):
            return {"generation": 1}

        def mark_candidate_healthy(self, generation, candidate_boot_id):
            return {"generation": generation}

        def switch_active(self, generation, candidate_boot_id):
            return {"active_boot_id": candidate_boot_id}

        def set_daemon_draining(self, boot_id, draining=True):
            self.called.append(("drain", boot_id, draining))

        def record_health_failure(self, generation, candidate_boot_id, reason=""):
            self.called.append(("fail", generation, candidate_boot_id, reason))
            return {"generation": generation}

        def rollback_active(self, generation, restore_boot_id, reason=""):
            self.called.append(("rollback", generation, restore_boot_id, reason))
            return {"active_boot_id": restore_boot_id}

    reg = _Reg()
    monkeypatch.setattr(sd, "ServerRegistry", lambda: reg)
    monkeypatch.setattr(sd.subprocess, "Popen", MagicMock())
    stop_calls = []
    monkeypatch.setattr("sari.mcp.cli.cmd_daemon_stop", lambda a: stop_calls.append((a.daemon_host, a.daemon_port)) or 0)

    h, p = sd.ensure_smart_daemon("127.0.0.1", 47779, "/tmp/ws")

    assert (h, p) == ("127.0.0.1", 47779)
    assert ("rollback", 1, "boot-old", "post_switch_probe_failed_x3") in reg.called
    assert ("drain", "boot-old", False) in reg.called
    assert ("127.0.0.1", 47811) in stop_calls
