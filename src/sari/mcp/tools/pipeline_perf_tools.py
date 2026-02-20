"""MCP 파이프라인 성능 도구를 제공한다."""

from __future__ import annotations

from sari.core.exceptions import PerfError
from sari.core.models import ErrorResponseDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.pipeline_perf_service import PipelinePerfService


def _perf_error(exc: PerfError) -> dict[str, object]:
    """성능 실측 예외를 pack1 오류 응답으로 변환한다."""
    return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))


class PipelinePerfRunTool:
    """pipeline_perf_run MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, perf_service: PipelinePerfService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._perf_service = perf_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """성능 실측 결과를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])
        target_raw = arguments.get("target_files", 2000)
        profile_raw = arguments.get("profile", "realistic_v1")
        dataset_mode_raw = arguments.get("dataset_mode", "isolated")
        if not isinstance(target_raw, int):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_TARGET_FILES", message="target_files must be integer"))
        if not isinstance(profile_raw, str) or profile_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_PROFILE", message="profile must be non-empty string"))
        if not isinstance(dataset_mode_raw, str):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_DATASET_MODE", message="dataset_mode must be string"))
        dataset_mode = dataset_mode_raw.strip().lower()
        if dataset_mode not in ("isolated", "legacy"):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_DATASET_MODE", message="dataset_mode must be isolated or legacy"))
        try:
            summary = self._perf_service.run(repo_root=repo, target_files=target_raw, profile=profile_raw, dataset_mode=dataset_mode)
        except PerfError as exc:
            return _perf_error(exc)
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


class PipelinePerfReportTool:
    """pipeline_perf_report MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, perf_service: PipelinePerfService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._perf_service = perf_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """최신 성능 실측 결과를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        try:
            summary = self._perf_service.get_latest_report()
        except PerfError as exc:
            return _perf_error(exc)
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
