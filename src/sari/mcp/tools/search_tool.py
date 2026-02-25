"""MCP search 도구를 구현한다."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sari.core.models import ErrorResponseDTO
from sari.core.repo_context_resolver import resolve_repo_context
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.mcp.tools.admin_tools import RepoValidationPort
from sari.mcp.stabilization.ports import StabilizationPort
from sari.mcp.stabilization.stabilization_service import StabilizationService
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.search.orchestrator import SearchOrchestrator

log = logging.getLogger(__name__)


class SearchTool:
    """pack1 계약 기반 search 도구를 제공한다."""

    def __init__(
        self,
        orchestrator: SearchOrchestrator,
        workspace_repo: RepoValidationPort | None = None,
        tool_layer_repo: ToolDataLayerRepository | None = None,
        metrics_provider: Callable[[], object] | None = None,
        repo_registry_repo: RepoRegistryRepository | None = None,
        stabilization_enabled: bool = True,
        include_info_default: bool = False,
        symbol_info_budget_sec_default: float = 10.0,
        resolve_symbols_default_provider: Callable[[], bool] | None = None,
        stabilization_service: StabilizationPort | None = None,
    ) -> None:
        """검색 오케스트레이터를 주입한다."""
        self._orchestrator = orchestrator
        self._workspace_repo = workspace_repo
        self._tool_layer_repo = tool_layer_repo
        self._metrics_provider = metrics_provider
        self._repo_registry_repo = repo_registry_repo
        self._include_info_default = bool(include_info_default)
        self._symbol_info_budget_sec_default = max(0.0, float(symbol_info_budget_sec_default))
        self._resolve_symbols_default_provider = resolve_symbols_default_provider
        self._stabilization_service = (
            stabilization_service if stabilization_service is not None else StabilizationService(enabled=stabilization_enabled)
        )

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
        stabilization_meta = self._stabilization_service.build_search_success_meta(
            arguments=arguments,
            repo=repo,
            query=query,
            items=result.items,
            degraded=result.meta.degraded,
            fatal_error=result.meta.fatal_error,
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
        effective_repo_root = getattr(item, "repo", repo_root)
        if not isinstance(effective_repo_root, str) or effective_repo_root.strip() == "":
            effective_repo_root = repo_root
        snapshot = tool_layer_repo.load_effective_snapshot(
            workspace_id=workspace.path,
            repo_root=effective_repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
        )
        l4_snapshot = snapshot.get("l4")
        if isinstance(l4_snapshot, dict):
            payload["l4"] = l4_snapshot
        l5_snapshot = snapshot.get("l5", [])
        if isinstance(l5_snapshot, list) and len(l5_snapshot) > 0:
            payload["l5"] = l5_snapshot
        self._attach_single_line_policy(payload=payload, item=item, snapshot=snapshot)
        return payload

    def _attach_single_line_policy(self, *, payload: dict[str, object], item: object, snapshot: dict[str, object]) -> None:
        # NOTE(policy): External tool output must expose exactly one canonical line.
        # We prefer L3(AST/text) coordinates for safe edits. L5/LSP semantic lines stay internal.
        if str(payload.get("type", "")) != "symbol":
            return
        line, end_line = self._resolve_canonical_line(item=item, snapshot=snapshot)
        if line is None:
            return
        payload["line"] = int(line)
        payload["end_line"] = int(end_line if end_line is not None else line)

    def _resolve_canonical_line(self, *, item: object, snapshot: dict[str, object]) -> tuple[int | None, int | None]:
        l3 = snapshot.get("l3")
        name = getattr(item, "name", None)
        kind = getattr(item, "kind", None)
        if isinstance(l3, dict):
            symbols = l3.get("symbols")
            if isinstance(symbols, list):
                for symbol in symbols:
                    if not isinstance(symbol, dict):
                        continue
                    symbol_name = symbol.get("name")
                    symbol_kind = symbol.get("kind")
                    if isinstance(name, str) and name.strip() != "" and str(symbol_name) != name:
                        continue
                    if isinstance(kind, str) and kind.strip() != "" and str(symbol_kind) != kind:
                        continue
                    try:
                        line = int(symbol.get("line", 0))
                        end_line = int(symbol.get("end_line", line))
                    except (TypeError, ValueError):
                        continue
                    if line > 0:
                        return (line, end_line if end_line >= line else line)
        raw_line = getattr(item, "line", None)
        raw_end_line = getattr(item, "end_line", None)
        try:
            if raw_line is not None:
                line = int(raw_line)
                if line > 0:
                    end_line = int(raw_end_line) if raw_end_line is not None else line
                    return (line, end_line if end_line >= line else line)
        except (TypeError, ValueError):
            return (None, None)
        return (None, None)

    def _build_progress_meta(self) -> dict[str, object] | None:
        """파이프라인 진행률 메타를 반환한다."""
        if self._metrics_provider is None:
            return None
        try:
            metrics = self._metrics_provider()
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            reason = f"metrics_provider_error:{type(exc).__name__}"
            log.debug("failed to collect search progress metrics", exc_info=True)
            return {
                "progress_percent_l2": 0.0,
                "progress_percent_l3": 0.0,
                "eta_l2_sec": -1,
                "eta_l3_sec": -1,
                "remaining_jobs_l2": 0,
                "remaining_jobs_l3": 0,
                "worker_state": "unknown",
                "error_reason": reason,
            }
        if not hasattr(metrics, "to_dict"):
            return {
                "progress_percent_l2": 0.0,
                "progress_percent_l3": 0.0,
                "eta_l2_sec": -1,
                "eta_l3_sec": -1,
                "remaining_jobs_l2": 0,
                "remaining_jobs_l3": 0,
                "worker_state": "unknown",
                "error_reason": "metrics_provider_missing_to_dict",
            }
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
