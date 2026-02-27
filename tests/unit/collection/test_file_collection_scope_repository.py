"""FileCollectionRepository scope 조회 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CollectedFileL1DTO
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.schema import init_schema


def _row(
    *,
    repo_id: str,
    repo_root: str,
    scope_repo_root: str,
    relative_path: str,
    absolute_path: str,
    content_hash: str,
    updated_at: str,
    is_deleted: bool = False,
) -> CollectedFileL1DTO:
    return CollectedFileL1DTO(
        repo_id=repo_id,
        repo_root=repo_root,
        scope_repo_root=scope_repo_root,
        relative_path=relative_path,
        absolute_path=absolute_path,
        repo_label=repo_id,
        mtime_ns=1,
        size_bytes=10,
        content_hash=content_hash,
        is_deleted=is_deleted,
        last_seen_at=updated_at,
        updated_at=updated_at,
        enrich_state="DONE",
    )


def test_file_collection_repository_list_and_get_by_scope(tmp_path: Path) -> None:
    """scope 기준 목록/경로 조회가 동작해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileCollectionRepository(db_path)

    scope_root = str((tmp_path / "mono").resolve())
    module_a = str((tmp_path / "mono" / "a").resolve())
    module_b = str((tmp_path / "mono" / "b").resolve())

    repo.upsert_file(
        _row(
            repo_id="a",
            repo_root=module_a,
            scope_repo_root=scope_root,
            relative_path="src/main.py",
            absolute_path=str((tmp_path / "mono" / "a" / "src" / "main.py").resolve()),
            content_hash="hash-a",
            updated_at="2026-02-25T00:00:01+00:00",
        )
    )
    repo.upsert_file(
        _row(
            repo_id="b",
            repo_root=module_b,
            scope_repo_root=scope_root,
            relative_path="src/main.py",
            absolute_path=str((tmp_path / "mono" / "b" / "src" / "main.py").resolve()),
            content_hash="hash-b",
            updated_at="2026-02-25T00:00:02+00:00",
        )
    )
    repo.upsert_file(
        _row(
            repo_id="b-del",
            repo_root=module_b,
            scope_repo_root=scope_root,
            relative_path="src/old.py",
            absolute_path=str((tmp_path / "mono" / "b" / "src" / "old.py").resolve()),
            content_hash="hash-del",
            updated_at="2026-02-25T00:00:03+00:00",
            is_deleted=True,
        )
    )

    listed = repo.list_files_by_scope(scope_repo_root=scope_root, limit=10)
    assert [item.relative_path for item in listed] == ["src/main.py", "src/main.py"]
    assert {item.repo for item in listed} == {module_a, module_b}

    candidates = repo.get_files_by_scope(scope_repo_root=scope_root, relative_path="src/main.py", limit=10)
    assert len(candidates) == 2
    assert {item.repo_root for item in candidates} == {module_a, module_b}

    assert repo.count_distinct_repo_roots_by_scope(scope_repo_root=scope_root) == 2
    assert repo.count_active_files_by_scope(scope_repo_root=scope_root) == 2
