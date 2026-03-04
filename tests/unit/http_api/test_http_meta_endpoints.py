"""HTTP meta endpoint lifecycle helpers를 검증한다."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from sari.http import meta_endpoints


def test_daemon_health_state_honors_custom_stale_timeout(monkeypatch) -> None:  # noqa: ANN001
    runtime = SimpleNamespace(pid=4321, last_heartbeat_at="2026-03-01T00:00:00+00:00", lease_expires_at=None)

    monkeypatch.setattr(meta_endpoints, "_is_pid_alive", lambda _pid: True)
    monkeypatch.setattr(meta_endpoints, "_heartbeat_age_sec", lambda _ts: 20.0)
    monkeypatch.setattr(meta_endpoints, "_lease_valid", lambda _lease: True)

    assert meta_endpoints._daemon_health_state(runtime=runtime, stale_timeout_sec=30.0) == "running"
    assert meta_endpoints._daemon_health_state(runtime=runtime) == "stale"


def test_is_pid_alive_returns_false_when_process_missing(monkeypatch) -> None:  # noqa: ANN001
    def _raise_missing(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(meta_endpoints.os, "kill", _raise_missing)

    assert meta_endpoints._is_pid_alive(4321) is False


def test_daemon_status_reason_uses_registry_degraded_signal() -> None:
    runtime = SimpleNamespace(pid=4321, last_heartbeat_at="2026-03-01T00:00:00+00:00", lease_expires_at=None)

    reason = meta_endpoints._daemon_status_reason(
        runtime=runtime,
        stale_timeout_sec=30.0,
        health_state="degraded",
        lease_valid=True,
        registry_degraded=True,
    )
    assert reason == "registry_degraded_but_pid_alive"


def test_registry_snapshot_swallows_sqlite_error() -> None:
    class _RegistryRepo:
        def list_all(self):  # noqa: ANN201, ANN001
            raise sqlite3.OperationalError("database is locked")

    context = SimpleNamespace(admin_service=SimpleNamespace(_registry_repo=_RegistryRepo()))
    snapshot = meta_endpoints._registry_snapshot(context=context, pid=1234)
    assert snapshot is None
