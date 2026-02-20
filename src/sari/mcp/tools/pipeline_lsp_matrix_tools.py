"""MCP LSP 매트릭스 도구를 제공한다."""

from __future__ import annotations

from sari.core.exceptions import DaemonError
from sari.core.models import ErrorResponseDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.arg_parser import parse_boolean, parse_string_list
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.pipeline_lsp_matrix_service import PipelineLspMatrixService


def _parse_required_languages(arguments: dict[str, object]) -> tuple[tuple[str, ...] | None, ErrorResponseDTO | None]:
    """MCP arguments에서 required_languages를 파싱한다."""
    parsed, parse_error = parse_string_list(arguments=arguments, key="required_languages")
    if parse_error is None:
        return parsed, None
    return None, ErrorResponseDTO(code="ERR_INVALID_REQUIRED_LANGUAGE", message=parse_error.message)


def _parse_fail_on_unavailable(arguments: dict[str, object]) -> tuple[bool, ErrorResponseDTO | None]:
    """MCP arguments에서 fail_on_unavailable 플래그를 파싱한다."""
    return parse_boolean(arguments=arguments, key="fail_on_unavailable", default=True)


def _parse_strict_all_languages(arguments: dict[str, object]) -> tuple[bool, ErrorResponseDTO | None]:
    """MCP arguments에서 strict_all_languages 플래그를 파싱한다."""
    return parse_boolean(arguments=arguments, key="strict_all_languages", default=True)


def _parse_strict_symbol_gate(arguments: dict[str, object]) -> tuple[bool, ErrorResponseDTO | None]:
    """MCP arguments에서 strict_symbol_gate 플래그를 파싱한다."""
    return parse_boolean(arguments=arguments, key="strict_symbol_gate", default=True)


def _matrix_error(exc: DaemonError) -> dict[str, object]:
    """LSP 매트릭스 예외를 pack1 오류 응답으로 변환한다."""
    return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))


class PipelineLspMatrixRunTool:
    """pipeline_lsp_matrix_run MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, matrix_service: PipelineLspMatrixService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._matrix_service = matrix_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """LSP 매트릭스 실행 결과를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])
        required_languages, required_error = _parse_required_languages(arguments)
        if required_error is not None:
            return pack1_error(required_error)
        fail_on_unavailable, fail_error = _parse_fail_on_unavailable(arguments)
        if fail_error is not None:
            return pack1_error(fail_error)
        strict_all_languages, strict_error = _parse_strict_all_languages(arguments)
        if strict_error is not None:
            return pack1_error(strict_error)
        strict_symbol_gate, strict_symbol_gate_error = _parse_strict_symbol_gate(arguments)
        if strict_symbol_gate_error is not None:
            return pack1_error(strict_symbol_gate_error)
        try:
            result = self._matrix_service.run(
                repo_root=repo,
                required_languages=required_languages,
                fail_on_unavailable=fail_on_unavailable,
                strict_all_languages=strict_all_languages,
                strict_symbol_gate=strict_symbol_gate,
            )
        except DaemonError as exc:
            return _matrix_error(exc)
        return pack1_success(
            {
                "items": [result],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineLspMatrixReportTool:
    """pipeline_lsp_matrix_report MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, matrix_service: PipelineLspMatrixService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._matrix_service = matrix_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """최신 LSP 매트릭스 리포트를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])
        try:
            result = self._matrix_service.get_latest_report(repo_root=repo)
        except DaemonError as exc:
            return _matrix_error(exc)
        return pack1_success(
            {
                "items": [result],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )
