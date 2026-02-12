import pytest
import asyncio
import os
import sys
import uuid
from unittest.mock import MagicMock, patch
from sari.mcp.daemon import SariDaemon
import sari.mcp.daemon as daemon_mod


@pytest.mark.asyncio
async def test_daemon_init():
    daemon = SariDaemon()
    assert daemon.boot_id is not None
    assert daemon.port > 0
    assert uuid.UUID(hex=daemon.boot_id).version == 7


@pytest.mark.asyncio
async def test_daemon_start_mock(tmp_path):
    # Mock PID_FILE location
    with patch('sari.mcp.daemon.PID_FILE', tmp_path / "daemon.pid"):
        daemon = SariDaemon()
        daemon.host = "127.0.0.1"
        daemon.port = int(os.environ.get("SARI_DAEMON_PORT", 47779))

        with patch('asyncio.start_server') as mock_start:
            mock_server = MagicMock()
            mock_start.return_value = mock_server

            # Use a task to start daemon and then cancel it
            with patch.object(daemon._registry, "resolve_daemon_by_endpoint", return_value=None):
                task = asyncio.create_task(daemon.start())
                await asyncio.sleep(0.1)

                assert mock_start.called
                daemon.shutdown()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


def test_daemon_cleanup_legacy_pid(tmp_path):
    with patch('sari.mcp.daemon.PID_FILE', tmp_path / "daemon.pid"):
        daemon = SariDaemon()
        legacy = tmp_path / "daemon.pid"
        legacy.write_text("1234")
        daemon._cleanup_legacy_pid_file()
        assert not legacy.exists()


@pytest.mark.asyncio
async def test_main_exits_when_daemon_task_finishes_without_signal(monkeypatch):
    events = {"shutdown_called": 0}

    class _FakeDaemon:
        async def start_async(self):
            await asyncio.sleep(0)
            return None

        def shutdown(self):
            events["shutdown_called"] += 1

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon_mod, "SariDaemon", _FakeDaemon)

    await daemon_mod.main()
    assert events["shutdown_called"] == 1


def test_pid_file_type_error_emits_warning(monkeypatch):
    monkeypatch.setattr(daemon_mod, "PID_FILE", object())
    daemon_mod.warning_sink.clear()

    pid_path = daemon_mod._pid_file()

    assert pid_path.name == "daemon.pid"
    assert daemon_mod.warning_sink.count("PID_FILE_RESOLVE_FAILED") >= 1


def test_shutdown_warns_when_child_terminate_fails(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49993)
    daemon_mod.warning_sink.clear()

    class _Child:
        pid = 43210

        def terminate(self):
            raise RuntimeError("boom")

    fake_mp = MagicMock()
    fake_mp.active_children.return_value = [_Child()]
    monkeypatch.setitem(sys.modules, "multiprocessing", fake_mp)
    monkeypatch.setattr(daemon, "_unregister_daemon", lambda: None)
    monkeypatch.setattr(daemon, "_cleanup_legacy_pid_file", lambda: None)

    daemon.shutdown()

    assert daemon_mod.warning_sink.count("CHILD_TERMINATE_FAILED") >= 1


@pytest.mark.asyncio
async def test_main_marks_signals_disabled_on_signal_registration_failure(monkeypatch):
    events = {"signals_disabled": 0}

    class _FakeDaemon:
        async def start_async(self):
            await asyncio.sleep(0)
            return None

        def shutdown(self):
            return None

        def mark_signals_disabled(self):
            events["signals_disabled"] += 1

    loop = asyncio.get_running_loop()

    def _raise(*_args, **_kwargs):
        raise RuntimeError("signals unsupported")

    monkeypatch.setattr(loop, "add_signal_handler", _raise)
    monkeypatch.setattr(daemon_mod, "SariDaemon", _FakeDaemon)

    await daemon_mod.main()
    assert events["signals_disabled"] == 1


def test_shutdown_waits_for_server_wait_closed(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49991)
    monkeypatch.setattr(daemon, "_unregister_daemon", lambda: None)
    monkeypatch.setattr(daemon, "_cleanup_legacy_pid_file", lambda: None)
    monkeypatch.setitem(sys.modules, "multiprocessing", MagicMock(active_children=lambda: []))

    waited = {"done": 0}

    class _FakeServer:
        def close(self):
            return None

        async def wait_closed(self):
            waited["done"] += 1

    daemon.server = _FakeServer()
    daemon.shutdown(reason="test_wait_closed")
    assert waited["done"] == 1
    assert daemon.last_shutdown_reason == "test_wait_closed"


def test_shutdown_uses_kill_fallback_for_lingering_child(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49990)
    monkeypatch.setattr(daemon, "_unregister_daemon", lambda: None)
    monkeypatch.setattr(daemon, "_cleanup_legacy_pid_file", lambda: None)

    class _Child:
        pid = 32123

        def __init__(self):
            self._alive = True
            self.kill_called = 0
            self.term_called = 0
            self.join_called = 0

        def terminate(self):
            self.term_called += 1

        def join(self, timeout=None):
            self.join_called += 1

        def is_alive(self):
            return self._alive

        def kill(self):
            self.kill_called += 1
            self._alive = False

    child = _Child()
    fake_mp = MagicMock()
    fake_mp.active_children.return_value = [child]
    monkeypatch.setitem(sys.modules, "multiprocessing", fake_mp)

    daemon.shutdown(reason="test_kill_fallback")
    assert child.term_called == 1
    assert child.join_called >= 1
    assert child.kill_called == 1


def test_leases_reaped_after_ttl():
    daemon = SariDaemon(host="127.0.0.1", port=49989)
    lease_id = daemon._issue_lease("test-client", ttl_sec=0.01)
    assert daemon.active_lease_count() == 1
    daemon._reap_expired_leases(now_ts=daemon_mod.time.time() + 1.0)
    assert daemon.active_lease_count() == 0
    assert lease_id not in daemon._active_leases


def test_lease_events_are_applied_via_heartbeat_queue():
    daemon = SariDaemon(host="127.0.0.1", port=49988)
    lease_id = "lease-q-1"

    daemon._enqueue_lease_event(daemon_mod.EVENT_LEASE_ISSUE, lease_id=lease_id, client_hint="cli")
    assert daemon.active_lease_count() == 0

    daemon._apply_lease_events(now_ts=daemon_mod.time.time())
    assert daemon.active_lease_count() == 1

    daemon._enqueue_lease_event(daemon_mod.EVENT_LEASE_REVOKE, lease_id=lease_id, reason="connection_closed")
    daemon._apply_lease_events(now_ts=daemon_mod.time.time())
    assert daemon.active_lease_count() == 0


def test_event_drain_respects_max_events():
    daemon = SariDaemon(host="127.0.0.1", port=49987)
    daemon._enqueue_lease_event(daemon_mod.EVENT_LEASE_ISSUE, lease_id="l1", client_hint="a")
    daemon._enqueue_lease_event(daemon_mod.EVENT_LEASE_ISSUE, lease_id="l2", client_hint="b")

    daemon._apply_lease_events(max_events=1)
    assert daemon.active_lease_count() == 1
    daemon._apply_lease_events(max_events=1)
    assert daemon.active_lease_count() == 2


def test_event_drain_coalesces_tick_and_processes_non_tick():
    daemon = SariDaemon(host="127.0.0.1", port=49986)
    daemon._enqueue_event(daemon_mod.DaemonEvent(event_type=daemon_mod.EVENT_HEARTBEAT_TICK))
    daemon._enqueue_event(daemon_mod.DaemonEvent(event_type=daemon_mod.EVENT_HEARTBEAT_TICK))
    daemon._enqueue_lease_event(daemon_mod.EVENT_LEASE_ISSUE, lease_id="l1", client_hint="cli")

    daemon._apply_lease_events(max_events=10)
    assert daemon.active_lease_count() == 1


def test_cleanup_old_logs_removes_only_stale_managed_logs(tmp_path):
    old_log = tmp_path / "daemon.log.1"
    new_log = tmp_path / "daemon.log"
    old_trace = tmp_path / "mcp_trace.log"
    keep_text = tmp_path / "notes.txt"

    old_log.write_text("old", encoding="utf-8")
    new_log.write_text("new", encoding="utf-8")
    old_trace.write_text("trace", encoding="utf-8")
    keep_text.write_text("notes", encoding="utf-8")

    now = 200_000.0
    old_ts = now - (20 * 86400)
    new_ts = now - (1 * 86400)
    os.utime(old_log, (old_ts, old_ts))
    os.utime(old_trace, (old_ts, old_ts))
    os.utime(new_log, (new_ts, new_ts))
    os.utime(keep_text, (old_ts, old_ts))

    removed = daemon_mod._cleanup_old_logs(tmp_path, retention_days=14, now_ts=now)

    assert removed == 2
    assert not old_log.exists()
    assert not old_trace.exists()
    assert new_log.exists()
    assert keep_text.exists()


def test_cleanup_old_logs_can_be_disabled(tmp_path):
    old_log = tmp_path / "daemon.log.1"
    old_log.write_text("old", encoding="utf-8")
    now = 100_000.0
    old_ts = now - (20 * 86400)
    os.utime(old_log, (old_ts, old_ts))

    removed = daemon_mod._cleanup_old_logs(tmp_path, retention_days=0, now_ts=now)

    assert removed == 0
    assert old_log.exists()
