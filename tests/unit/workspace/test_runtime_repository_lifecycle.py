"""런타임 저장소 lifecycle 필드를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import DaemonRuntimeDTO
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.schema import init_schema


def test_runtime_repository_persists_heartbeat_and_exit_reason(tmp_path: Path) -> None:
    """heartbeat/exit reason 필드는 저장/갱신되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = RuntimeRepository(db_path)

    runtime = DaemonRuntimeDTO(
        pid=12345,
        host="127.0.0.1",
        port=47777,
        state="running",
        started_at="2026-02-16T12:00:00+00:00",
        session_count=0,
        last_heartbeat_at="2026-02-16T12:00:00+00:00",
        last_exit_reason=None,
    )
    repo.upsert_runtime(runtime)

    repo.touch_heartbeat(pid=12345, heartbeat_at="2026-02-16T12:00:05+00:00")
    repo.mark_exit_reason(pid=12345, exit_reason="NORMAL_SHUTDOWN", heartbeat_at="2026-02-16T12:00:06+00:00")

    loaded = repo.get_runtime()
    assert loaded is not None
    assert loaded.last_heartbeat_at == "2026-02-16T12:00:06+00:00"
    assert loaded.last_exit_reason == "NORMAL_SHUTDOWN"
    latest_exit = repo.get_latest_exit_event()
    assert latest_exit is not None
    assert latest_exit["exit_reason"] == "NORMAL_SHUTDOWN"


def test_runtime_repository_clear_stale_runtime(tmp_path: Path) -> None:
    """stale heartbeat 런타임은 cutoff 기준으로 정리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = RuntimeRepository(db_path)

    repo.upsert_runtime(
        DaemonRuntimeDTO(
            pid=22222,
            host="127.0.0.1",
            port=47777,
            state="running",
            started_at="2026-02-16T12:00:00+00:00",
            session_count=0,
            last_heartbeat_at="2026-02-16T12:00:01+00:00",
            last_exit_reason=None,
        )
    )
    deleted = repo.clear_stale_runtime(cutoff_iso="2026-02-16T12:00:05+00:00")
    assert deleted == 1
    assert repo.get_runtime() is None


def test_runtime_repository_session_count_increment_and_decrement(tmp_path: Path) -> None:
    """session_count 증감 API는 런타임 레코드를 원자적으로 갱신해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = RuntimeRepository(db_path)

    repo.upsert_runtime(
        DaemonRuntimeDTO(
            pid=33333,
            host="127.0.0.1",
            port=47777,
            state="running",
            started_at="2026-02-16T12:00:00+00:00",
            session_count=0,
            last_heartbeat_at="2026-02-16T12:00:01+00:00",
            last_exit_reason=None,
        )
    )

    repo.increment_session()
    repo.increment_session()
    loaded = repo.get_runtime()
    assert loaded is not None
    assert loaded.session_count == 2

    repo.decrement_session()
    repo.decrement_session()
    repo.decrement_session()
    loaded = repo.get_runtime()
    assert loaded is not None
    assert loaded.session_count == 0
