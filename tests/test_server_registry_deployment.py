from sari.core.server_registry import ServerRegistry


def _setup_registry(tmp_path, monkeypatch):
    reg_file = tmp_path / "server.json"
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(reg_file))
    reg = ServerRegistry()
    monkeypatch.setattr(reg, "_is_process_alive", lambda _pid: True)
    reg.register_daemon("boot-a", "127.0.0.1", 47779, 111, version="0.6.32")
    reg.register_daemon("boot-b", "127.0.0.1", 47811, 222, version="0.6.33")
    reg.set_workspace("/tmp/ws-a", "boot-a")
    return reg


def test_deployment_begin_switch_and_health_failure(tmp_path, monkeypatch):
    reg = _setup_registry(tmp_path, monkeypatch)

    dep = reg.begin_deploy("boot-b", expected_active_boot_id="boot-a")
    assert dep["generation"] == 1
    assert dep["candidate_boot_id"] == "boot-b"
    assert dep["state"] == "starting"

    dep = reg.mark_candidate_healthy(1, "boot-b")
    assert dep["state"] == "ready"

    dep = reg.switch_active(1, "boot-b")
    assert dep["active_boot_id"] == "boot-b"
    assert dep["old_boot_id"] == "boot-a"
    assert dep["state"] == "switched"

    ws = reg.get_workspace("/tmp/ws-a") or {}
    assert ws.get("boot_id") == "boot-b"

    dep = reg.record_health_failure(1, "boot-b", reason="probe-fail")
    assert dep["health_fail_streak"] == 1
    assert dep["rollback_reason"] == "probe-fail"


def test_deployment_generation_mismatch_is_noop(tmp_path, monkeypatch):
    reg = _setup_registry(tmp_path, monkeypatch)
    reg.begin_deploy("boot-b", expected_active_boot_id="boot-a")

    dep = reg.switch_active(999, "boot-b")
    assert dep["active_boot_id"] == "boot-a"
    assert dep["state"] == "starting"

    dep = reg.mark_candidate_healthy(999, "boot-b")
    assert dep["state"] == "starting"


def test_deployment_rollback_restores_active(tmp_path, monkeypatch):
    reg = _setup_registry(tmp_path, monkeypatch)
    reg.begin_deploy("boot-b", expected_active_boot_id="boot-a")
    reg.mark_candidate_healthy(1, "boot-b")
    reg.switch_active(1, "boot-b")

    dep = reg.rollback_active(1, "boot-a", reason="rollback-by-test")
    assert dep["active_boot_id"] == "boot-a"
    assert dep["candidate_boot_id"] == ""
    assert dep["state"] == "idle"
    assert dep["rollback_reason"] == "rollback-by-test"

    ws = reg.get_workspace("/tmp/ws-a") or {}
    assert ws.get("boot_id") == "boot-a"


def test_get_active_daemons_excludes_draining(tmp_path, monkeypatch):
    reg = _setup_registry(tmp_path, monkeypatch)
    reg.set_daemon_draining("boot-a", True)
    rows = reg.get_active_daemons()
    boots = {str(r.get("boot_id") or "") for r in rows}
    assert "boot-a" not in boots
    assert "boot-b" in boots
