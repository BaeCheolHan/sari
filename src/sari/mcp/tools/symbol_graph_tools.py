"""심볼/콜그래프 MCP 도구 구현."""

from __future__ import annotations

from sari.core.models import ErrorResponseDTO, SymbolSearchItemDTO
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.mcp.tools.admin_tools import RepoValidationPort, validate_repo_argument
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.row_mapper import rows_to_items
from sari.mcp.tools.tool_common import pack1_items_success, resolve_symbol_key
from sari.semantic.python_protocol_implementations import is_excluded_candidate_path, scan_python_protocol_implementations


class ListSymbolsTool:
    """list_symbols MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """심볼 목록 조회 결과를 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        query_raw = arguments.get("query", "")
        query = query_raw.strip() if isinstance(query_raw, str) else ""
        limit_raw = arguments.get("limit", 50)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._lsp_repo.search_symbols(repo_root=str(arguments["repo"]), query=query, limit=limit_raw, path_prefix=None)
        return pack1_items_success(rows_to_items(rows), cache_hit=True, warnings=warnings_payload)


class ReadSymbolTool:
    """read_symbol MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """심볼 상세 조회 결과를 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
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
        return pack1_items_success(rows_to_items(rows), cache_hit=True, warnings=warnings_payload)


class GetImplementationsTool:
    """get_implementations MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """구현 후보 심볼 목록을 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        symbol_key = resolve_symbol_key(arguments)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        repo_root = str(arguments["repo"])
        query_name = symbol_key
        if _looks_like_symbol_key(symbol_key):
            resolved_name = self._lsp_repo.resolve_symbol_name_from_key(repo_root=repo_root, symbol_key=symbol_key)
            if resolved_name is not None:
                query_name = resolved_name
        rows = self._lsp_repo.find_implementations(repo_root=repo_root, symbol_name=query_name, limit=limit_raw)
        rows = _filter_implementation_candidates(rows=rows, symbol_key=query_name, limit=limit_raw)
        if len(rows) == 0:
            rows = scan_python_protocol_implementations(repo_root=repo_root, symbol_name=query_name, limit=limit_raw)
        return pack1_items_success(rows_to_items(rows), cache_hit=True, warnings=warnings_payload)


class CallGraphTool:
    """call_graph MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """호출 그래프 요약(호출자/피호출자)을 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        symbol_key = resolve_symbol_key(arguments)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required"))
        limit_raw = arguments.get("limit", 50)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        repo_root = str(arguments["repo"])
        callers = rows_to_items(self._lsp_repo.find_callers(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw))
        if len(callers) == 0:
            callers = rows_to_items(
                self._lsp_repo.find_python_semantic_callers(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw)
        )
        callees = rows_to_items(self._lsp_repo.find_callees(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw))
        health = self._lsp_repo.get_repo_call_graph_health(repo_root=repo_root)
        relation_data_ready = int(health.get("relation_count", 0)) > 0
        caller_evidence_types = sorted(
            {
                str(item.get("evidence_type", "")).strip()
                for item in callers
                if str(item.get("evidence_type", "")).strip() != ""
            }
        )
        caller_confidences = [
            float(item["confidence"])
            for item in callers
            if isinstance(item.get("confidence"), (int, float))
        ]
        semantic_callers_used = len(caller_evidence_types) > 0
        effective_relation_data_ready = relation_data_ready or semantic_callers_used
        if not effective_relation_data_ready:
            warnings_payload.append(
                {
                    "code": "WARN_CALL_GRAPH_RELATIONS_NOT_READY",
                    "message": "call relations index is empty; run L5 relation extraction pipeline",
                }
            )
        return pack1_items_success(
            [
                {
                    "kind": "record",
                    "path": repo_root,
                    "name": symbol_key,
                    "symbol": symbol_key,
                    "callers": callers,
                    "callees": callees,
                    "caller_count": len(callers),
                    "callee_count": len(callees),
                    "relation_data_ready": effective_relation_data_ready,
                    "semantic_callers_used": semantic_callers_used,
                    "caller_evidence_types": caller_evidence_types,
                    "max_caller_confidence": max(caller_confidences) if len(caller_confidences) > 0 else None,
                }
            ],
            cache_hit=True,
            warnings=warnings_payload,
        )


class CallGraphHealthTool:
    """call_graph_health MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, lsp_repo: LspToolDataRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._lsp_repo = lsp_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """호출 그래프 건강 지표를 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo_root = str(arguments["repo"])
        health = self._lsp_repo.get_repo_call_graph_health(repo_root=repo_root)
        return pack1_items_success([{"repo": repo_root, **health}], cache_hit=True, warnings=warnings_payload)


def _filter_implementation_candidates(
    *,
    rows: list[SymbolSearchItemDTO],
    symbol_key: str,
    limit: int,
) -> list[SymbolSearchItemDTO]:
    filtered = [
        row
        for row in rows
        if row.name != symbol_key and not is_excluded_candidate_path(row.relative_path)
    ]
    return filtered[:limit]


def _looks_like_symbol_key(value: str) -> bool:
    normalized = value.strip()
    if normalized == "":
        return False
    return "://" in normalized or "#" in normalized or "::" in normalized
