"""후보 인덱스 변경 로그 저장소 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CandidateIndexChangeDTO
from sari.db.repositories.candidate_index_change_repository import CandidateIndexChangeRepository
from sari.db.schema import init_schema


def test_candidate_index_change_repository_coalesces_latest_upsert(tmp_path: Path) -> None:
    """동일 파일 upsert는 최신 1건으로 coalesce되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = CandidateIndexChangeRepository(db_path)
    first = CandidateIndexChangeDTO(
        repo_id="r_repo_a",
        repo_root="/repo-a",
        relative_path="a.py",
        absolute_path="/repo-a/a.py",
        content_hash="h1",
        mtime_ns=1,
        size_bytes=10,
        event_source="scan",
        recorded_at="2026-02-16T00:00:00+00:00",
    )
    second = CandidateIndexChangeDTO(
        repo_id="r_repo_a",
        repo_root="/repo-a",
        relative_path="a.py",
        absolute_path="/repo-a/a.py",
        content_hash="h2",
        mtime_ns=2,
        size_bytes=20,
        event_source="watcher",
        recorded_at="2026-02-16T00:00:01+00:00",
    )

    repo.enqueue_upsert(first)
    repo.enqueue_upsert(second)
    items = repo.acquire_pending(limit=10)

    assert len(items) == 1
    assert items[0].change_type == "UPSERT"
    assert items[0].content_hash == "h2"
    assert items[0].event_source == "watcher"


def test_candidate_index_change_repository_delete_overwrites_pending_upsert(tmp_path: Path) -> None:
    """동일 파일에서 delete가 들어오면 pending upsert를 대체해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = CandidateIndexChangeRepository(db_path)
    dto = CandidateIndexChangeDTO(
        repo_id="r_repo_a",
        repo_root="/repo-a",
        relative_path="a.py",
        absolute_path="/repo-a/a.py",
        content_hash="h1",
        mtime_ns=1,
        size_bytes=10,
        event_source="scan",
        recorded_at="2026-02-16T00:00:00+00:00",
    )

    repo.enqueue_upsert(dto)
    repo.enqueue_delete(
        repo_id="r_repo_a",
        repo_root="/repo-a",
        relative_path="a.py",
        event_source="watcher",
        recorded_at="2026-02-16T00:00:01+00:00",
    )
    items = repo.acquire_pending(limit=10)

    assert len(items) == 1
    assert items[0].change_type == "DELETE"
    assert items[0].event_source == "watcher"
