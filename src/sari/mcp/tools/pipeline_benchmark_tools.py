"""MCP 파이프라인 벤치마크 도구를 제공한다."""

from __future__ import annotations

from sari.core.exceptions import BenchmarkError
from sari.core.models import ErrorResponseDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.arg_parser import parse_boolean, parse_string_list
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.pipeline_benchmark_service import PipelineBenchmarkService


def _parse_language_filter(arguments: dict[str, object]) -> tuple[tuple[str, ...] | None, ErrorResponseDTO | None]:
    """MCP arguments에서 language_filter를 파싱한다."""
    return parse_string_list(arguments=arguments, key="language_filter")


def _parse_per_language_report(arguments: dict[str, object]) -> tuple[bool, ErrorResponseDTO | None]:
    """MCP arguments에서 per_language_report 플래그를 파싱한다."""
    return parse_boolean(arguments=arguments, key="per_language_report", default=False)


def _benchmark_error(exc: BenchmarkError) -> dict[str, object]:
    """벤치마크 예외를 pack1 오류 응답으로 변환한다."""
    return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))


class PipelineBenchmarkRunTool:
    """pipeline_benchmark_run MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, benchmark_service: PipelineBenchmarkService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._benchmark_service = benchmark_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """벤치마크 실행 결과를 pack1 형식으로 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        repo = str(arguments["repo"])
        target_raw = arguments.get("target_files", 50_000)
        profile_raw = arguments.get("profile", "default")
        if not isinstance(target_raw, int):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_TARGET_FILES", message="target_files must be integer"))
        if not isinstance(profile_raw, str) or profile_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_PROFILE", message="profile must be non-empty string"))
        language_filter, language_filter_error = _parse_language_filter(arguments)
        if language_filter_error is not None:
            return pack1_error(language_filter_error)
        per_language_report, per_language_report_error = _parse_per_language_report(arguments)
        if per_language_report_error is not None:
            return pack1_error(per_language_report_error)
        try:
            summary = self._benchmark_service.run(
                repo_root=repo,
                target_files=target_raw,
                profile=profile_raw,
                language_filter=language_filter,
                per_language_report=per_language_report,
            )
        except BenchmarkError as exc:
            return _benchmark_error(exc)
        return pack1_success(
            {
                "items": [summary],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                ).to_dict(),
            }
        )


class PipelineBenchmarkReportTool:
    """pipeline_benchmark_report MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, benchmark_service: PipelineBenchmarkService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._benchmark_service = benchmark_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """최신 벤치마크 결과를 pack1 형식으로 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        try:
            summary = self._benchmark_service.get_latest_report()
        except BenchmarkError as exc:
            return _benchmark_error(exc)
        return pack1_success(
            {
                "items": [summary],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                ).to_dict(),
            }
        )
