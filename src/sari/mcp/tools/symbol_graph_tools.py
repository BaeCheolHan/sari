"""심볼/콜그래프 MCP 도구 구현."""

from __future__ import annotations

import ast
from pathlib import Path

from sari.core.models import CallerEdgeDTO, ErrorResponseDTO, SymbolSearchItemDTO
from sari.core.models import now_iso8601_utc
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.mcp.tools.arg_parser import parse_optional_string
from sari.mcp.tools.admin_tools import RepoValidationPort, validate_repo_argument
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.row_mapper import rows_to_items
from sari.mcp.tools.tool_common import content_hash, pack1_items_success, resolve_symbol_key
from sari.semantic.python_call_edges import candidate_python_base_names
from sari.semantic.python_call_edges import classify_python_scope
from sari.semantic.python_call_edges import dotted_name
from sari.semantic.python_call_edges import extract_python_include_router_edges
from sari.semantic.python_call_edges import extract_python_semantic_call_edges
from sari.semantic.python_call_edges import scope_matches
from sari.semantic.python_call_edges import symbol_matches_target


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
        symbol_key = _resolve_graph_symbol_name(arguments=arguments, repo_root=str(arguments["repo"]), lsp_repo=self._lsp_repo)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._lsp_repo.find_implementations(
            repo_root=str(arguments["repo"]),
            symbol_name=symbol_key,
            limit=limit_raw,
            scope=_resolve_scope(arguments),
        )
        if len(rows) == 0:
            rows = _scan_python_protocol_implementations(
                repo_root=str(arguments["repo"]),
                symbol_name=symbol_key,
                limit=limit_raw,
                scope=_resolve_scope(arguments),
            )
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
        repo_root = str(arguments["repo"])
        scope = _resolve_scope(arguments)
        symbol_key = _resolve_graph_symbol_name(arguments=arguments, repo_root=repo_root, lsp_repo=self._lsp_repo, scope=scope)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required"))
        limit_raw = arguments.get("limit", 50)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        callers = rows_to_items(self._lsp_repo.find_callers(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw, scope=scope))
        if len(callers) == 0:
            callers = rows_to_items(
                scan_python_semantic_callers(
                    repo_root=repo_root,
                    symbol_name=symbol_key,
                    limit=limit_raw,
                    scope=scope,
                    lsp_repo=self._lsp_repo,
                )
            )
        callees = rows_to_items(self._lsp_repo.find_callees(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw, scope=scope))
        health = self._lsp_repo.get_repo_call_graph_health(repo_root=repo_root, scope=scope)
        relation_data_ready = int(health.get("relation_count", 0)) > 0
        if not relation_data_ready:
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
                    "relation_data_ready": relation_data_ready,
                    "confidence": 1.0,
                    "evidence_type": "exact_symbol_name",
                    "scope": "all" if scope == "all" else "production",
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
        health = self._lsp_repo.get_repo_call_graph_health(repo_root=repo_root, scope=_resolve_scope(arguments))
        return pack1_items_success([{"repo": repo_root, **health}], cache_hit=True, warnings=warnings_payload)


def _resolve_scope(arguments: dict[str, object]) -> str:
    raw = parse_optional_string(arguments=arguments, key="scope")
    if raw is None:
        return "production"
    normalized = raw.strip().lower()
    if normalized in {"all", "*", "tests", "test", "production", "prod"}:
        return normalized
    return "production"


def _resolve_graph_symbol_name(
    *,
    arguments: dict[str, object],
    repo_root: str,
    lsp_repo: LspToolDataRepository,
    scope: str = "production",
) -> str | None:
    raw_symbol = parse_optional_string(arguments=arguments, key="symbol")
    if raw_symbol is not None:
        resolved = lsp_repo.resolve_symbol_name(repo_root=repo_root, symbol_ref=raw_symbol.strip(), scope=scope)
        return resolved or raw_symbol.strip()
    raw_symbol_id = parse_optional_string(arguments=arguments, key="symbol_id")
    if raw_symbol_id is not None:
        return lsp_repo.resolve_symbol_name(repo_root=repo_root, symbol_ref=raw_symbol_id.strip(), scope=scope)
    raw_sid = parse_optional_string(arguments=arguments, key="sid")
    if raw_sid is not None:
        return lsp_repo.resolve_symbol_name(repo_root=repo_root, symbol_ref=raw_sid.strip(), scope=scope)
    return resolve_symbol_key(arguments)


def _scan_python_protocol_implementations(
    *,
    repo_root: str,
    symbol_name: str,
    limit: int,
    scope: str,
) -> list[SymbolSearchItemDTO]:
    root = Path(repo_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return []
    target_names = candidate_python_base_names(symbol_name)
    if len(target_names) == 0:
        return []
    results: list[SymbolSearchItemDTO] = []
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        relative_path = str(path.relative_to(root)).replace("\\", "/")
        path_scope = classify_python_scope(relative_path)
        if not scope_matches(path_scope=path_scope, scope=scope):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {dotted_name(base) for base in node.bases}
            if any(symbol_matches_target(candidate=base_name, target_names=target_names) for base_name in base_names):
                line = int(getattr(node, "lineno", 1))
                end_line = int(getattr(node, "end_lineno", line))
                results.append(
                    SymbolSearchItemDTO(
                        repo=str(root),
                        relative_path=relative_path,
                        name=node.name,
                        kind="Class",
                        line=line,
                        end_line=end_line,
                        content_hash=content_hash(source),
                        symbol_key=f"{relative_path}::{node.name}@{line}",
                        confidence=0.9,
                        evidence_type="python_protocol_base",
                        scope=path_scope,
                    )
                )
                if len(results) >= limit:
                    return results
    return results


def scan_python_semantic_callers(
    *,
    repo_root: str,
    symbol_name: str,
    limit: int,
    scope: str,
    lsp_repo: LspToolDataRepository | None = None,
) -> list[CallerEdgeDTO]:
    if lsp_repo is not None:
        persisted = lsp_repo.find_python_semantic_callers(repo_root=repo_root, symbol_name=symbol_name, limit=limit, scope=scope)
        if len(persisted) > 0:
            return persisted
    root = Path(repo_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return []
    target_names = candidate_python_base_names(symbol_name)
    if len(target_names) == 0:
        return []
    include_router_edges = _scan_python_include_router_callers(
        repo_root=str(root),
        target_names=target_names,
        scope=scope,
    )
    include_router_edges_by_path: dict[str, list[CallerEdgeDTO]] = {}
    for edge in include_router_edges:
        include_router_edges_by_path.setdefault(edge.relative_path, []).append(edge)
    results: list[CallerEdgeDTO] = []
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        relative_path = str(path.relative_to(root)).replace("\\", "/")
        path_scope = classify_python_scope(relative_path)
        if not scope_matches(path_scope=path_scope, scope=scope):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        file_edges = [
            edge
            for edge in extract_python_semantic_call_edges(
                repo_root=str(root),
                relative_path=relative_path,
                content_text=source,
            )
            if symbol_matches_target(candidate=edge.to_symbol, target_names=target_names)
        ]
        file_edges.extend(include_router_edges_by_path.get(relative_path, ()))
        if lsp_repo is not None and len(file_edges) > 0:
            lsp_repo.replace_python_semantic_call_edges(
                repo_root=str(root),
                relative_path=relative_path,
                content_hash=content_hash(source),
                edges=file_edges,
                created_at=now_iso8601_utc(),
            )
        results.extend(file_edges)
        if len(results) >= limit:
            break
    deduped: list[CallerEdgeDTO] = []
    seen: set[tuple[str, str, int, str]] = set()
    for item in results:
        key = (item.relative_path, item.from_symbol, item.line, item.to_symbol)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _scan_python_include_router_callers(
    *,
    repo_root: str,
    target_names: tuple[str, ...],
    scope: str,
) -> list[CallerEdgeDTO]:
    root = Path(repo_root)
    sources_by_path: dict[str, str] = {}
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        relative_path = str(path.relative_to(root)).replace("\\", "/")
        path_scope = classify_python_scope(relative_path)
        if not scope_matches(path_scope=path_scope, scope=scope):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        sources_by_path[relative_path] = source
    return [
        edge
        for edge in extract_python_include_router_edges(repo_root=repo_root, sources_by_path=sources_by_path, scope=scope)
        if symbol_matches_target(candidate=edge.to_symbol, target_names=target_names)
    ]
