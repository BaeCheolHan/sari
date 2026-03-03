"""검색 관련 HTTP 엔드포인트를 제공한다."""

from __future__ import annotations

from starlette.responses import JSONResponse

from sari.http.context import HttpContext
from sari.http.request_parsers import resolve_repo_from_query


def _build_meta_payload(result) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "candidate_count": result.meta.candidate_count,
        "resolved_count": result.meta.resolved_count,
        "candidate_source": result.meta.candidate_source,
        "fatal_error": result.meta.fatal_error,
        "degraded": result.meta.degraded,
        "error_count": result.meta.error_count,
        "ranking_policy": result.meta.ranking_policy,
        "rrf_k": result.meta.rrf_k,
        "lsp_query_mode": result.meta.lsp_query_mode,
        "lsp_sync_mode": result.meta.lsp_sync_mode,
        "lsp_fallback_used": result.meta.lsp_fallback_used,
        "lsp_fallback_reason": result.meta.lsp_fallback_reason,
        "lsp_include_info_requested": result.meta.include_info_requested,
        "lsp_symbol_info_budget_sec": result.meta.symbol_info_budget_sec,
        "lsp_symbol_info_requested_count": result.meta.symbol_info_requested_count,
        "lsp_symbol_info_budget_exceeded_count": result.meta.symbol_info_budget_exceeded_count,
        "lsp_symbol_info_skipped_count": result.meta.symbol_info_skipped_count,
        "importance_policy": result.meta.importance_policy,
        "importance_weights": result.meta.importance_weights,
        "importance_normalize_mode": result.meta.importance_normalize_mode,
        "importance_max_boost": result.meta.importance_max_boost,
        "vector_enabled": result.meta.vector_enabled,
        "vector_rerank_count": result.meta.vector_rerank_count,
        "vector_applied_count": result.meta.vector_applied_count,
        "vector_skipped_count": result.meta.vector_skipped_count,
        "vector_threshold": result.meta.vector_threshold,
        "ranking_version": result.meta.ranking_version,
        "ranking_components_enabled": result.meta.ranking_components_enabled if result.meta.ranking_components_enabled is not None else {},
        "errors": [error.to_dict() for error in result.meta.errors],
    }


async def search_endpoint(request) -> JSONResponse:
    """검색 결과를 반환한다."""
    context: HttpContext = request.app.state.context
    repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    if repo_id is None or repo is None:
        raise ValueError("resolve_repo_from_query returned no error but repo_id/repo is None")
    query = str(request.query_params.get("q", "")).strip()
    limit_raw = str(request.query_params.get("limit", "20"))
    if query == "":
        return JSONResponse({"error": {"code": "ERR_QUERY_REQUIRED", "message": "q는 비어 있을 수 없습니다"}}, status_code=400)
    try:
        limit = int(limit_raw)
    except ValueError:
        return JSONResponse({"error": {"code": "ERR_INVALID_LIMIT", "message": "limit는 정수여야 합니다"}}, status_code=400)
    if limit <= 0:
        return JSONResponse({"error": {"code": "ERR_INVALID_LIMIT", "message": "limit는 1 이상이어야 합니다"}}, status_code=400)

    resolve_symbols_default = False
    provider = getattr(context, "search_resolve_symbols_default_provider", None)
    if callable(provider):
        try:
            resolve_symbols_default = bool(provider())
        except (RuntimeError, OSError, ValueError, TypeError):
            resolve_symbols_default = False
    try:
        result = context.search_orchestrator.search(
            query=query,
            limit=limit,
            repo_root=repo,
            repo_id=repo_id,
            resolve_symbols=resolve_symbols_default,
        )
    except TypeError:
        result = context.search_orchestrator.search(query=query, limit=limit, repo_root=repo)

    progress_meta = _search_progress_meta(context)
    if result.meta.fatal_error:
        first_error = result.meta.errors[0]
        meta_payload = _build_meta_payload(result)
        if progress_meta is not None:
            meta_payload["index_progress"] = progress_meta
        return JSONResponse({"error": {"code": first_error.code, "message": first_error.message}, "meta": meta_payload}, status_code=503)

    meta_payload = _build_meta_payload(result)
    if progress_meta is not None:
        meta_payload["index_progress"] = progress_meta
    presentation_service = context.resolve_http_presentation_service()
    return JSONResponse(
        {
            "items": [presentation_service.build_search_item_payload(repo_root=repo, item=item) for item in result.items],
            "meta": meta_payload,
        }
    )


def _search_progress_meta(context: HttpContext) -> dict[str, object] | None:
    if context.file_collection_service is None:
        return None
    payload = context.file_collection_service.get_pipeline_metrics().to_dict()

    def _safe_float(value: object, default: float) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return default
            try:
                return float(stripped)
            except ValueError:
                return default
        return default

    def _safe_int(value: object, default: int) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return default
            try:
                return int(stripped)
            except ValueError:
                return default
        return default

    raw_worker_state = payload.get("worker_state", "unknown")
    worker_state = raw_worker_state if isinstance(raw_worker_state, str) and raw_worker_state.strip() != "" else "unknown"
    return {
        "progress_percent_l2": _safe_float(payload.get("progress_percent_l2", 0.0), 0.0),
        "progress_percent_l3": _safe_float(payload.get("progress_percent_l3", 0.0), 0.0),
        "eta_l2_sec": _safe_int(payload.get("eta_l2_sec", -1), -1),
        "eta_l3_sec": _safe_int(payload.get("eta_l3_sec", -1), -1),
        "remaining_jobs_l2": _safe_int(payload.get("remaining_jobs_l2", 0), 0),
        "remaining_jobs_l3": _safe_int(payload.get("remaining_jobs_l3", 0), 0),
        "worker_state": worker_state,
    }
