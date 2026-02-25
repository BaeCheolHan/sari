"""Pipeline perf 관련 HTTP 엔드포인트."""

from __future__ import annotations

from starlette.responses import JSONResponse

from sari.core.exceptions import PerfError
from sari.core.models import ErrorResponseDTO
from sari.http.context import HttpContext
from sari.http.pipeline_common import pipeline_perf_or_error
from sari.http.request_parsers import parse_bool_value, resolve_repo_from_query


async def pipeline_perf_run_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_perf_or_error(context)
    if error is not None:
        return error
    _repo_id, repo, _repo_key, repo_error = resolve_repo_from_query(context, request)
    if repo_error is not None:
        return repo_error
    assert repo is not None
    target_files_raw = str(request.query_params.get("target_files", "2000")).strip()
    profile = str(request.query_params.get("profile", "realistic_v1")).strip()
    dataset_mode = str(request.query_params.get("dataset_mode", "isolated")).strip().lower()
    fresh_db, fresh_db_error = parse_bool_value(
        request.query_params.get("fresh_db"),
        error_code="ERR_INVALID_FRESH_DB",
        field_name="fresh_db",
    )
    if fresh_db_error is not None:
        return JSONResponse({"error": {"code": fresh_db_error.code, "message": fresh_db_error.message}}, status_code=400)
    reset_probe_state, reset_probe_state_error = parse_bool_value(
        request.query_params.get("reset_probe_state"),
        error_code="ERR_INVALID_RESET_PROBE_STATE",
        field_name="reset_probe_state",
    )
    if reset_probe_state_error is not None:
        return JSONResponse({"error": {"code": reset_probe_state_error.code, "message": reset_probe_state_error.message}}, status_code=400)
    cold_lsp_reset, cold_lsp_reset_error = parse_bool_value(
        request.query_params.get("cold_lsp_reset"),
        error_code="ERR_INVALID_COLD_LSP_RESET",
        field_name="cold_lsp_reset",
    )
    if cold_lsp_reset_error is not None:
        return JSONResponse({"error": {"code": cold_lsp_reset_error.code, "message": cold_lsp_reset_error.message}}, status_code=400)
    if dataset_mode not in ("isolated", "legacy"):
        error = ErrorResponseDTO(code="ERR_INVALID_DATASET_MODE", message="dataset_mode must be isolated or legacy")
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    try:
        target_files = int(target_files_raw)
    except ValueError:
        error = ErrorResponseDTO(code="ERR_INVALID_TARGET_FILES", message="target_files는 정수여야 합니다")
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    try:
        summary = service.run(
            repo_root=repo,
            target_files=target_files,
            profile=profile,
            dataset_mode=dataset_mode,
            fresh_db=fresh_db,
            reset_probe_state=reset_probe_state,
            cold_lsp_reset=cold_lsp_reset,
        )
    except PerfError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    return JSONResponse({"perf": summary})


async def pipeline_perf_report_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_perf_or_error(context)
    if error is not None:
        return error
    try:
        summary = service.get_latest_report()
    except PerfError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=404)
    return JSONResponse({"perf": summary})
