from __future__ import annotations

from pathlib import Path

from sari.core.models import ToolReadinessStateDTO
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.schema import init_schema


def test_upsert_state_many_does_not_downgrade_same_hash_ok_readiness(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolReadinessRepository(db_path)

    repo.upsert_state(
        ToolReadinessStateDTO(
            repo_root="/repo",
            scope_repo_root="/repo",
            relative_path="src/a.py",
            content_hash="h1",
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=True,
            get_callers_ready=True,
            consistency_ready=True,
            quality_ready=True,
            tool_ready=True,
            last_reason="ok",
            updated_at="2026-03-16T12:00:00+00:00",
        )
    )

    repo.upsert_state_many([
        ToolReadinessStateDTO(
            repo_root="/repo",
            scope_repo_root="/repo",
            relative_path="src/a.py",
            content_hash="h1",
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=True,
            get_callers_ready=False,
            consistency_ready=True,
            quality_ready=True,
            tool_ready=True,
            last_reason="l3_preprocess_supported_language",
            updated_at="2026-03-16T12:01:00+00:00",
        )
    ])

    state = repo.get_state(repo_root="/repo", relative_path="src/a.py")

    assert state is not None
    assert state.content_hash == "h1"
    assert state.get_callers_ready is True
    assert state.last_reason == "ok"


def test_upsert_state_many_does_not_downgrade_same_hash_ok_readiness_for_tsls_fast_path(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = ToolReadinessRepository(db_path)

    repo.upsert_state(
        ToolReadinessStateDTO(
            repo_root="/repo",
            scope_repo_root="/repo",
            relative_path="src/a.ts",
            content_hash="h1",
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=True,
            get_callers_ready=True,
            consistency_ready=True,
            quality_ready=True,
            tool_ready=True,
            last_reason="ok",
            updated_at="2026-03-16T12:00:00+00:00",
        )
    )

    repo.upsert_state_many([
        ToolReadinessStateDTO(
            repo_root="/repo",
            scope_repo_root="/repo",
            relative_path="src/a.ts",
            content_hash="h1",
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=True,
            get_callers_ready=False,
            consistency_ready=True,
            quality_ready=True,
            tool_ready=True,
            last_reason="l3_preprocess_tsls_fast_path",
            updated_at="2026-03-16T12:01:00+00:00",
        )
    ])

    state = repo.get_state(repo_root="/repo", relative_path="src/a.ts")

    assert state is not None
    assert state.get_callers_ready is True
    assert state.last_reason == "ok"
