"""MCP 운영 도구(doctor/rescan/repo_candidates)를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import ErrorResponseDTO
from sari.core.repo_context_resolver import (
    ERR_WORKSPACE_INACTIVE,
    WORKSPACE_INACTIVE_MESSAGE,
    resolve_repo_context,
)
from sari.core.repo_resolver import resolve_repo_key
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.admin_service import AdminService


def validate_repo_argument(arguments: dict[str, object], workspace_repo: WorkspaceRepository) -> ErrorResponseDTO | None:
    """repo 인자를 검증하고 오류 DTO를 반환한다."""
    repo = arguments.get("repo")
    if (not isinstance(repo, str) or repo.strip() == "") and isinstance(arguments.get("repo_id"), str):
        arguments["repo"] = str(arguments["repo_id"])
        repo = arguments["repo"]
    if not isinstance(repo, str) or repo.strip() == "":
        return ErrorResponseDTO(code="ERR_REPO_REQUIRED", message="repo_id is required (alias: repo)")
    repo_key_raw = arguments.get("repo_key")
    raw_repo = repo_key_raw.strip() if isinstance(repo_key_raw, str) and repo_key_raw.strip() != "" else repo.strip()
    resolved_context, context_error = resolve_repo_context(
        raw_repo=raw_repo,
        workspace_repo=workspace_repo,
        repo_registry_repo=None,
        allow_absolute_input=False,
    )
    if context_error is None and resolved_context is not None:
        arguments["repo"] = resolved_context.repo_root
        arguments["repo_key"] = resolved_context.repo_key
        return None
    workspace_match = workspace_repo.get_by_path(repo.strip())
    if workspace_match is None:
        assert context_error is not None
        return context_error
    if not workspace_match.is_active:
        return ErrorResponseDTO(code=ERR_WORKSPACE_INACTIVE, message=WORKSPACE_INACTIVE_MESSAGE)
    workspace_paths = [item.path for item in workspace_repo.list_all()]
    arguments["repo"] = workspace_match.path
    arguments["repo_key"] = resolve_repo_key(repo_root=workspace_match.path, workspace_paths=workspace_paths)
    return None


@dataclass(frozen=True)
class DoctorItemDTO:
    """doctor 응답 항목 DTO다."""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        """직렬화 가능한 딕셔너리로 변환한다."""
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


class DoctorTool:
    """doctor MCP 도구를 처리한다."""

    def __init__(self, admin_service: AdminService, workspace_repo: WorkspaceRepository) -> None:
        """필요 의존성을 주입한다."""
        self._admin_service = admin_service
        self._workspace_repo = workspace_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """doctor 응답을 pack1 형식으로 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)

        items = [
            DoctorItemDTO(name=check.name, passed=check.passed, detail=check.detail).to_dict()
            for check in self._admin_service.doctor()
        ]
        return pack1_success(
            {
                "items": items,
                "meta": Pack1MetaDTO(
                    candidate_count=len(items),
                    resolved_count=len(items),
                    cache_hit=None,
                    errors=[],
                ).to_dict(),
            }
        )


class RescanTool:
    """rescan MCP 도구를 처리한다."""

    def __init__(self, admin_service: AdminService, workspace_repo: WorkspaceRepository) -> None:
        """필요 의존성을 주입한다."""
        self._admin_service = admin_service
        self._workspace_repo = workspace_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """캐시 무효화 결과를 pack1 형식으로 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)

        result = self._admin_service.index()
        invalidated_rows = int(result.get("invalidated_cache_rows", 0))
        return pack1_success(
            {
                "items": [],
                "invalidated_cache_rows": invalidated_rows,
                "meta": Pack1MetaDTO(
                    candidate_count=0,
                    resolved_count=invalidated_rows,
                    cache_hit=None,
                    errors=[],
                ).to_dict(),
            }
        )


class RepoCandidatesTool:
    """repo_candidates MCP 도구를 처리한다."""

    def __init__(self, admin_service: AdminService, workspace_repo: WorkspaceRepository) -> None:
        """필요 의존성을 주입한다."""
        self._admin_service = admin_service
        self._workspace_repo = workspace_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장소 후보 목록을 pack1 형식으로 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)

        items = self._admin_service.repo_candidates()
        return pack1_success(
            {
                "items": items,
                "meta": Pack1MetaDTO(
                    candidate_count=len(items),
                    resolved_count=len(items),
                    cache_hit=None,
                    errors=[],
                ).to_dict(),
            }
        )
