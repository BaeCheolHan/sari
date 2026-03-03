"""repo_context_resolver의 workspace active 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import WorkspaceDTO
from sari.core.repo.context_resolver import (
    ERR_WORKSPACE_INACTIVE,
    WORKSPACE_INACTIVE_MESSAGE,
    resolve_repo_context,
)
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema


def test_resolve_repo_context_rejects_inactive_workspace(tmp_path: Path) -> None:
    """resolve_repo_context는 비활성 workspace 경로를 거부해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-inactive"
    repo_dir.mkdir()
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(repo_dir.resolve()),
            name="repo-inactive",
            indexed_at=None,
            is_active=False,
        )
    )

    resolved, error = resolve_repo_context(
        raw_repo=str(repo_dir.resolve()),
        workspace_repo=workspace_repo,
        allow_absolute_input=True,
    )

    assert resolved is None
    assert error is not None
    assert error.code == ERR_WORKSPACE_INACTIVE
    assert error.message == WORKSPACE_INACTIVE_MESSAGE
