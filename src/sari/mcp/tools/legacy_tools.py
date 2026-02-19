"""레거시 MCP 도구 구현."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from sari.core.exceptions import CollectionError
from sari.core.language_registry import get_enabled_language_names
from sari.core.models import (
    ErrorResponseDTO,
    KnowledgeEntryDTO,
    LanguageProbeStatusDTO,
    SnippetSaveDTO,
    now_iso8601_utc,
)
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.knowledge_repository import KnowledgeRepository
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.stabilization.aggregation import add_read_to_bundle
from sari.mcp.stabilization.budget_guard import apply_soft_limits, evaluate_budget_state
from sari.mcp.stabilization.reason_codes import ReasonCode
from sari.mcp.stabilization.relevance_guard import assess_relevance
from sari.mcp.stabilization.session_state import (
    get_metrics_snapshot,
    get_search_context,
    get_session_key,
    record_read_metrics,
    requires_strict_session_id,
)
from sari.mcp.tools.arg_normalizer import ARG_META_KEY
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.collection.ports import CollectionScanPort
def _success(
    items: list[dict[str, object]],
    *,
    cache_hit: bool = False,
    stabilization: dict[str, object] | None = None,
) -> dict[str, object]:
    """pack1 success 응답을 생성한다."""
    return pack1_success(
        {
            "items": items,
            "meta": Pack1MetaDTO(
                candidate_count=len(items),
                resolved_count=len(items),
                cache_hit=cache_hit,
                errors=[],
                stabilization=stabilization,
            ).to_dict(),
        }
    )
def _resolve_symbol_key(arguments: dict[str, object]) -> str | None:
    """심볼 키 입력(symbol/symbol_id/sid)을 단일 문자열로 변환한다."""
    for key in ("symbol", "symbol_id", "sid", "name", "target"):
        raw = arguments.get(key)
        if isinstance(raw, str) and raw.strip() != "":
            return raw.strip()
    return None
def _resolve_source_path(repo_root: str, raw_path: str) -> Path:
    """입력 path를 저장소 기준 절대 경로로 변환한다."""
    source = Path(raw_path).expanduser()
    if source.is_absolute():
        return source
    return (Path(repo_root) / raw_path).resolve()
def _normalize_source_path(repo_root: str, source_path: Path) -> str:
    """소스 경로를 저장소 기준 상대경로 우선으로 정규화한다."""
    try:
        return str(source_path.resolve().relative_to(Path(repo_root).resolve()))
    except ValueError:
        return str(source_path.resolve())
def _stabilization_enabled() -> bool:
    """stabilization 활성 여부를 반환한다."""
    raw_value = os.getenv("SARI_STABILIZATION_ENABLED", "1").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _argument_error(
    *,
    code: str,
    message: str,
    arguments: dict[str, object],
    expected: list[str],
    example: dict[str, object],
) -> dict[str, object]:
    """자기설명형 인자 오류 응답을 생성한다."""
    received_keys, normalized_from = _extract_arg_meta(arguments)
    return pack1_error(
        ErrorResponseDTO(code=code, message=message),
        expected=expected,
        received=received_keys,
        example=example,
        normalized_from=normalized_from,
    )


def _extract_arg_meta(arguments: dict[str, object]) -> tuple[list[str], dict[str, str]]:
    """정규화 메타를 추출한다."""
    raw_meta = arguments.get(ARG_META_KEY)
    if not isinstance(raw_meta, dict):
        return ([], {})
    received_raw = raw_meta.get("received_keys")
    normalized_raw = raw_meta.get("normalized_from")
    received_keys: list[str] = []
    normalized_from: dict[str, str] = {}
    if isinstance(received_raw, list):
        received_keys = [str(item) for item in received_raw]
    if isinstance(normalized_raw, dict):
        normalized_from = {str(key): str(value) for key, value in normalized_raw.items()}
    return (received_keys, normalized_from)
def _content_hash(text: str) -> str:
    """텍스트 본문 해시를 생성한다."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
class SariGuideTool:
    """sari_guide MCP 도구를 처리한다."""
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """가이드 텍스트를 pack1 형식으로 반환한다."""
        del arguments
        return _success(
            [
                {
                    "name": "sari_guide",
                    "summary": "최소 호출 흐름: search -> read(file) -> search_symbol",
                    "quick_start": [
                        {"tool": "search", "arguments": {"repo_id": "sari", "query": "AuthService", "limit": 5}},
                        {"tool": "read", "arguments": {"repo_id": "sari", "mode": "file", "target": "README.md", "limit": 40}},
                        {"tool": "search_symbol", "arguments": {"repo_id": "sari", "query": "Auth", "limit": 10}},
                    ],
                    "alias_map": {
                        "repo": ["repo_id"],
                        "read.target": ["path", "file_path", "relative_path"],
                        "search.query": ["q", "keyword"],
                        "search_symbol.path_prefix": ["path"],
                        "read.mode": {"file_preview": "file", "preview": "diff_preview"},
                    },
                }
            ]
        )
class StatusTool:
    """status MCP 도구를 처리한다."""
    def __init__(
        self,
        workspace_repo: WorkspaceRepository,
        runtime_repo: RuntimeRepository,
        file_repo: FileCollectionRepository,
        lsp_repo: LspToolDataRepository,
        language_probe_repo: LanguageProbeRepository | None = None,
    ) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._runtime_repo = runtime_repo
        self._file_repo = file_repo
        self._lsp_repo = lsp_repo
        self._language_probe_repo = language_probe_repo
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장소 단위 상태 요약을 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        repo_root = str(arguments["repo"])
        runtime = self._runtime_repo.get_runtime()
        repo_stats = self._file_repo.get_repo_stats()
        file_count = 0
        for stat in repo_stats:
            if str(stat.get("repo", "")) == repo_root:
                file_count = int(stat.get("file_count", 0))
                break
        graph_health = self._lsp_repo.get_repo_call_graph_health(repo_root=repo_root)
        language_support = _build_language_support_payload(self._language_probe_repo)
        return _success(
            [
                {
                    "repo": repo_root,
                    "daemon_state": None if runtime is None else runtime.state,
                    "file_count": file_count,
                    "symbol_count": graph_health["symbol_count"],
                    "relation_count": graph_health["relation_count"],
                    "orphan_relation_count": graph_health["orphan_relation_count"],
                    "language_support": language_support,
                }
            ]
        )
def _build_language_support_payload(language_probe_repo: LanguageProbeRepository | None) -> dict[str, object]:
    """언어 지원 상태 페이로드를 구성한다."""
    enabled_languages = list(get_enabled_language_names())
    snapshots: dict[str, LanguageProbeStatusDTO] = {}
    if language_probe_repo is not None:
        for item in language_probe_repo.list_all():
            snapshots[item.language] = item
    languages: list[dict[str, object]] = []
    for language in enabled_languages:
        snapshot = snapshots.get(language)
        if snapshot is None:
            languages.append(
                {
                    "language": language,
                    "enabled": True,
                    "available": False,
                    "last_probe_at": None,
                    "last_error_code": None,
                    "last_error_message": None,
                    "symbol_extract_success": False,
                    "document_symbol_count": 0,
                    "path_mapping_ok": False,
                    "timeout_occurred": False,
                    "recovered_by_restart": False,
                }
            )
            continue
        languages.append(
            {
                "language": snapshot.language,
                "enabled": snapshot.enabled,
                "available": snapshot.available,
                "last_probe_at": snapshot.last_probe_at,
                "last_error_code": snapshot.last_error_code,
                "last_error_message": snapshot.last_error_message,
                "symbol_extract_success": snapshot.symbol_extract_success,
                "document_symbol_count": snapshot.document_symbol_count,
                "path_mapping_ok": snapshot.path_mapping_ok,
                "timeout_occurred": snapshot.timeout_occurred,
                "recovered_by_restart": snapshot.recovered_by_restart,
            }
        )
    available_count = len([item for item in languages if bool(item["available"])])
    return {
        "enabled": enabled_languages,
        "enabled_count": len(enabled_languages),
        "available_count": available_count,
        "active_last_5m": [],
        "languages": languages,
    }
class ReadTool:
    """read MCP unified 도구를 처리한다."""
    def __init__(
        self,
        workspace_repo: WorkspaceRepository,
        file_collection_service: CollectionScanPort,
        lsp_repo: LspToolDataRepository,
        knowledge_repo: KnowledgeRepository,
    ) -> None:
        """필요 저장소/서비스 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._file_collection_service = file_collection_service
        self._lsp_repo = lsp_repo
        self._knowledge_repo = knowledge_repo
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """모드별 read 응답을 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        stabilization_enabled = _stabilization_enabled()
        if stabilization_enabled and requires_strict_session_id(arguments):
            return pack1_error(
                ErrorResponseDTO(code="ERR_SESSION_ID_REQUIRED", message="session_id is required by strict session policy."),
                stabilization={
                    "budget_state": "NORMAL",
                    "suggested_next_action": "read",
                    "warnings": ["Provide session_id or disable strict mode."],
                    "reason_codes": [ReasonCode.SESSION_ID_REQUIRED.value],
                    "next_calls": [],
                    "metrics_snapshot": get_metrics_snapshot(arguments, [str(arguments["repo"])]),
                },
            )
        repo_root = str(arguments["repo"])
        if stabilization_enabled:
            pre_metrics = get_metrics_snapshot(arguments, [repo_root])
            budget_state, budget_warnings, suggested_next_action = evaluate_budget_state(pre_metrics)
            if budget_state == "HARD_LIMIT":
                return pack1_error(
                    ErrorResponseDTO(code="ERR_BUDGET_HARD_LIMIT", message="read budget exceeded. use search first."),
                    stabilization={
                        "budget_state": budget_state,
                        "suggested_next_action": suggested_next_action or "search",
                        "warnings": budget_warnings,
                        "reason_codes": [ReasonCode.BUDGET_HARD_LIMIT.value],
                        "next_calls": [{"tool": "search", "arguments": {"query": "target", "limit": 5}}],
                        "metrics_snapshot": pre_metrics,
                    },
                )
        mode_raw = arguments.get("mode", "file")
        if not isinstance(mode_raw, str) or mode_raw.strip() == "":
            return _argument_error(
                code="ERR_MODE_REQUIRED",
                message="mode is required",
                arguments=arguments,
                expected=["mode"],
                example={"repo": repo_root, "mode": "file", "target": "README.md"},
            )
        mode = mode_raw.strip().lower()
        if mode == "ast_edit":
            return pack1_error(ErrorResponseDTO(code="ERR_AST_DISABLED", message="ast_edit mode is disabled by policy"))
        if mode == "file":
            return self._read_file_mode(repo_root=repo_root, arguments=arguments)
        if mode == "symbol":
            return self._read_symbol_mode(repo_root=repo_root, arguments=arguments)
        if mode == "snippet":
            return self._read_snippet_mode(repo_root=repo_root, arguments=arguments)
        if mode == "diff_preview":
            return self._read_diff_preview_mode(repo_root=repo_root, arguments=arguments)
        return _argument_error(
            code="ERR_UNSUPPORTED_MODE",
            message=f"unsupported mode: {mode}",
            arguments=arguments,
            expected=["file", "symbol", "snippet", "diff_preview"],
            example={"repo": repo_root, "mode": "file", "target": "README.md"},
        )
    def _build_stabilization_meta(
        self,
        arguments: dict[str, object],
        repo_root: str,
        mode: str,
        target: str,
        content_text: str,
        read_lines: int,
        read_span: int,
        warnings: list[str],
        degraded: bool,
    ) -> dict[str, object] | None:
        """read 성공 응답용 stabilization 메타를 생성한다."""
        if not _stabilization_enabled():
            return None
        metrics_snapshot = record_read_metrics(
            arguments,
            [repo_root],
            read_lines=read_lines,
            read_chars=len(content_text),
            read_span=read_span,
        )
        budget_state, budget_warnings, suggested_next_action = evaluate_budget_state(metrics_snapshot)
        search_context = get_search_context(arguments, [repo_root])
        relevance_state, relevance_warnings, relevance_alternatives, relevance_suggested = assess_relevance(
            mode=mode,
            target=target,
            search_context=search_context,
        )
        session_key = get_session_key(arguments, [repo_root])
        bundle_info = add_read_to_bundle(
            session_key=session_key,
            mode=mode,
            path=target,
            text=content_text,
        )
        reason_codes: list[str] = []
        if degraded:
            reason_codes.append(ReasonCode.BUDGET_SOFT_LIMIT.value)
        if relevance_state == "LOW_RELEVANCE":
            reason_codes.append(ReasonCode.LOW_RELEVANCE_OUTSIDE_TOPK.value)
        all_warnings = [*warnings, *budget_warnings, *relevance_warnings]
        next_calls = self._next_calls_for_read(mode=mode, target=target, alternatives=relevance_alternatives)
        suggested = relevance_suggested or suggested_next_action or "read"
        return {
            "budget_state": budget_state,
            "suggested_next_action": suggested,
            "warnings": all_warnings,
            "reason_codes": reason_codes,
            "bundle_id": str(bundle_info.get("context_bundle_id") or ""),
            "next_calls": next_calls,
            "metrics_snapshot": metrics_snapshot,
            "evidence_refs": [
                {
                    "kind": mode,
                    "path": target,
                    "content_hash": _content_hash(content_text),
                }
            ],
        }
    def _next_calls_for_read(self, mode: str, target: str, alternatives: list[str]) -> list[dict[str, object]]:
        """read 응답의 다음 호출 힌트를 생성한다."""
        if mode == "symbol":
            return [{"tool": "search", "arguments": {"query": target, "limit": 5}}]
        if len(alternatives) > 0:
            return [{"tool": "read", "arguments": {"mode": "file", "target": alternatives[0]}}]
        return [{"tool": "search", "arguments": {"query": target, "limit": 5}}]
    def _read_file_mode(self, repo_root: str, arguments: dict[str, object]) -> dict[str, object]:
        """file 모드 읽기를 수행한다."""
        target = arguments.get("target")
        if not isinstance(target, str) or target.strip() == "":
            return _argument_error(
                code="ERR_TARGET_REQUIRED",
                message="target is required",
                arguments=arguments,
                expected=["target"],
                example={"repo": repo_root, "mode": "file", "target": "README.md"},
            )
        offset_raw = arguments.get("offset", 0)
        limit_raw = arguments.get("limit", 300)
        if not isinstance(offset_raw, int) or offset_raw < 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_OFFSET", message="offset must be non-negative integer"))
        if limit_raw is not None and (not isinstance(limit_raw, int) or limit_raw <= 0):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer or null"))
        delegated_args = {"offset": offset_raw, "limit": limit_raw}
        constrained_args, degraded, soft_warnings = apply_soft_limits(mode="file", delegated_args=delegated_args)
        constrained_offset = constrained_args.get("offset", offset_raw)
        constrained_limit = constrained_args.get("limit", limit_raw)
        if not isinstance(constrained_offset, int) or constrained_offset < 0:
            constrained_offset = offset_raw
        if constrained_limit is not None and not isinstance(constrained_limit, int):
            constrained_limit = limit_raw
        try:
            result = self._file_collection_service.read_file(
                repo_root=repo_root,
                relative_path=target.strip(),
                offset=constrained_offset,
                limit=constrained_limit if isinstance(constrained_limit, int) else None,
            )
        except CollectionError as exc:
            return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode="file",
            target=result.relative_path,
            content_text=result.content,
            read_lines=max(0, result.end_line - result.start_line + 1),
            read_span=max(0, result.end_line - result.start_line + 1),
            warnings=soft_warnings,
            degraded=degraded,
        )
        return _success(
            [
                {
                    "relative_path": result.relative_path,
                    "content": result.content,
                    "start_line": result.start_line,
                    "end_line": result.end_line,
                    "source": result.source,
                    "total_lines": result.total_lines,
                    "is_truncated": result.is_truncated,
                    "next_offset": result.next_offset,
                }
            ],
            cache_hit=result.source == "l2",
            stabilization=stabilization,
        )
    def _read_symbol_mode(self, repo_root: str, arguments: dict[str, object]) -> dict[str, object]:
        """symbol 모드 읽기를 수행한다."""
        target = arguments.get("target")
        if not isinstance(target, str) or target.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        path_raw = arguments.get("path")
        path_prefix = path_raw if isinstance(path_raw, str) and path_raw.strip() != "" else None
        rows = self._lsp_repo.search_symbols(repo_root=repo_root, query=target.strip(), limit=limit_raw, path_prefix=path_prefix)
        row_items = [row.to_dict() for row in rows]
        content_text = "\n".join(str(item.get("name", "")) for item in row_items)
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode="symbol",
            target=target.strip(),
            content_text=content_text,
            read_lines=len(row_items),
            read_span=len(row_items),
            warnings=[],
            degraded=False,
        )
        return _success(row_items, cache_hit=True, stabilization=stabilization)
    def _read_snippet_mode(self, repo_root: str, arguments: dict[str, object]) -> dict[str, object]:
        """snippet 모드 읽기를 수행한다."""
        target = arguments.get("target")
        query = None if not isinstance(target, str) else target.strip()
        if query is not None and query == "":
            query = None
        tag_raw = arguments.get("tag")
        tag = None if not isinstance(tag_raw, str) else tag_raw.strip()
        if tag == "":
            tag = None
        if tag is None and query is None:
            return pack1_error(ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target or tag is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        constrained_args, degraded, soft_warnings = apply_soft_limits(
            mode="snippet",
            delegated_args={"limit": limit_raw, "context_lines": arguments.get("context_lines")},
        )
        constrained_limit = constrained_args.get("limit", limit_raw)
        if not isinstance(constrained_limit, int) or constrained_limit <= 0:
            constrained_limit = limit_raw
        rows = self._knowledge_repo.query_snippets(repo_root=repo_root, tag=tag, query=query, limit=constrained_limit)
        row_items = [row.to_dict() for row in rows]
        content_text = "\n".join(str(item.get("content", "")) for item in row_items)
        target_value = query if query is not None else (tag if tag is not None else "")
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode="snippet",
            target=target_value,
            content_text=content_text,
            read_lines=len(row_items),
            read_span=len(row_items),
            warnings=soft_warnings,
            degraded=degraded,
        )
        return _success(row_items, cache_hit=True, stabilization=stabilization)
    def _read_diff_preview_mode(self, repo_root: str, arguments: dict[str, object]) -> dict[str, object]:
        """diff_preview 모드 읽기를 수행한다."""
        target = arguments.get("target")
        content = arguments.get("content")
        if not isinstance(target, str) or target.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target is required"))
        if not isinstance(content, str):
            return pack1_error(ErrorResponseDTO(code="ERR_CONTENT_REQUIRED", message="content is required"))
        against_raw = arguments.get("against", "WORKTREE")
        against = against_raw if isinstance(against_raw, str) and against_raw.strip() != "" else "WORKTREE"
        constrained_args, degraded, soft_warnings = apply_soft_limits(
            mode="diff_preview",
            delegated_args={"max_preview_chars": arguments.get("max_preview_chars")},
        )
        max_preview_chars = constrained_args.get("max_preview_chars")
        if not isinstance(max_preview_chars, int) or max_preview_chars <= 0:
            max_preview_chars = 12000
        source_path = _resolve_source_path(repo_root=repo_root, raw_path=target.strip())
        if not source_path.exists():
            return pack1_error(ErrorResponseDTO(code="ERR_FILE_NOT_FOUND", message="target file not found"))
        try:
            before_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return pack1_error(ErrorResponseDTO(code="ERR_TEXT_DECODE_FAILED", message="failed to read target file as utf-8"))
        preview_before = before_text[:max_preview_chars]
        preview_after = content[:max_preview_chars]
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode="diff_preview",
            target=_normalize_source_path(repo_root=repo_root, source_path=source_path),
            content_text=f"{preview_before}\n{preview_after}",
            read_lines=max(1, preview_before.count("\n") + preview_after.count("\n")),
            read_span=max(1, preview_before.count("\n") + 1),
            warnings=soft_warnings,
            degraded=degraded,
        )
        return _success(
            [
                {
                    "path": _normalize_source_path(repo_root=repo_root, source_path=source_path),
                    "against": against,
                    "before_chars": len(before_text),
                    "after_chars": len(content),
                    "preview_before": preview_before[:400],
                    "preview_after": preview_after[:400],
                }
            ],
            stabilization=stabilization,
        )
class DryRunDiffTool:
    """dry_run_diff MCP 도구를 처리한다."""
    def __init__(self, read_tool: ReadTool) -> None:
        """read 도구 의존성을 주입한다."""
        self._read_tool = read_tool
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """legacy dry_run_diff 입력을 read(diff_preview)로 위임한다."""
        target = arguments.get("path")
        transformed = dict(arguments)
        transformed["mode"] = "diff_preview"
        transformed["target"] = target
        return self._read_tool.call(transformed)
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
        return _success([row.to_dict() for row in rows], cache_hit=True)
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
        symbol_key = _resolve_symbol_key(arguments)
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
        return _success([row.to_dict() for row in rows], cache_hit=True)
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
        symbol_key = _resolve_symbol_key(arguments)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._lsp_repo.find_implementations(repo_root=str(arguments["repo"]), symbol_name=symbol_key, limit=limit_raw)
        return _success([row.to_dict() for row in rows], cache_hit=True)
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
        symbol_key = _resolve_symbol_key(arguments)
        if symbol_key is None:
            return pack1_error(ErrorResponseDTO(code="ERR_SYMBOL_REQUIRED", message="symbol or symbol_id is required"))
        limit_raw = arguments.get("limit", 50)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        repo_root = str(arguments["repo"])
        callers = [row.to_dict() for row in self._lsp_repo.find_callers(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw)]
        callees = [row.to_dict() for row in self._lsp_repo.find_callees(repo_root=repo_root, symbol_name=symbol_key, limit=limit_raw)]
        return _success(
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
        return _success([{"repo": repo_root, **health}], cache_hit=True)
class KnowledgeTool:
    """knowledge MCP 도구를 처리한다."""
    def __init__(self, workspace_repo: WorkspaceRepository, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """지식 엔트리 조회 결과를 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        query_raw = arguments.get("query")
        query = query_raw if isinstance(query_raw, str) else None
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._knowledge_repo.query_knowledge(repo_root=str(arguments["repo"]), kind="knowledge", query=query, limit=limit_raw)
        return _success([row.to_dict() for row in rows], cache_hit=True)
class SaveSnippetTool:
    """save_snippet MCP 도구를 처리한다."""
    def __init__(self, workspace_repo: WorkspaceRepository, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """파일 구간 스니펫을 저장한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        repo_root = str(arguments["repo"])
        path_raw = arguments.get("path")
        if not isinstance(path_raw, str) or path_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_PATH_REQUIRED", message="path is required"))
        start_line_raw = arguments.get("start_line")
        end_line_raw = arguments.get("end_line")
        if not isinstance(start_line_raw, int) or not isinstance(end_line_raw, int):
            return pack1_error(ErrorResponseDTO(code="ERR_LINE_RANGE_REQUIRED", message="start_line/end_line are required"))
        if start_line_raw <= 0 or end_line_raw < start_line_raw:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LINE_RANGE", message="invalid line range"))
        tag_raw = arguments.get("tag")
        if not isinstance(tag_raw, str) or tag_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_TAG_REQUIRED", message="tag is required"))
        source_path = _resolve_source_path(repo_root=repo_root, raw_path=path_raw.strip())
        if not source_path.exists() or not source_path.is_file():
            return pack1_error(ErrorResponseDTO(code="ERR_FILE_NOT_FOUND", message="source file not found"))
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return pack1_error(ErrorResponseDTO(code="ERR_TEXT_DECODE_FAILED", message="failed to read source file as utf-8"))
        if end_line_raw > len(lines):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LINE_RANGE", message="line range out of bound"))
        content = "\n".join(lines[start_line_raw - 1 : end_line_raw])
        snippet_id = self._knowledge_repo.save_snippet(
            SnippetSaveDTO(
                repo_root=repo_root,
                source_path=_normalize_source_path(repo_root=repo_root, source_path=source_path),
                start_line=start_line_raw,
                end_line=end_line_raw,
                tag=tag_raw.strip(),
                note=arguments.get("note") if isinstance(arguments.get("note"), str) else None,
                commit_hash=arguments.get("commit") if isinstance(arguments.get("commit"), str) else None,
                content_text=content,
                created_at=now_iso8601_utc(),
            )
        )
        return _success([{"snippet_id": snippet_id, "tag": tag_raw.strip()}])
class GetSnippetTool:
    """get_snippet MCP 도구를 처리한다."""
    def __init__(self, workspace_repo: WorkspaceRepository, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장된 스니펫을 조회한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        tag = arguments.get("tag") if isinstance(arguments.get("tag"), str) else None
        query = arguments.get("query") if isinstance(arguments.get("query"), str) else None
        if (tag is None or tag.strip() == "") and (query is None or query.strip() == ""):
            return pack1_error(ErrorResponseDTO(code="ERR_QUERY_REQUIRED", message="tag or query is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._knowledge_repo.query_snippets(
            repo_root=str(arguments["repo"]),
            tag=None if tag is None else tag.strip(),
            query=None if query is None else query.strip(),
            limit=limit_raw,
        )
        return _success([row.to_dict() for row in rows], cache_hit=True)
class ArchiveContextTool:
    """archive_context MCP 도구를 처리한다."""
    def __init__(self, workspace_repo: WorkspaceRepository, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """문맥 정보를 보존 저장한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        topic_raw = arguments.get("topic")
        content_raw = arguments.get("content")
        if not isinstance(topic_raw, str) or topic_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_TOPIC_REQUIRED", message="topic is required"))
        if not isinstance(content_raw, str) or content_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_CONTENT_REQUIRED", message="content is required"))
        tags_raw = arguments.get("tags")
        tags: list[str] = []
        if isinstance(tags_raw, list):
            for item in tags_raw:
                if isinstance(item, str) and item.strip() != "":
                    tags.append(item.strip())
        files_raw = arguments.get("related_files")
        related_files: list[str] = []
        if isinstance(files_raw, list):
            for item in files_raw:
                if isinstance(item, str) and item.strip() != "":
                    related_files.append(item.strip())
        entry_id = self._knowledge_repo.archive_knowledge(
            KnowledgeEntryDTO(
                kind="context",
                repo_root=str(arguments["repo"]),
                topic=topic_raw.strip(),
                content_text=content_raw,
                tags=tuple(tags),
                related_files=tuple(related_files),
                created_at=now_iso8601_utc(),
            )
        )
        return _success([{"entry_id": entry_id, "topic": topic_raw.strip()}])
class GetContextTool:
    """get_context MCP 도구를 처리한다."""
    def __init__(self, workspace_repo: WorkspaceRepository, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo
    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장된 문맥 엔트리를 조회한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        query = arguments.get("query") if isinstance(arguments.get("query"), str) else None
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._knowledge_repo.query_knowledge(repo_root=str(arguments["repo"]), kind="context", query=query, limit=limit_raw)
        return _success([row.to_dict() for row in rows], cache_hit=True)
