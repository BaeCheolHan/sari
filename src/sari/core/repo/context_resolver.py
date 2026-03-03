"""repo 입력값을 내부 실행 컨텍스트로 정규화한다."""

from __future__ import annotations

from dataclasses import dataclass

from sari.core.exceptions import ValidationError
from sari.core.models import ErrorResponseDTO, WorkspaceDTO
from sari.core.repo.identity import compute_repo_id, resolve_workspace_root
from sari.core.repo.resolver import resolve_repo_key, resolve_repo_root
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository

ERR_WORKSPACE_INACTIVE = "ERR_WORKSPACE_INACTIVE"
WORKSPACE_INACTIVE_MESSAGE = "workspace is inactive"


@dataclass(frozen=True)
class RepoContextDTO:
    """repo 입력 정규화 결과를 표현한다."""

    repo_id: str
    repo_root: str
    repo_key: str


def resolve_repo_context(
    *,
    raw_repo: str,
    workspace_repo: WorkspaceRepository,
    repo_registry_repo: RepoRegistryRepository | None = None,
    allow_absolute_input: bool = False,
) -> tuple[RepoContextDTO | None, ErrorResponseDTO | None]:
    """repo 입력을 repo_id/repo_root/repo_key로 정규화한다."""
    if raw_repo.strip() == "":
        return (None, ErrorResponseDTO(code="ERR_REPO_REQUIRED", message="repo is required"))
    workspace_items = workspace_repo.list_all()
    workspace_paths = [item.path for item in workspace_items]
    try:
        resolved_repo = resolve_repo_root(
            repo_or_path=raw_repo.strip(),
            workspace_paths=workspace_paths,
            allow_absolute_input=allow_absolute_input,
        )
        resolved_key = resolve_repo_key(repo_root=resolved_repo, workspace_paths=workspace_paths)
    except ValidationError as exc:
        return (None, ErrorResponseDTO(code=exc.context.code, message=exc.context.message))
    workspace_match = _resolve_workspace_for_repo_root(repo_root=resolved_repo, workspace_items=workspace_items)
    if workspace_match is not None and not workspace_match.is_active:
        return (None, ErrorResponseDTO(code=ERR_WORKSPACE_INACTIVE, message=WORKSPACE_INACTIVE_MESSAGE))
    workspace_root = resolve_workspace_root(repo_root=resolved_repo, workspace_paths=workspace_paths)
    repo_id = compute_repo_id(repo_label=resolved_key, workspace_root=workspace_root)
    if repo_registry_repo is not None:
        from sari.core.models import RepoIdentityDTO, now_iso8601_utc

        repo_registry_repo.upsert(
            RepoIdentityDTO(
                repo_id=repo_id,
                repo_label=resolved_key,
                repo_root=resolved_repo,
                workspace_root=workspace_root,
                updated_at=now_iso8601_utc(),
            )
        )
    return (RepoContextDTO(repo_id=repo_id, repo_root=resolved_repo, repo_key=resolved_key), None)


def _resolve_workspace_for_repo_root(repo_root: str, workspace_items: list[WorkspaceDTO]) -> WorkspaceDTO | None:
    """repo_root를 포함하는 가장 긴 workspace 항목을 반환한다.

    workspace_items 원소는 path/is_active 속성을 가진 WorkspaceDTO 계약을 충족해야 한다.
    """
    normalized_repo = resolve_workspace_root(repo_root=repo_root, workspace_paths=[item.path for item in workspace_items])
    if normalized_repo is None:
        return None
    for item in workspace_items:
        if item.path == normalized_repo:
            return item
    return None
