"""데몬 DB fail-fast 보조 로직을 검증한다."""

from __future__ import annotations

import signal
import sqlite3
import threading

from pytest import MonkeyPatch

from sari.daemon_process import _handle_auto_loop_event_record_failure, _is_fatal_db_error, _trigger_fatal_shutdown


def test_is_fatal_db_error_detects_critical_operational_error() -> None:
    """치명 DB 오류 패턴은 fail-fast 대상으로 분류해야 한다."""
    assert _is_fatal_db_error(sqlite3.OperationalError("disk I/O error")) is True
    assert _is_fatal_db_error(sqlite3.OperationalError("no such table: workspaces")) is True
    assert _is_fatal_db_error(sqlite3.OperationalError("database disk image is malformed")) is True


def test_is_fatal_db_error_ignores_non_critical_error() -> None:
    """비치명 DB 오류는 즉시 종료 대상으로 분류하지 않아야 한다."""
    assert _is_fatal_db_error(sqlite3.OperationalError("database is locked")) is False


def test_trigger_fatal_shutdown_marks_reason_and_sends_sigterm(monkeypatch: MonkeyPatch) -> None:
    """치명 오류 트리거는 종료 사유 기록 후 SIGTERM을 보내야 한다."""

    class _RuntimeRepo:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str, str]] = []

        def mark_exit_reason(self, pid: int, reason: str, at: str) -> None:
            self.calls.append((pid, reason, at))

    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr("sari.daemon_process.os.kill", lambda pid, sig: kill_calls.append((pid, int(sig))))

    stop_event = threading.Event()
    shutdown_reason = {"value": ""}
    runtime_repo = _RuntimeRepo()

    _trigger_fatal_shutdown(
        stop_event=stop_event,
        runtime_repo=runtime_repo,
        pid=12345,
        reason="DB_FATAL_HEARTBEAT",
        shutdown_reason=shutdown_reason,
    )

    assert stop_event.is_set() is True
    assert shutdown_reason["value"] == "DB_FATAL_HEARTBEAT"
    assert runtime_repo.calls and runtime_repo.calls[0][1] == "DB_FATAL_HEARTBEAT"
    assert kill_calls == [(12345, int(signal.SIGTERM))]


def test_handle_auto_loop_event_record_failure_continues_on_non_fatal_db_error(monkeypatch: MonkeyPatch) -> None:
    """비치명 DB 오류는 auto-loop 지속을 위해 continue 경로를 반환해야 한다."""

    class _StopEventStub:
        def __init__(self) -> None:
            self.wait_calls: list[float] = []

        def wait(self, timeout: float) -> bool:
            self.wait_calls.append(timeout)
            return False

    class _RuntimeRepo:
        def mark_exit_reason(self, pid: int, reason: str, at: str) -> None:
            _ = (pid, reason, at)

    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr("sari.daemon_process.os.kill", lambda pid, sig: kill_calls.append((pid, int(sig))))

    stop_event = _StopEventStub()
    shutdown_reason = {"value": ""}
    should_continue = _handle_auto_loop_event_record_failure(
        event_exc=sqlite3.OperationalError("database is locked"),
        stop_event=stop_event,  # type: ignore[arg-type]
        runtime_repo=_RuntimeRepo(),  # type: ignore[arg-type]
        pid=7,
        shutdown_reason=shutdown_reason,
        tick_wait=0.25,
    )
    assert should_continue is True
    assert stop_event.wait_calls == [0.25]
    assert shutdown_reason["value"] == ""
    assert kill_calls == []
