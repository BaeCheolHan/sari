"""MCP 심볼/호출자 도구(search_symbol/get_callers)를 제공한다."""

from __future__ import annotations

from sari.core.models import ErrorResponseDTO
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.arg_parser import parse_non_empty_string, parse_optional_string, parse_positive_int
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success


class SearchSymbolTool:
    """search_symbol MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, lsp_repo: LspToolDataRepository) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """심볼 인덱스 검색 결과를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]

        query_raw, query_error = parse_non_empty_string(arguments=arguments, key="query")
        if query_error is not None:
            return pack1_error(query_error)
        limit_raw, limit_error = parse_positive_int(arguments=arguments, key="limit", default=20)
        if limit_error is not None:
            return pack1_error(limit_error)
        path_prefix = parse_optional_string(arguments=arguments, key="path_prefix")

        repo = str(arguments["repo"])
        rows = self._lsp_repo.search_symbols(repo_root=repo, query=query_raw.strip(), limit=limit_raw, path_prefix=path_prefix)
        return pack1_success(
            {
                "items": [item.to_dict() for item in rows],
                "meta": Pack1MetaDTO(
                    candidate_count=len(rows),
                    resolved_count=len(rows),
                    cache_hit=True,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class GetCallersTool:
    """get_callers MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, lsp_repo: LspToolDataRepository) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """호출자 관계 조회 결과를 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]

        symbol_name = self._resolve_symbol_name(arguments)
        if symbol_name is None:
            return pack1_error(
                ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required")
            )

        limit_raw, limit_error = parse_positive_int(arguments=arguments, key="limit", default=50)
        if limit_error is not None:
            return pack1_error(limit_error)

        repo = str(arguments["repo"])
        rows = self._lsp_repo.find_callers(repo_root=repo, symbol_name=symbol_name, limit=limit_raw)
        return pack1_success(
            {
                "items": [item.to_dict() for item in rows],
                "meta": Pack1MetaDTO(
                    candidate_count=len(rows),
                    resolved_count=len(rows),
                    cache_hit=True,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )

    def _resolve_symbol_name(self, arguments: dict[str, object]) -> str | None:
        """symbol/symbol_id 입력에서 검색 키를 결정한다."""
        symbol_raw = arguments.get("symbol")
        if isinstance(symbol_raw, str) and symbol_raw.strip() != "":
            return symbol_raw.strip()
        symbol_id_raw = arguments.get("symbol_id")
        if isinstance(symbol_id_raw, str) and symbol_id_raw.strip() != "":
            return symbol_id_raw.strip()
        return None
