from types import SimpleNamespace
from unittest.mock import patch
import threading
import time

from sari.mcp.daemon import SariDaemon
from sari.mcp.workspace_registry import Registry


def test_registry_get_or_create_track_ref_false_does_not_increment_ref():
    reg = Registry()
    state = SimpleNamespace(ref_count=0, persistent=False, touch=lambda: None)
    reg._sessions["/tmp/ws"] = state

    out = reg.get_or_create("/tmp/ws", track_ref=False)

    assert out is state
    assert state.ref_count == 0


def test_daemon_autostart_workspace_is_not_persistent_and_not_tracked(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49998)

    mock_registry = SimpleNamespace(
        get_or_create=lambda *args, **kwargs: None,
    )

    calls = {}

    def _fake_get_or_create(workspace_root, persistent=False, track_ref=True):
        calls["workspace_root"] = workspace_root
        calls["persistent"] = persistent
        calls["track_ref"] = track_ref
        return SimpleNamespace(workspace_root=workspace_root)

    mock_registry.get_or_create = _fake_get_or_create

    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTART", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.WORKSPACE_ROOT", "/tmp/ws")

    with patch("sari.mcp.workspace_registry.Registry.get_instance", return_value=mock_registry):
        with patch.object(daemon, "_start_http_gateway", return_value=None):
            with patch.object(daemon._registry, "set_workspace", return_value=None):
                daemon._autostart_workspace()

    assert calls["workspace_root"] == "/tmp/ws"
    assert calls["persistent"] is False
    assert calls["track_ref"] is False


def test_heartbeat_autostops_when_no_active_clients(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49997)
    daemon._autostop_no_client_since = time.time() - 60

    fake_ws_reg = SimpleNamespace(
        active_count=lambda: 0,
        has_persistent=lambda: False,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: 0.0,
    )
    monkeypatch.setattr("sari.mcp.workspace_registry.Registry.get_instance", lambda: fake_ws_reg)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_HEARTBEAT_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_IDLE_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP_GRACE_SEC", 1)
    monkeypatch.setattr(daemon._registry, "touch_daemon", lambda _boot_id: None)
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": False})

    called = {"shutdown": 0}

    def _shutdown():
        called["shutdown"] += 1
        daemon._stop_event.set()

    monkeypatch.setattr(daemon, "shutdown", _shutdown)
    daemon._heartbeat_loop()
    assert called["shutdown"] == 1


def test_heartbeat_autostops_even_with_persistent_workspace_when_no_clients(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49995)
    daemon._autostop_no_client_since = time.time() - 60

    fake_ws_reg = SimpleNamespace(
        active_count=lambda: 0,
        has_persistent=lambda: True,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: 0.0,
    )
    monkeypatch.setattr("sari.mcp.workspace_registry.Registry.get_instance", lambda: fake_ws_reg)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_HEARTBEAT_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_IDLE_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP_GRACE_SEC", 1)
    monkeypatch.setattr(daemon._registry, "touch_daemon", lambda _boot_id: None)
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": False})

    called = {"shutdown": 0}

    def _shutdown():
        called["shutdown"] += 1
        daemon._stop_event.set()

    monkeypatch.setattr(daemon, "shutdown", _shutdown)
    daemon._heartbeat_loop()
    assert called["shutdown"] == 1


def test_heartbeat_does_not_autostop_with_active_clients(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49996)
    fake_ws_reg = SimpleNamespace(
        active_count=lambda: 1,
        has_persistent=lambda: False,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: time.time(),
    )
    monkeypatch.setattr("sari.mcp.workspace_registry.Registry.get_instance", lambda: fake_ws_reg)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_HEARTBEAT_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_IDLE_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP_GRACE_SEC", 1)
    monkeypatch.setattr(daemon._registry, "touch_daemon", lambda _boot_id: None)
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": False})

    called = {"shutdown": 0}
    monkeypatch.setattr(daemon, "shutdown", lambda: called.__setitem__("shutdown", called["shutdown"] + 1))

    t = threading.Thread(target=daemon._heartbeat_loop, daemon=True)
    t.start()
    time.sleep(0.02)
    daemon._stop_event.set()
    t.join(timeout=1.0)
    assert called["shutdown"] == 0


def test_heartbeat_does_not_autostop_while_indexing_active(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49994)
    daemon._autostop_no_client_since = time.time() - 60

    fake_ws_reg = SimpleNamespace(
        active_count=lambda: 0,
        has_persistent=lambda: False,
        has_indexing_activity=lambda: True,
        get_last_activity_ts=lambda: time.time(),
    )
    monkeypatch.setattr("sari.mcp.workspace_registry.Registry.get_instance", lambda: fake_ws_reg)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_HEARTBEAT_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_IDLE_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP_GRACE_SEC", 1)
    monkeypatch.setattr(daemon._registry, "touch_daemon", lambda _boot_id: None)
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": False})

    called = {"shutdown": 0}
    monkeypatch.setattr(daemon, "shutdown", lambda: called.__setitem__("shutdown", called["shutdown"] + 1))

    t = threading.Thread(target=daemon._heartbeat_loop, daemon=True)
    t.start()
    time.sleep(0.02)
    daemon._stop_event.set()
    t.join(timeout=1.0)
    assert called["shutdown"] == 0
