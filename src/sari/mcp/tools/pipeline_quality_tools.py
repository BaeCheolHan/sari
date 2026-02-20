"""MCP 파이프라인 품질 도구를 제공한다."""

from __future__ import annotations

from sari.core.exceptions import QualityError
from sari.core.models import ErrorResponseDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.arg_parser import parse_string_list
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.pipeline_quality_service import PipelineQualityService


def _parse_language_filter(arguments: dict[str, object]) -> tuple[tuple[str, ...] | None, ErrorResponseDTO | None]:
    """MCP arguments에서 language_filter를 파싱한다."""
    return parse_string_list(arguments=arguments, key="language_filter")


def _quality_error(exc: QualityError) -> dict[str, object]:
    """품질 예외를 pack1 오류 응답으로 변환한다."""
    return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))


class PipelineQualityRunTool:
    """pipeline_quality_run MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, quality_service: PipelineQualityService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._quality_service = quality_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """품질 실행 결과를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])
        limit_raw = arguments.get("limit_files", 2_000)
        profile_raw = arguments.get("profile", "default")
        if not isinstance(limit_raw, int):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT_FILES", message="limit_files must be integer"))
        if not isinstance(profile_raw, str) or profile_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_PROFILE", message="profile must be non-empty string"))
        language_filter, parse_error = _parse_language_filter(arguments)
        if parse_error is not None:
            return pack1_error(parse_error)
        try:
            summary = self._quality_service.run(
                repo_root=repo,
                limit_files=limit_raw,
                profile=profile_raw,
                language_filter=language_filter,
            )
        except QualityError as exc:
            return _quality_error(exc)
        return pack1_success(
            {
                "items": [summary],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineQualityReportTool:
    """pipeline_quality_report MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, quality_service: PipelineQualityService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._quality_service = quality_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """최신 품질 리포트를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])
        try:
            summary = self._quality_service.get_latest_report(repo_root=repo)
        except QualityError as exc:
            return _quality_error(exc)
        return pack1_success(
            {
                "items": [summary],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )
