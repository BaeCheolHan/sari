"""MCP 운영 도구(doctor/rescan/repo_candidates)를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sari.core.models import ErrorResponseDTO, RepoValidationResultDTO, WarningDTO, WorkspaceDTO
from sari.core.repo_context_resolver import (
    ERR_WORKSPACE_INACTIVE,
    RepoContextDTO,
    WORKSPACE_INACTIVE_MESSAGE,
    resolve_repo_context,
)
from sari.core.repo_resolver import resolve_repo_key
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.admin_service import AdminService

ERR_REPO_ARGUMENT_CONFLICT = "ERR_REPO_ARGUMENT_CONFLICT"
REPO_ARGUMENT_CONFLICT_MESSAGE = "repo and repo_key resolve to different repositories"
WARN_REPO_ARG_PARTIAL_FALLBACK = "WARN_REPO_ARG_PARTIAL_FALLBACK"
REPO_ARG_PARTIAL_FALLBACK_MESSAGE = "repo/repo_key mismatch or invalid input; resolved by fallback"


class RepoValidationPort(Protocol):
    """repo 인자 검증에 필요한 워크스페이스 조회 포트."""

    def get_by_path(self, path: str) -> WorkspaceDTO | None: ...
    def list_all(self) -> list[WorkspaceDTO]: ...


def validate_repo_argument(arguments: dict[str, object], workspace_repo: RepoValidationPort) -> RepoValidationResultDTO:
    """repo 인자를 검증하고 정규화 결과 DTO를 반환한다."""
    repo = arguments.get("repo")
    if (not isinstance(repo, str) or repo.strip() == "") and isinstance(arguments.get("repo_id"), str):
        arguments["repo"] = str(arguments["repo_id"])
        repo = arguments["repo"]
    if not isinstance(repo, str) or repo.strip() == "":
        return RepoValidationResultDTO(
            repo_root=None,
            repo_key=None,
            error=ErrorResponseDTO(code="ERR_REPO_REQUIRED", message="repo_id is required (alias: repo)"),
        )
    repo_key_raw = arguments.get("repo_key")
    repo_value = repo.strip()
    repo_key_value = repo_key_raw.strip() if isinstance(repo_key_raw, str) and repo_key_raw.strip() != "" else None
    warnings: list[WarningDTO] = []

    def _append_partial_warning() -> None:
        for item in warnings:
            if item.code == WARN_REPO_ARG_PARTIAL_FALLBACK:
                return
        warnings.append(
            WarningDTO(code=WARN_REPO_ARG_PARTIAL_FALLBACK, message=REPO_ARG_PARTIAL_FALLBACK_MESSAGE)
        )

    if repo_key_value is not None:
        repo_context, _ = _resolve_context_with_workspace_fallback(raw_repo=repo_value, workspace_repo=workspace_repo)
        repo_key_context, _ = _resolve_context_with_workspace_fallback(raw_repo=repo_key_value, workspace_repo=workspace_repo)
        if repo_context is not None and repo_key_context is not None and repo_context.repo_root != repo_key_context.repo_root:
            return RepoValidationResultDTO(
                repo_root=None,
                repo_key=None,
                error=ErrorResponseDTO(code=ERR_REPO_ARGUMENT_CONFLICT, message=REPO_ARGUMENT_CONFLICT_MESSAGE),
            )

    raw_repo = repo_key_value if repo_key_value is not None else repo_value
    resolved_context, context_error = _resolve_context_with_workspace_fallback(raw_repo=raw_repo, workspace_repo=workspace_repo)
    if resolved_context is None and repo_key_value is not None:
        repo_context, repo_error = _resolve_context_with_workspace_fallback(raw_repo=repo_value, workspace_repo=workspace_repo)
        if repo_context is not None:
            resolved_context = repo_context
            context_error = None
            _append_partial_warning()
        else:
            context_error = context_error if context_error is not None else repo_error
    if resolved_context is None:
        assert context_error is not None
        return RepoValidationResultDTO(repo_root=None, repo_key=None, error=context_error)
    if repo_key_value is not None:
        repo_context, repo_error = _resolve_context_with_workspace_fallback(raw_repo=repo_value, workspace_repo=workspace_repo)
        repo_key_context, repo_key_error = _resolve_context_with_workspace_fallback(
            raw_repo=repo_key_value,
            workspace_repo=workspace_repo,
        )
        if (repo_context is None and repo_error is not None) or (repo_key_context is None and repo_key_error is not None):
            _append_partial_warning()
    arguments["repo"] = resolved_context.repo_root
    arguments["repo_key"] = resolved_context.repo_key
    return RepoValidationResultDTO(
        repo_root=resolved_context.repo_root,
        repo_key=resolved_context.repo_key,
        error=None,
        warnings=tuple(warnings),
    )


def _resolve_context_with_workspace_fallback(
    *,
    raw_repo: str,
    workspace_repo: RepoValidationPort,
) -> tuple[RepoContextDTO | None, ErrorResponseDTO | None]:
    """repo_context 해석 후 absolute path 입력에 대해 workspace fallback을 적용한다."""
    resolved_context, context_error = resolve_repo_context(
        raw_repo=raw_repo,
        workspace_repo=workspace_repo,
        repo_registry_repo=None,
        allow_absolute_input=False,
    )
    if context_error is None and resolved_context is not None:
        return resolved_context, None

    workspace_match = workspace_repo.get_by_path(raw_repo.strip())
    if workspace_match is None:
        return None, context_error
    if not workspace_match.is_active:
        return None, ErrorResponseDTO(code=ERR_WORKSPACE_INACTIVE, message=WORKSPACE_INACTIVE_MESSAGE)
    workspace_paths = [item.path for item in workspace_repo.list_all()]
    resolved_key = resolve_repo_key(repo_root=workspace_match.path, workspace_paths=workspace_paths)
    return (
        RepoContextDTO(repo_id="", repo_root=workspace_match.path, repo_key=resolved_key),
        None,
    )


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

    def __init__(self, admin_service: AdminService, workspace_repo: RepoValidationPort) -> None:
        """필요 의존성을 주입한다."""
        self._admin_service = admin_service
        self._workspace_repo = workspace_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """doctor 응답을 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo_root = str(arguments["repo"])

        items = [DoctorItemDTO(name="repo_scope_root", passed=True, detail=repo_root).to_dict(), *[
            DoctorItemDTO(name=check.name, passed=check.passed, detail=check.detail).to_dict()
            for check in self._admin_service.doctor()
        ]]
        return pack1_success(
            {
                "items": items,
                "meta": Pack1MetaDTO(
                    candidate_count=len(items),
                    resolved_count=len(items),
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class RescanTool:
    """rescan MCP 도구를 처리한다."""

    def __init__(self, admin_service: AdminService, workspace_repo: RepoValidationPort) -> None:
        """필요 의존성을 주입한다."""
        self._admin_service = admin_service
        self._workspace_repo = workspace_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """캐시 무효화 결과를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]

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
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class RepoCandidatesTool:
    """repo_candidates MCP 도구를 처리한다."""

    def __init__(self, admin_service: AdminService, workspace_repo: RepoValidationPort) -> None:
        """필요 의존성을 주입한다."""
        self._admin_service = admin_service
        self._workspace_repo = workspace_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장소 후보 목록을 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]

        items = self._admin_service.repo_candidates()
        return pack1_success(
            {
                "items": items,
                "meta": Pack1MetaDTO(
                    candidate_count=len(items),
                    resolved_count=len(items),
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )
