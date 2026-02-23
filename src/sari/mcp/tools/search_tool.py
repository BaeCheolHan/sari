"""MCP search 도구를 구현한다."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from sari.core.models import ErrorResponseDTO
from sari.core.repo_context_resolver import resolve_repo_context
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.stabilization.reason_codes import ReasonCode
from sari.mcp.stabilization.session_state import record_search_metrics
from sari.mcp.stabilization.warning_sink import warn
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.search.orchestrator import SearchOrchestrator


class SearchTool:
    """pack1 계약 기반 search 도구를 제공한다."""

    def __init__(
        self,
        orchestrator: SearchOrchestrator,
        workspace_repo: WorkspaceRepository | None = None,
        tool_layer_repo: ToolDataLayerRepository | None = None,
        metrics_provider: Callable[[], object] | None = None,
        repo_registry_repo: RepoRegistryRepository | None = None,
        stabilization_enabled: bool = True,
        include_info_default: bool = False,
        symbol_info_budget_sec_default: float = 10.0,
        resolve_symbols_default_provider: Callable[[], bool] | None = None,
    ) -> None:
        """검색 오케스트레이터를 주입한다."""
        self._orchestrator = orchestrator
        self._workspace_repo = workspace_repo
        self._tool_layer_repo = tool_layer_repo
        self._metrics_provider = metrics_provider
        self._repo_registry_repo = repo_registry_repo
        self._stabilization_enabled = stabilization_enabled
        self._include_info_default = bool(include_info_default)
        self._symbol_info_budget_sec_default = max(0.0, float(symbol_info_budget_sec_default))
        self._resolve_symbols_default_provider = resolve_symbols_default_provider

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """도구 입력을 검증하고 pack1 결과를 반환한다."""
        repo = arguments.get("repo")
        query = arguments.get("query")
        limit = arguments.get("limit", 20)
        include_info_raw = arguments.get("include_info", None)
        symbol_info_budget_raw = arguments.get("symbol_info_budget_sec", None)
        resolve_symbols_raw = arguments.get("resolve_symbols", None)

        if not isinstance(repo, str) or repo.strip() == "":
            return pack1_error(
                ErrorResponseDTO(code="ERR_REPO_REQUIRED", message="repo is required"),
                recovery_hint="search 호출 시 repo 파라미터를 반드시 제공해야 합니다.",
            )
        repo_id: str | None = None
        if self._workspace_repo is not None:
            context, context_error = resolve_repo_context(
                raw_repo=repo.strip(),
                workspace_repo=self._workspace_repo,
                repo_registry_repo=self._repo_registry_repo,
                allow_absolute_input=True,
            )
            if context_error is not None:
                return pack1_error(context_error)
            assert context is not None
            repo = context.repo_root
            repo_id = context.repo_id
        if not isinstance(query, str) or query.strip() == "":
            return pack1_error(
                ErrorResponseDTO(code="ERR_QUERY_REQUIRED", message="query is required"),
                recovery_hint="search 호출 시 query 파라미터를 반드시 제공해야 합니다.",
            )
        if not isinstance(limit, int) or limit <= 0:
            return pack1_error(
                ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"),
                recovery_hint="limit은 1 이상의 정수여야 합니다.",
            )
        if include_info_raw is not None and not isinstance(include_info_raw, bool):
            return pack1_error(
                ErrorResponseDTO(code="ERR_INVALID_INCLUDE_INFO", message="include_info must be boolean"),
                recovery_hint="include_info는 true/false 불리언이어야 합니다.",
            )
        if symbol_info_budget_raw is not None and not isinstance(symbol_info_budget_raw, (int, float)):
            return pack1_error(
                ErrorResponseDTO(code="ERR_INVALID_SYMBOL_INFO_BUDGET", message="symbol_info_budget_sec must be number"),
                recovery_hint="symbol_info_budget_sec는 0 이상의 숫자여야 합니다.",
            )
        if resolve_symbols_raw is not None and not isinstance(resolve_symbols_raw, bool):
            return pack1_error(
                ErrorResponseDTO(code="ERR_INVALID_RESOLVE_SYMBOLS", message="resolve_symbols must be boolean"),
                recovery_hint="resolve_symbols는 true/false 불리언이어야 합니다.",
            )
        include_info = self._include_info_default if include_info_raw is None else bool(include_info_raw)
        default_resolve_symbols = False
        if self._resolve_symbols_default_provider is not None:
            try:
                default_resolve_symbols = bool(self._resolve_symbols_default_provider())
            except (RuntimeError, OSError, ValueError, TypeError):
                default_resolve_symbols = False
        resolve_symbols = default_resolve_symbols if resolve_symbols_raw is None else bool(resolve_symbols_raw)
        symbol_info_budget_sec = (
            self._symbol_info_budget_sec_default
            if symbol_info_budget_raw is None
            else max(0.0, float(symbol_info_budget_raw))
        )

        try:
            result = self._orchestrator.search(
                query=query,
                limit=limit,
                repo_root=repo,
                repo_id=repo_id,
                resolve_symbols=resolve_symbols,
                include_info=include_info,
                symbol_info_budget_sec=symbol_info_budget_sec,
            )
        except TypeError:
            # 이전 시그니처 호환 경로
            result = self._orchestrator.search(query=query, limit=limit, repo_root=repo)
        stabilization_meta = _build_search_stabilization(
            arguments=arguments,
            repo=repo,
            query=query,
            items=result.items,
            degraded=result.meta.degraded,
            fatal_error=result.meta.fatal_error,
            stabilization_enabled=self._stabilization_enabled,
            errors=[error.to_dict() for error in result.meta.errors],
        )
        progress_meta = self._build_progress_meta()
        if result.meta.fatal_error:
            first_error = result.meta.errors[0]
            recovery_hint = _resolve_recovery_hint(first_error.code)
            return pack1_error(
                ErrorResponseDTO(code=first_error.code, message=first_error.message),
                detailed_errors=[error.to_dict() for error in result.meta.errors],
                stabilization=stabilization_meta,
                recovery_hint=recovery_hint,
            )
        meta_payload = Pack1MetaDTO(
            candidate_count=result.meta.candidate_count,
            resolved_count=result.meta.resolved_count,
            cache_hit=None,
            errors=[err.to_dict() for err in result.meta.errors],
            stabilization=stabilization_meta,
        ).to_dict()
        meta_payload["lsp_query_mode"] = result.meta.lsp_query_mode
        meta_payload["lsp_sync_mode"] = result.meta.lsp_sync_mode
        meta_payload["lsp_fallback_used"] = result.meta.lsp_fallback_used
        meta_payload["lsp_fallback_reason"] = result.meta.lsp_fallback_reason
        meta_payload["lsp_include_info_requested"] = result.meta.include_info_requested
        meta_payload["lsp_symbol_info_budget_sec"] = result.meta.symbol_info_budget_sec
        meta_payload["lsp_symbol_info_requested_count"] = result.meta.symbol_info_requested_count
        meta_payload["lsp_symbol_info_budget_exceeded_count"] = result.meta.symbol_info_budget_exceeded_count
        meta_payload["lsp_symbol_info_skipped_count"] = result.meta.symbol_info_skipped_count
        meta_payload["ranking_version"] = result.meta.ranking_version
        meta_payload["ranking_components_enabled"] = (
            result.meta.ranking_components_enabled if result.meta.ranking_components_enabled is not None else {}
        )
        if progress_meta is not None:
            meta_payload["index_progress"] = progress_meta
        return pack1_success(
            {
                "items": [self._build_item_payload(item=item, repo_root=repo) for item in result.items],
                "meta": meta_payload,
            }
        )

    def _build_item_payload(self, *, item: object, repo_root: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": getattr(item, "item_type"),
            "repo": getattr(item, "repo"),
            "relative_path": getattr(item, "relative_path"),
            "score": getattr(item, "score"),
            "source": getattr(item, "source"),
            "name": getattr(item, "name"),
            "kind": getattr(item, "kind"),
            "symbol_info": getattr(item, "symbol_info"),
        }
        tool_layer_repo = self._tool_layer_repo
        workspace_repo = self._workspace_repo
        content_hash = getattr(item, "content_hash", None)
        if (
            tool_layer_repo is None
            or workspace_repo is None
            or not isinstance(content_hash, str)
            or content_hash.strip() == ""
        ):
            return payload
        relative_path = getattr(item, "relative_path", None)
        if not isinstance(relative_path, str) or relative_path.strip() == "":
            return payload
        workspace = workspace_repo.get_by_path(repo_root)
        if workspace is None:
            return payload
        snapshot = tool_layer_repo.load_effective_snapshot(
            workspace_id=workspace.path,
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
        )
        l4_snapshot = snapshot.get("l4")
        if isinstance(l4_snapshot, dict):
            payload["l4"] = l4_snapshot
        l5_snapshot = snapshot.get("l5", [])
        if isinstance(l5_snapshot, list) and len(l5_snapshot) > 0:
            payload["l5"] = l5_snapshot
        return payload

    def _build_progress_meta(self) -> dict[str, object] | None:
        """파이프라인 진행률 메타를 반환한다."""
        if self._metrics_provider is None:
            return None
        try:
            metrics = self._metrics_provider()
        except (RuntimeError, OSError, ValueError, TypeError):
            return None
        if not hasattr(metrics, "to_dict"):
            return None
        payload = metrics.to_dict()
        return {
            "progress_percent_l2": float(payload.get("progress_percent_l2", 0.0)),
            "progress_percent_l3": float(payload.get("progress_percent_l3", 0.0)),
            "eta_l2_sec": int(payload.get("eta_l2_sec", -1)),
            "eta_l3_sec": int(payload.get("eta_l3_sec", -1)),
            "remaining_jobs_l2": int(payload.get("remaining_jobs_l2", 0)),
            "remaining_jobs_l3": int(payload.get("remaining_jobs_l3", 0)),
            "worker_state": str(payload.get("worker_state", "unknown")),
        }


def _build_search_stabilization(
    arguments: dict[str, object],
    repo: str,
    query: str,
    items: list[object],
    degraded: bool,
    fatal_error: bool,
    stabilization_enabled: bool,
    errors: list[dict[str, object]],
) -> dict[str, object] | None:
    """search 응답용 stabilization 메타를 생성한다."""
    if not stabilization_enabled:
        return None
    top_paths = [str(getattr(item, "relative_path", "") or "") for item in items[:10]]
    candidates = _candidate_mapping(query=query, items=items)
    generated_bundle_id = _bundle_id(query=query, paths=top_paths)
    metrics_snapshot = record_search_metrics(
        arguments,
        [repo],
        preview_degraded=degraded,
        query=query,
        top_paths=top_paths,
        candidates=candidates,
        bundle_id=generated_bundle_id,
    )
    warnings: list[str] = []
    reason_codes: list[str] = []
    if degraded:
        warnings.append("Search completed with degraded backend state; inspect meta.errors.")
        reason_codes.append(ReasonCode.SEARCH_DEGRADED.value)
    if fatal_error:
        warnings.append("Search failed with fatal backend errors.")
        reason_codes.append(ReasonCode.SEARCH_FATAL.value)
    for error in errors:
        message = str(error.get("message", "")).strip()
        severity = str(error.get("severity", "")).strip().upper()
        code = str(error.get("code", "")).strip()
        if severity == "FATAL":
            warn(f"[search:fatal] {code}: {message}")
        elif message != "":
            warn(f"[search:degraded] {code}: {message}")
    return {
        "budget_state": "NORMAL",
        "suggested_next_action": "read" if len(items) > 0 else "search",
        "warnings": warnings,
        "reason_codes": reason_codes,
        "bundle_id": generated_bundle_id,
        "next_calls": _next_calls(items),
        "metrics_snapshot": metrics_snapshot,
        "degraded": degraded,
        "fatal_error": fatal_error,
    }


def _candidate_mapping(query: str, items: list[object]) -> dict[str, str]:
    """검색 결과에서 candidate_id 매핑을 생성한다."""
    mapping: dict[str, str] = {}
    for index, item in enumerate(items):
        relative_path = str(getattr(item, "relative_path", "") or "")
        name = str(getattr(item, "name", "") or "")
        raw = f"{query}|{relative_path}|{name}|{index}"
        candidate_key = "cand_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        mapping[candidate_key] = relative_path
    return mapping


def _bundle_id(query: str, paths: list[str]) -> str:
    """검색 응답 번들 식별자를 생성한다."""
    merged = "\n".join([query, *paths])
    return "bundle_" + hashlib.sha256(merged.encode("utf-8")).hexdigest()[:12]


def _next_calls(items: list[object]) -> list[dict[str, object]]:
    """다음 권장 호출 힌트를 생성한다."""
    calls: list[dict[str, object]] = []
    for item in items[:3]:
        item_type = str(getattr(item, "item_type", "") or "")
        relative_path = str(getattr(item, "relative_path", "") or "")
        name = str(getattr(item, "name", "") or "")
        if item_type == "symbol":
            calls.append({"tool": "read", "arguments": {"mode": "symbol", "target": name, "path": relative_path}})
        else:
            calls.append({"tool": "read", "arguments": {"mode": "file", "target": relative_path}})
    return calls


def _resolve_recovery_hint(error_code: str) -> str | None:
    """치명 오류 코드별 운영 복구 힌트를 생성한다."""
    if error_code == "ERR_LSP_UNAVAILABLE":
        return (
            "LSP 서버가 기동되지 않았습니다. sari doctor 및 pipeline lsp-matrix diagnose로 "
            "언어별 상태를 확인하고 필요한 서버를 설치하세요."
        )
    if error_code == "ERR_REPO_NOT_REGISTERED":
        return "해당 경로를 roots add로 등록한 뒤 다시 검색하세요."
    if error_code == "ERR_TANTIVY_LOCK_BUSY":
        return "현재 DB/인덱스를 다른 프로세스가 사용 중입니다. `sari mcp stdio`(proxy 기본 경로)로 연결하거나, 데몬을 단일 경로로 사용하세요."
    return None
