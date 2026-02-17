"""데몬 레지스트리 저장소 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import DaemonRegistryEntryDTO
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.schema import init_schema


def test_daemon_registry_upsert_and_resolve_latest(tmp_path: Path) -> None:
    """workspace 기준 최신 non-draining 엔트리를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repository = DaemonRegistryRepository(db_path)
    repository.upsert(
        DaemonRegistryEntryDTO(
            daemon_id="d-1",
            host="127.0.0.1",
            port=47777,
            pid=111,
            workspace_root="/repo/a",
            protocol="http",
            started_at="2026-02-16T10:00:00+00:00",
            last_seen_at="2026-02-16T10:00:01+00:00",
            is_draining=False,
        )
    )
    repository.upsert(
        DaemonRegistryEntryDTO(
            daemon_id="d-2",
            host="127.0.0.1",
            port=47778,
            pid=222,
            workspace_root="/repo/a",
            protocol="http",
            started_at="2026-02-16T10:00:02+00:00",
            last_seen_at="2026-02-16T10:00:03+00:00",
            is_draining=False,
        )
    )

    resolved = repository.resolve_latest("/repo/a")
    assert resolved is not None
    assert resolved.daemon_id == "d-2"
    assert resolved.port == 47778


def test_daemon_registry_excludes_draining_entry(tmp_path: Path) -> None:
    """draining 엔트리는 resolve_latest 대상에서 제외해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repository = DaemonRegistryRepository(db_path)
    repository.upsert(
        DaemonRegistryEntryDTO(
            daemon_id="d-1",
            host="127.0.0.1",
            port=47777,
            pid=111,
            workspace_root="/repo/a",
            protocol="http",
            started_at="2026-02-16T10:00:00+00:00",
            last_seen_at="2026-02-16T10:00:01+00:00",
            is_draining=True,
        )
    )

    assert repository.resolve_latest("/repo/a") is None

