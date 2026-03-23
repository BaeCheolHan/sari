"""FileScanner flush의 DB 원자성을 검증한다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern

from sari.core.models import CollectedFileL1DTO, EnqueueRequestDTO, RepoIdentityDTO
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.schema import connect, init_schema
from sari.services.collection.l1.scanner import FileScanner


def _build_scanner(*, db_path: Path) -> tuple[FileScanner, FileCollectionRepository, FileEnrichQueueRepository]:
    file_repo = FileCollectionRepository(db_path)
    queue_repo = FileEnrichQueueRepository(db_path)
    scanner = FileScanner(
        file_repo=file_repo,
        enrich_queue_repo=queue_repo,
        candidate_index_sink=None,
        resolve_lsp_language=lambda relative_path: None,
        configure_lsp_prewarm_languages=lambda repo_root, counts, samples: None,
        schedule_lsp_probe_for_file=None,
        resolve_repo_identity=lambda repo_root: RepoIdentityDTO(
            repo_id="repo_id",
            repo_label="repo",
            repo_root=repo_root,
            workspace_root=str(Path(repo_root).parent),
            updated_at="2026-03-23T00:00:00+00:00",
        ),
        load_gitignore_spec=lambda repo_root: PathSpec.from_lines(GitWildMatchPattern, []),
        is_collectible=lambda file_path, repo_root, gitignore_spec: True,
        priority_low=10,
        priority_medium=20,
        scan_flush_batch_size=100,
        scan_flush_interval_sec=10.0,
        scan_hash_max_workers=1,
    )
    return scanner, file_repo, queue_repo


def test_flush_scan_buffers_rolls_back_file_rows_when_queue_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """queue write가 잠금 오류로 실패하면 file rows만 부분 반영되면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    scanner, _, queue_repo = _build_scanner(db_path=db_path)

    l1_row = CollectedFileL1DTO(
        repo_id="repo_id",
        repo_root="/repo",
        scope_repo_root="/repo",
        relative_path="src/app.py",
        absolute_path="/repo/src/app.py",
        repo_label="repo",
        mtime_ns=1,
        size_bytes=1,
        content_hash="hash-1",
        is_deleted=False,
        last_seen_at="2026-03-23T00:00:00+00:00",
        updated_at="2026-03-23T00:00:00+00:00",
        enrich_state="PENDING",
    )
    enqueue_request = EnqueueRequestDTO(
        repo_id="repo_id",
        repo_root="/repo",
        scope_repo_root="/repo",
        relative_path="src/app.py",
        content_hash="hash-1",
        priority=10,
        enqueue_source="scan",
        now_iso="2026-03-23T00:00:00+00:00",
    )

    def _raise_locked(requests, *, conn=None):  # noqa: ANN001
        _ = (requests, conn)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(queue_repo, "enqueue_many", _raise_locked)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        scanner._flush_scan_buffers(
            l1_rows=[l1_row],
            enqueue_requests=[enqueue_request],
            candidate_changes=[],
        )

    with connect(db_path) as conn:
        file_count = conn.execute("SELECT COUNT(*) FROM collected_files_l1").fetchone()[0]
        queue_count = conn.execute("SELECT COUNT(*) FROM file_enrich_queue").fetchone()[0]

    assert int(file_count) == 0
    assert int(queue_count) == 0
