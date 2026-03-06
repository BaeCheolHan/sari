"""MCP 운영 도구(doctor/rescan/repo_candidates)를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sari.core.exceptions import DaemonError
from sari.core.models import ErrorResponseDTO, RepoValidationResultDTO, WarningDTO, WorkspaceDTO
from sari.core.repo.identity import compute_repo_id, resolve_workspace_root
from sari.core.repo.context_resolver import (
    ERR_WORKSPACE_INACTIVE,
    RepoContextDTO,
    WORKSPACE_INACTIVE_MESSAGE,
    resolve_repo_context,
)
from sari.core.repo.resolver import resolve_repo_key
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.admin import AdminService

ERR_REPO_ARGUMENT_CONFLICT = "ERR_REPO_ARGUMENT_CONFLICT"
REPO_ARGUMENT_CONFLICT_MESSAGE = "repo/repo_key/repo_id must match"
ERR_REPO_NOT_REGISTERED = "ERR_REPO_NOT_REGISTERED"
ERR_REPO_PATH_DEPRECATED = "ERR_REPO_PATH_DEPRECATED"
WARN_REPO_LEGACY_KEY_FALLBACK = "WARN_REPO_LEGACY_KEY_FALLBACK"
WARN_REPO_PATH_DEPRECATED = "WARN_REPO_PATH_DEPRECATED"
WARN_REPO_ALIAS_USED = "WARN_REPO_ALIAS_USED"


class RepoValidationPort(Protocol):
    """repo 인자 검증에 필요한 워크스페이스 조회 포트."""

    def get_by_path(self, path: str) -> WorkspaceDTO | None: ...
    def list_all(self) -> list[WorkspaceDTO]: ...


def validate_repo_argument(arguments: dict[str, object], workspace_repo: RepoValidationPort) -> RepoValidationResultDTO:
    """repo 인자를 검증하고 정규화 결과 DTO를 반환한다."""
    received: dict[str, str] = {}
    for key in ("repo", "repo_key", "repo_id"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip() != "":
            received[key] = value.strip()

    if len(received) == 0:
        return RepoValidationResultDTO(
            repo_id=None,
            repo_root=None,
            repo_key=None,
            error=ErrorResponseDTO(code="ERR_REPO_REQUIRED", message="repo is required"),
        )

    source_key = "repo"
    if "repo" in received:
        raw_repo = received["repo"]
    elif "repo_key" in received:
        source_key = "repo_key"
        raw_repo = received["repo_key"]
    else:
        source_key = "repo_id"
        raw_repo = received["repo_id"]

    warnings: list[WarningDTO] = []
    if source_key != "repo":
        warnings.append(WarningDTO(code=WARN_REPO_ALIAS_USED, message=f"{source_key} is deprecated; use repo"))
    repo_registry_repo = _resolve_repo_registry_repo(workspace_repo=workspace_repo)

    resolved_context: RepoContextDTO | None = None
    context_error: ErrorResponseDTO | None = None
    if _looks_like_absolute_path(raw_repo):
        warnings.append(
            WarningDTO(
                code=WARN_REPO_PATH_DEPRECATED,
                message="absolute repo path input is deprecated and will be blocked in next release",
            )
        )
        resolved_context, context_error = _resolve_absolute_repo_input(
            raw_repo=raw_repo,
            workspace_repo=workspace_repo,
            repo_registry_repo=repo_registry_repo,
        )
    else:
        resolved_context, context_error = _resolve_identifier_repo_input(
            raw_repo=raw_repo,
            workspace_repo=workspace_repo,
            repo_registry_repo=repo_registry_repo,
        )
        if resolved_context is not None and context_error is None and raw_repo != resolved_context.repo_id:
            warnings.append(
                WarningDTO(
                    code=WARN_REPO_LEGACY_KEY_FALLBACK,
                    message="repo key fallback is deprecated; use repo_id token",
                )
            )

    if resolved_context is None:
        if context_error is None:
            context_error = ErrorResponseDTO(code=ERR_REPO_NOT_REGISTERED, message=f"repo is not registered: {raw_repo}")
        return RepoValidationResultDTO(repo_id=None, repo_root=None, repo_key=None, error=context_error, warnings=tuple(warnings))

    for key, value in received.items():
        if key == source_key:
            continue
        normalized_value = value.strip()
        if key == "repo_key":
            if normalized_value == resolved_context.repo_key:
                continue
            candidate_context = _resolve_repo_argument_identity(
                value=value,
                workspace_repo=workspace_repo,
                repo_registry_repo=repo_registry_repo,
            )
            if candidate_context is None or candidate_context.repo_id != resolved_context.repo_id:
                return RepoValidationResultDTO(
                    repo_id=None,
                    repo_root=None,
                    repo_key=None,
                    error=ErrorResponseDTO(code=ERR_REPO_ARGUMENT_CONFLICT, message=REPO_ARGUMENT_CONFLICT_MESSAGE),
                )
            continue
        if key == "repo_id" and normalized_value == resolved_context.repo_id:
            continue
        if key == "repo_id":
            candidate_context = _resolve_repo_argument_identity(
                value=value,
                workspace_repo=workspace_repo,
                repo_registry_repo=repo_registry_repo,
            )
            if candidate_context is None or candidate_context.repo_id != resolved_context.repo_id:
                return RepoValidationResultDTO(
                    repo_id=None,
                    repo_root=None,
                    repo_key=None,
                    error=ErrorResponseDTO(code=ERR_REPO_ARGUMENT_CONFLICT, message=REPO_ARGUMENT_CONFLICT_MESSAGE),
                )
            continue
        candidate_context = _resolve_repo_argument_identity(
            value=value,
            workspace_repo=workspace_repo,
            repo_registry_repo=repo_registry_repo,
        )
        # repo가 유효하면 해석 불가한 보조 필드는 무시하되,
        # 유효하게 해석되어 다른 저장소를 가리키는 경우만 충돌로 처리한다.
        if candidate_context is not None and candidate_context.repo_id != resolved_context.repo_id:
            return RepoValidationResultDTO(
                repo_id=None,
                repo_root=None,
                repo_key=None,
                error=ErrorResponseDTO(code=ERR_REPO_ARGUMENT_CONFLICT, message=REPO_ARGUMENT_CONFLICT_MESSAGE),
            )

    arguments["repo"] = resolved_context.repo_root
    arguments["repo_id"] = resolved_context.repo_id
    arguments["repo_key"] = resolved_context.repo_key
    return RepoValidationResultDTO(
        repo_id=resolved_context.repo_id,
        repo_root=resolved_context.repo_root,
        repo_key=resolved_context.repo_key,
        error=None,
        warnings=tuple(warnings),
    )


def _resolve_identifier_repo_input(
    *,
    raw_repo: str,
    workspace_repo: RepoValidationPort,
    repo_registry_repo: RepoRegistryRepository | None,
) -> tuple[RepoContextDTO | None, ErrorResponseDTO | None]:
    """식별자 입력(repo/repo_id/repo_key alias)을 repo_id 우선으로 해석한다."""
    if repo_registry_repo is not None:
        identity = repo_registry_repo.get_by_repo_id(raw_repo)
        if identity is not None:
            workspace_match = _resolve_workspace_by_path(workspace_repo=workspace_repo, raw_path=identity.repo_root)
            if workspace_match is None:
                return (None, ErrorResponseDTO(code=ERR_REPO_NOT_REGISTERED, message=f"repo is not registered: {raw_repo}"))
            if workspace_match is not None and not workspace_match.is_active:
                return (None, ErrorResponseDTO(code=ERR_WORKSPACE_INACTIVE, message=WORKSPACE_INACTIVE_MESSAGE))
            return (
                RepoContextDTO(repo_id=identity.repo_id, repo_root=identity.repo_root, repo_key=identity.repo_label),
                None,
            )
        label_identity = repo_registry_repo.get_by_repo_label(raw_repo)
        if label_identity is not None:
            workspace_match = _resolve_workspace_by_path(workspace_repo=workspace_repo, raw_path=label_identity.repo_root)
            if workspace_match is not None:
                if not workspace_match.is_active:
                    return (None, ErrorResponseDTO(code=ERR_WORKSPACE_INACTIVE, message=WORKSPACE_INACTIVE_MESSAGE))
                return (
                    RepoContextDTO(
                        repo_id=label_identity.repo_id,
                        repo_root=label_identity.repo_root,
                        repo_key=label_identity.repo_label,
                    ),
                    None,
                )
            # stale label row면 registry 결과를 신뢰하지 않고 resolver fallback으로 진행한다.
    context, error = _resolve_context_with_workspace_fallback(
        raw_repo=raw_repo,
        workspace_repo=workspace_repo,
        allow_absolute_input=False,
        repo_registry_repo=repo_registry_repo,
    )
    if context is None:
        if error is not None:
            return (None, error)
        return (None, ErrorResponseDTO(code=ERR_REPO_NOT_REGISTERED, message=f"repo is not registered: {raw_repo}"))
    return (context, error)


def _resolve_absolute_repo_input(
    *,
    raw_repo: str,
    workspace_repo: RepoValidationPort,
    repo_registry_repo: RepoRegistryRepository | None,
) -> tuple[RepoContextDTO | None, ErrorResponseDTO | None]:
    """절대경로 입력을 전환기 정책(v2.N)으로 제한 해석한다."""
    normalized = str(Path(raw_repo).expanduser().resolve())
    if repo_registry_repo is not None:
        identity = repo_registry_repo.get_by_repo_root(normalized)
        if identity is not None:
            workspace_match = _resolve_workspace_by_path(workspace_repo=workspace_repo, raw_path=identity.repo_root)
            if workspace_match is None:
                return (None, ErrorResponseDTO(code=ERR_REPO_PATH_DEPRECATED, message="absolute repo path is deprecated; use repo identifier"))
            if not workspace_match.is_active:
                return (None, ErrorResponseDTO(code=ERR_WORKSPACE_INACTIVE, message=WORKSPACE_INACTIVE_MESSAGE))
            return (
                RepoContextDTO(repo_id=identity.repo_id, repo_root=identity.repo_root, repo_key=identity.repo_label),
                None,
            )

    workspace_match = _resolve_workspace_by_path(workspace_repo=workspace_repo, raw_path=normalized)
    if workspace_match is None:
        return (None, ErrorResponseDTO(code=ERR_REPO_PATH_DEPRECATED, message="absolute repo path is deprecated; use repo identifier"))
    if not workspace_match.is_active:
        return (None, ErrorResponseDTO(code=ERR_WORKSPACE_INACTIVE, message=WORKSPACE_INACTIVE_MESSAGE))
    return _resolve_context_with_workspace_fallback(
        raw_repo=normalized,
        workspace_repo=workspace_repo,
        allow_absolute_input=False,
        repo_registry_repo=repo_registry_repo,
    )


def _resolve_context_with_workspace_fallback(
    *,
    raw_repo: str,
    workspace_repo: RepoValidationPort,
    allow_absolute_input: bool,
    repo_registry_repo: RepoRegistryRepository | None,
) -> tuple[RepoContextDTO | None, ErrorResponseDTO | None]:
    """repo_context 해석 후 absolute path 입력에 대해 workspace fallback을 적용한다."""
    resolved_context, context_error = resolve_repo_context(
        raw_repo=raw_repo,
        workspace_repo=workspace_repo,
        repo_registry_repo=repo_registry_repo,
        allow_absolute_input=allow_absolute_input,
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
    workspace_root = resolve_workspace_root(repo_root=workspace_match.path, workspace_paths=workspace_paths)
    repo_id = compute_repo_id(repo_label=resolved_key, workspace_root=workspace_root)
    return (
        RepoContextDTO(repo_id=repo_id, repo_root=workspace_match.path, repo_key=resolved_key),
        None,
    )


def _resolve_repo_argument_identity(
    *,
    value: str,
    workspace_repo: RepoValidationPort,
    repo_registry_repo: RepoRegistryRepository | None,
) -> RepoContextDTO | None:
    """충돌 판정용으로 입력 필드를 RepoContext로 해석한다."""
    raw_value = value.strip()
    if raw_value == "":
        return None
    if _looks_like_absolute_path(raw_value):
        context, error = _resolve_absolute_repo_input(
            raw_repo=raw_value,
            workspace_repo=workspace_repo,
            repo_registry_repo=repo_registry_repo,
        )
    else:
        context, error = _resolve_identifier_repo_input(
            raw_repo=raw_value,
            workspace_repo=workspace_repo,
            repo_registry_repo=repo_registry_repo,
        )
    if error is not None:
        return None
    return context


def _resolve_workspace_by_path(*, workspace_repo: RepoValidationPort, raw_path: str) -> WorkspaceDTO | None:
    """워크스페이스 경로를 문자열/resolve 동치까지 허용해 조회한다."""
    direct = workspace_repo.get_by_path(raw_path)
    if direct is not None:
        return direct
    try:
        normalized = str(Path(raw_path).expanduser().resolve())
    except OSError:
        return None
    for item in workspace_repo.list_all():
        try:
            if str(Path(item.path).expanduser().resolve()) == normalized:
                return item
        except OSError:
            continue
    return None


def _resolve_repo_registry_repo(workspace_repo: RepoValidationPort) -> RepoRegistryRepository | None:
    """workspace repository가 DB 경로를 노출하면 repo registry 저장소를 구성한다."""
    db_path = getattr(workspace_repo, "_db_path", None)
    if db_path is None:
        return None
    return RepoRegistryRepository(Path(db_path))


def _looks_like_absolute_path(value: str) -> bool:
    """입력이 절대경로 형태인지 판별한다."""
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        return True
    return len(value) >= 2 and value[1] == ":"


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

        try:
            result = self._admin_service.index()
        except DaemonError as exc:
            return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))
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
