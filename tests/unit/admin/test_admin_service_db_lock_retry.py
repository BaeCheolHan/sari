from __future__ import annotations

import sqlite3
from pathlib import Path

from pytest import MonkeyPatch

from sari.core.config import AppConfig
from sari.core.exceptions import DaemonError
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.admin.service import AdminService


class _FlakySymbolCacheRepo:
    def __init__(self, fail_count: int) -> None:
        self._fail_count = fail_count
        self.calls = 0

    def invalidate_all(self) -> int:
        self.calls += 1
        if self.calls <= self._fail_count:
            raise sqlite3.OperationalError("database is locked")
        return 7


def _build_service(db_path: Path, symbol_cache_repo: object) -> AdminService:
    return AdminService(
        config=AppConfig.default(),
        workspace_repo=WorkspaceRepository(db_path),
        runtime_repo=RuntimeRepository(db_path),
        symbol_cache_repo=symbol_cache_repo,  # type: ignore[arg-type]
    )


def test_index_retries_on_database_locked_then_succeeds(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = _FlakySymbolCacheRepo(fail_count=2)
    service = _build_service(db_path, repo)
    sleeps: list[float] = []
    monkeypatch.setattr("sari.db.sqlite_retry.time.sleep", lambda sec: sleeps.append(sec))

    payload = service.index()

    assert payload["invalidated_cache_rows"] == 7
    assert payload["lock_retry_count"] == 2
    assert repo.calls == 3
    assert sleeps == [0.05, 0.1]


def test_index_raises_domain_error_when_lock_retries_exhausted(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = _FlakySymbolCacheRepo(fail_count=10)
    service = _build_service(db_path, repo)
    monkeypatch.setattr("sari.db.sqlite_retry.time.sleep", lambda _sec: None)

    try:
        _ = service.index()
    except DaemonError as exc:
        assert exc.context.code == "ERR_DB_LOCK_BUSY"
    else:
        raise AssertionError("DaemonError must be raised when DB lock retry is exhausted")


def test_index_propagates_non_lock_operational_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    class _BrokenSymbolCacheRepo:
        def invalidate_all(self) -> int:
            raise sqlite3.OperationalError("malformed database schema")

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = _build_service(db_path, _BrokenSymbolCacheRepo())
    monkeypatch.setattr("sari.db.sqlite_retry.time.sleep", lambda _sec: None)

    try:
        _ = service.index()
    except sqlite3.OperationalError as exc:
        assert "malformed database schema" in str(exc)
    else:
        raise AssertionError("Non-lock OperationalError must be propagated")
