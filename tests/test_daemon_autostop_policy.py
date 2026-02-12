from types import SimpleNamespace
from unittest.mock import patch
import threading
import time
import sys

from sari.mcp.daemon import DaemonEvent, SariDaemon, RuntimeStateProvider
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


def test_runtime_state_provider_collects_controller_signals(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49999)
    ws_reg = SimpleNamespace(
        active_count=lambda: 3,
        has_indexing_activity=lambda: True,
        get_last_activity_ts=lambda: 123.0,
    )
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": True})
    monkeypatch.setattr(daemon, "_get_active_connections", lambda: 2)
    monkeypatch.setattr(daemon, "active_lease_count", lambda: 1)
    monkeypatch.setattr(daemon, "_workers_inflight", lambda: 4)

    snap = RuntimeStateProvider(daemon, ws_reg).collect()
    assert snap.draining is True
    assert snap.active_count == 3
    assert snap.socket_active == 2
    assert snap.lease_active == 1
    assert snap.workers_inflight == 4
    assert snap.indexing_active is True
    assert snap.last_activity == 123.0


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
    daemon._controller_loop()
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
    daemon._controller_loop()
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

    t = threading.Thread(target=daemon._controller_loop, daemon=True)
    t.start()
    time.sleep(0.02)
    daemon._stop_event.set()
    t.join(timeout=1.0)
    assert called["shutdown"] == 0


def test_heartbeat_autostops_even_when_indexing_active_if_no_clients(monkeypatch):
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
    def _shutdown():
        called["shutdown"] += 1
        daemon._stop_event.set()
    monkeypatch.setattr(daemon, "shutdown", _shutdown)

    daemon._controller_loop()
    assert called["shutdown"] == 1


def test_heartbeat_reaps_stale_refs_when_no_socket_clients(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49993)
    daemon._autostop_no_client_since = time.time() - 60
    daemon._active_connections = 0

    state = {"active": 1, "reaped": 0}

    def _reap(_max_idle_sec):
        state["reaped"] += 1
        state["active"] = 0
        return 1

    fake_ws_reg = SimpleNamespace(
        active_count=lambda: state["active"],
        has_persistent=lambda: False,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: 0.0,
        reap_stale_refs=_reap,
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
    daemon._controller_loop()
    assert state["reaped"] >= 1
    assert called["shutdown"] == 1


def test_heartbeat_uses_socket_clients_as_active_signal(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49992)
    daemon._active_connections = 1
    fake_ws_reg = SimpleNamespace(
        active_count=lambda: 0,
        has_persistent=lambda: False,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: time.time(),
        reap_stale_refs=lambda _max_idle_sec: 0,
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

    t = threading.Thread(target=daemon._controller_loop, daemon=True)
    t.start()
    time.sleep(0.02)
    daemon._stop_event.set()
    t.join(timeout=1.0)
    assert called["shutdown"] == 0


def test_heartbeat_moves_suicide_state_grace_to_stopping(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49991)
    daemon._autostop_no_client_since = time.time() - 60

    fake_ws_reg = SimpleNamespace(
        active_count=lambda: 0,
        has_persistent=lambda: False,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: time.time(),
        reap_stale_refs=lambda _max_idle_sec: 0,
    )
    monkeypatch.setattr("sari.mcp.workspace_registry.Registry.get_instance", lambda: fake_ws_reg)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_HEARTBEAT_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_IDLE_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP_GRACE_SEC", 1)
    monkeypatch.setattr(daemon._registry, "touch_daemon", lambda _boot_id: None)
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": False})
    monkeypatch.setattr(daemon, "_workers_inflight", lambda: 0)

    called = {"shutdown": 0}

    def _shutdown():
        called["shutdown"] += 1
        daemon._stop_event.set()

    monkeypatch.setattr(daemon, "shutdown", _shutdown)
    daemon._controller_loop()
    assert called["shutdown"] == 1
    assert daemon._suicide_state == "stopping"


def test_grace_reconnect_returns_to_idle(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49990)
    state = {"active": 0}
    fake_ws_reg = SimpleNamespace(
        active_count=lambda: state["active"],
        has_persistent=lambda: False,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: time.time(),
        reap_stale_refs=lambda _max_idle_sec: 0,
    )
    monkeypatch.setattr("sari.mcp.workspace_registry.Registry.get_instance", lambda: fake_ws_reg)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_HEARTBEAT_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_IDLE_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP_GRACE_SEC", 5)
    monkeypatch.setattr(daemon._registry, "touch_daemon", lambda _boot_id: None)
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": False})

    calls = {"shutdown": 0}
    monkeypatch.setattr(daemon, "shutdown", lambda *_a, **_k: calls.__setitem__("shutdown", calls["shutdown"] + 1))

    t = threading.Thread(target=daemon._controller_loop, daemon=True)
    t.start()
    daemon._enqueue_lease_event("LEASE_ISSUE", lease_id="l1", client_hint="cli")
    time.sleep(0.02)
    daemon._enqueue_lease_event("LEASE_REVOKE", lease_id="l1", reason="conn_close")
    time.sleep(0.02)
    daemon._enqueue_lease_event("LEASE_ISSUE", lease_id="l2", client_hint="reconnect")
    time.sleep(0.02)
    daemon._stop_event.set()
    t.join(timeout=1.0)
    assert daemon._suicide_state == "idle"
    assert calls["shutdown"] == 0


def test_shutdown_request_runs_once(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49989)
    monkeypatch.setattr(daemon, "_unregister_daemon", lambda: None)
    monkeypatch.setattr(daemon, "_cleanup_legacy_pid_file", lambda: None)
    monkeypatch.setitem(sys.modules, "multiprocessing", SimpleNamespace(active_children=lambda: []))

    daemon._enqueue_shutdown_request("first")
    daemon._enqueue_shutdown_request("second")
    daemon._apply_lease_events()
    daemon._apply_lease_events()

    assert daemon._shutdown_once.is_set()
    assert daemon.last_shutdown_reason == "first"


def test_shutdown_request_runs_once_even_with_ten_events(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49979)
    monkeypatch.setattr(daemon, "_unregister_daemon", lambda: None)
    monkeypatch.setattr(daemon, "_cleanup_legacy_pid_file", lambda: None)
    monkeypatch.setitem(sys.modules, "multiprocessing", SimpleNamespace(active_children=lambda: []))

    for i in range(10):
        daemon._enqueue_shutdown_request(f"reason-{i}")

    for _ in range(10):
        daemon._apply_lease_events()

    assert daemon._shutdown_once.is_set()
    assert daemon.last_shutdown_reason == "reason-0"


def test_conn_close_event_revokes_immediately(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49988)
    monkeypatch.setattr(daemon, "_unregister_daemon", lambda: None)
    monkeypatch.setattr(daemon, "_cleanup_legacy_pid_file", lambda: None)
    daemon._enqueue_lease_event("LEASE_ISSUE", lease_id="l1", client_hint="cli")
    daemon._apply_lease_events()
    assert daemon.active_lease_count() == 1
    daemon._enqueue_lease_event("LEASE_REVOKE", lease_id="l1", reason="conn_close")
    daemon._apply_lease_events()
    assert daemon.active_lease_count() == 0


def test_event_burst_soak_drains_queue_without_state_corruption():
    daemon = SariDaemon(host="127.0.0.1", port=49987)
    total = 5000
    for i in range(total):
        lease_id = f"lease-{i % 200}"
        daemon._enqueue_lease_event("LEASE_ISSUE", lease_id=lease_id, client_hint="burst")
        daemon._enqueue_lease_event("LEASE_RENEW", lease_id=lease_id)
        daemon._enqueue_lease_event("LEASE_REVOKE", lease_id=lease_id, reason="burst_revoke")

    guard = 0
    while daemon._event_queue_depth > 0 and guard < 2000:
        daemon._apply_lease_events(max_events=128)
        guard += 1

    assert daemon._event_queue_depth == 0
    assert daemon.active_lease_count() == 0
    assert daemon._suicide_state in {"idle", "grace", "stopping"}


def test_lease_renew_events_are_coalesced_before_drain():
    daemon = SariDaemon(host="127.0.0.1", port=49985)

    daemon._enqueue_lease_event("LEASE_ISSUE", lease_id="lease-1", client_hint="cli")
    for _ in range(100):
        daemon._enqueue_lease_event("LEASE_RENEW", lease_id="lease-1")

    # ISSUE(1) + coalesced RENEW(1)
    assert daemon._event_queue_depth == 2
    daemon._apply_lease_events(max_events=64)
    assert daemon._event_queue_depth == 0


def test_heartbeat_events_are_coalesced_before_drain():
    daemon = SariDaemon(host="127.0.0.1", port=49984)

    for _ in range(100):
        daemon._enqueue_event(DaemonEvent(event_type="HEARTBEAT_TICK"))

    assert daemon._event_queue_depth == 1
    daemon._apply_lease_events(max_events=64)
    assert daemon._event_queue_depth == 0


def test_controller_wakeup_drains_events_before_heartbeat_interval(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49983)
    fake_ws_reg = SimpleNamespace(
        active_count=lambda: 0,
        has_persistent=lambda: False,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: time.time(),
        reap_stale_refs=lambda _max_idle_sec: 0,
    )
    monkeypatch.setattr("sari.mcp.workspace_registry.Registry.get_instance", lambda: fake_ws_reg)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_HEARTBEAT_SEC", 5.0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP", False)
    monkeypatch.setattr(daemon._registry, "touch_daemon", lambda _boot_id: None)
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": False})

    t = threading.Thread(target=daemon._controller_loop, daemon=True)
    t.start()
    daemon._enqueue_lease_event("LEASE_ISSUE", lease_id="lease-early-drain", client_hint="cli")
    daemon._enqueue_lease_event("LEASE_REVOKE", lease_id="lease-early-drain", reason="close")

    deadline = time.time() + 0.5
    while time.time() < deadline and daemon._event_queue_depth > 0:
        time.sleep(0.01)

    daemon._stop_event.set()
    daemon._controller_wakeup.set()
    t.join(timeout=1.0)
    assert daemon._event_queue_depth == 0


def test_issue_then_revoke_before_drain_is_coalesced():
    daemon = SariDaemon(host="127.0.0.1", port=49982)

    daemon._enqueue_lease_event("LEASE_ISSUE", lease_id="lease-coalesce", client_hint="cli")
    daemon._enqueue_lease_event("LEASE_REVOKE", lease_id="lease-coalesce", reason="close")

    # ISSUE event remains queued but paired REVOKE is absorbed before enqueue.
    assert daemon._event_queue_depth == 1
    daemon._apply_lease_events(max_events=64)
    assert daemon._event_queue_depth == 0
    assert daemon.active_lease_count() == 0


def test_duplicate_revoke_events_are_coalesced():
    daemon = SariDaemon(host="127.0.0.1", port=49981)

    daemon._enqueue_lease_event("LEASE_REVOKE", lease_id="lease-r", reason="close1")
    daemon._enqueue_lease_event("LEASE_REVOKE", lease_id="lease-r", reason="close2")

    assert daemon._event_queue_depth == 1
    daemon._apply_lease_events(max_events=64)
    assert daemon._event_queue_depth == 0


def test_event_queue_depth_is_capped_under_issue_burst(monkeypatch):
    monkeypatch.setenv("SARI_DAEMON_EVENT_QUEUE_SIZE", "8")
    daemon = SariDaemon(host="127.0.0.1", port=49980)

    for i in range(200):
        daemon._enqueue_lease_event("LEASE_ISSUE", lease_id=f"burst-{i}", client_hint="burst")

    assert daemon._event_queue_depth <= 8


def test_worker_hang_with_reconnect_still_shuts_down_once(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49986)
    daemon._autostop_no_client_since = time.time() - 60

    fake_ws_reg = SimpleNamespace(
        active_count=lambda: 0,
        has_persistent=lambda: False,
        has_indexing_activity=lambda: False,
        get_last_activity_ts=lambda: time.time(),
        reap_stale_refs=lambda _max_idle_sec: 0,
    )
    monkeypatch.setattr("sari.mcp.workspace_registry.Registry.get_instance", lambda: fake_ws_reg)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_HEARTBEAT_SEC", 0.01)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_IDLE_SEC", 0)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTOP_GRACE_SEC", 1)
    monkeypatch.setenv("SARI_DAEMON_SHUTDOWN_INHIBIT_MAX_SEC", "2")
    monkeypatch.setattr(daemon._registry, "touch_daemon", lambda _boot_id: None)
    monkeypatch.setattr(daemon._registry, "get_daemon", lambda _boot_id: {"draining": False})

    state = {"workers": 1}
    monkeypatch.setattr(daemon, "_workers_inflight", lambda: state["workers"])

    calls = {"shutdown": 0}

    def _shutdown(*_args, **_kwargs):
        calls["shutdown"] += 1
        daemon._shutdown_once.set()
        daemon._stop_event.set()

    monkeypatch.setattr(daemon, "shutdown", _shutdown)

    t = threading.Thread(target=daemon._controller_loop, daemon=True)
    t.start()

    for i in range(100):
        lease_id = f"r-{i}"
        daemon._enqueue_lease_event("LEASE_ISSUE", lease_id=lease_id, client_hint="reconnect")
        daemon._enqueue_lease_event("LEASE_REVOKE", lease_id=lease_id, reason="drop")
    time.sleep(0.5)
    assert calls["shutdown"] == 0

    state["workers"] = 0
    for _ in range(150):
        if calls["shutdown"] >= 1:
            break
        time.sleep(0.02)

    daemon._stop_event.set()
    t.join(timeout=2.0)
    assert calls["shutdown"] == 1
