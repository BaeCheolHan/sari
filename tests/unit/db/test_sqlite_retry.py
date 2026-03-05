from __future__ import annotations

import sqlite3

from sari.db.sqlite_retry import run_with_sqlite_lock_retry


def test_run_with_sqlite_lock_retry_returns_result_and_retry_count(monkeypatch) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []
    monkeypatch.setattr("sari.db.sqlite_retry.time.sleep", lambda sec: sleeps.append(sec))

    def _op() -> int:
        calls["count"] += 1
        if calls["count"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return 42

    result, retry_count = run_with_sqlite_lock_retry(_op)
    assert result == 42
    assert retry_count == 2
    assert sleeps == [0.05, 0.1]


def test_run_with_sqlite_lock_retry_raises_after_exhausted() -> None:
    def _op() -> int:
        raise sqlite3.OperationalError("database is locked")

    try:
        _ = run_with_sqlite_lock_retry(_op, max_attempts=2)
    except sqlite3.OperationalError as exc:
        assert "database is locked" in str(exc).lower()
    else:
        raise AssertionError("OperationalError must be raised when retry is exhausted")
