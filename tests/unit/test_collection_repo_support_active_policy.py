"""CollectionRepoSupport의 workspace 활성 정책 분리를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CollectionPolicyDTO, WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.collection.repo_support import CollectionRepoSupport


def _policy() -> CollectionPolicyDTO:
    return CollectionPolicyDTO(
        include_ext=(".py",),
        exclude_globs=(),
        max_file_size_bytes=512 * 1024,
        scan_interval_sec=120,
        max_enrich_batch=100,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        queue_poll_interval_ms=100,
    )


def test_resolve_repo_identity_uses_registered_workspace_even_if_inactive(tmp_path: Path) -> None:
    """비활성 workspace여도 등록 루트 기준으로 repo identity를 계산해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_root = tmp_path / "workspace"
    repo_root = workspace_root / "repo-a"
    repo_root.mkdir(parents=True)

    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(workspace_root.resolve()),
            name="workspace",
            indexed_at=None,
            is_active=False,
        )
    )
    support = CollectionRepoSupport(
        workspace_repo=workspace_repo,
        policy=_policy(),
        policy_repo=None,
        lsp_backend=object(),
        repo_registry_repo=None,
        lsp_prewarm_min_language_files=1,
        lsp_prewarm_top_language_count=3,
    )

    identity = support.resolve_repo_identity(str(repo_root.resolve()))

    assert identity.workspace_root == str(workspace_root.resolve())

