"""심볼/콜그래프 MCP 도구 구현."""

from __future__ import annotations

from sari.core.models import ErrorResponseDTO
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.tool_common import pack1_items_success, resolve_symbol_key


class ListSymbolsTool:
    """list_symbols MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """심볼 목록 조회 결과를 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        query_raw = arguments.get("query", "")
        query = query_raw.strip() if isinstance(query_raw, str) else ""
        limit_raw = arguments.get("limit", 50)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._lsp_repo.search_symbols(repo_root=str(arguments["repo"]), query=query, limit=limit_raw, path_prefix=None)
        return pack1_items_success([row.to_dict() for row in rows], cache_hit=True)


class ReadSymbolTool:
    """read_symbol MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """심볼 상세 조회 결과를 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        symbol_key = resolve_symbol_key(arguments)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="name/symbol_id/sid is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        path_raw = arguments.get("path")
        path_prefix = path_raw if isinstance(path_raw, str) and path_raw.strip() != "" else None
        rows = self._lsp_repo.search_symbols(
            repo_root=str(arguments["repo"]),
            query=symbol_key,
            limit=limit_raw,
            path_prefix=path_prefix,
        )
        return pack1_items_success([row.to_dict() for row in rows], cache_hit=True)


class GetImplementationsTool:
    """get_implementations MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """구현 후보 심볼 목록을 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        symbol_key = resolve_symbol_key(arguments)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._lsp_repo.find_implementations(repo_root=str(arguments["repo"]), symbol_name=symbol_key, limit=limit_raw)
        return pack1_items_success([row.to_dict() for row in rows], cache_hit=True)


class CallGraphTool:
    """call_graph MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """호출 그래프 요약(호출자/피호출자)을 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        symbol_key = resolve_symbol_key(arguments)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required"))
        limit_raw = arguments.get("limit", 50)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        repo_root = str(arguments["repo"])
        callers = [row.to_dict() for row in self._lsp_repo.find_callers(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw)]
        callees = [row.to_dict() for row in self._lsp_repo.find_callees(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw)]
        return pack1_items_success(
            [
                {
                    "symbol": symbol_key,
                    "callers": callers,
                    "callees": callees,
                    "caller_count": len(callers),
                    "callee_count": len(callees),
                }
            ],
            cache_hit=True,
        )


class CallGraphHealthTool:
    """call_graph_health MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """호출 그래프 건강 지표를 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        repo_root = str(arguments["repo"])
        health = self._lsp_repo.get_repo_call_graph_health(repo_root=repo_root)
        return pack1_items_success([{"repo": repo_root, **health}], cache_hit=True)

