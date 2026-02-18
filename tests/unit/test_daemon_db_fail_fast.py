"""데몬 DB fail-fast 보조 로직을 검증한다."""

from __future__ import annotations

import signal
import sqlite3
import threading

from pytest import MonkeyPatch

from sari.daemon_process import _is_fatal_db_error, _trigger_fatal_shutdown


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
