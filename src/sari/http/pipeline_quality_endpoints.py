"""Pipeline quality 관련 HTTP 엔드포인트."""

from __future__ import annotations

from starlette.responses import JSONResponse

from sari.core.exceptions import QualityError
from sari.core.models import ErrorResponseDTO
from sari.http.context import HttpContext
from sari.http.pipeline_common import pipeline_quality_or_error
from sari.http.request_parsers import read_language_filter_from_query, resolve_repo_from_query


async def pipeline_quality_run_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_quality_or_error(context)
    if error is not None:
        return error
    _repo_id, repo, _repo_key, repo_error = resolve_repo_from_query(context, request)
    if repo_error is not None:
        return repo_error
    assert repo is not None
    limit_files_raw = str(request.query_params.get("limit_files", "2000")).strip()
    profile = str(request.query_params.get("profile", "default")).strip()
    try:
        limit_files = int(limit_files_raw)
    except ValueError:
        error = ErrorResponseDTO(code="ERR_INVALID_LIMIT_FILES", message="limit_files는 정수여야 합니다")
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    language_filter, filter_error = read_language_filter_from_query(request)
    if filter_error is not None:
        return JSONResponse({"error": {"code": filter_error.code, "message": filter_error.message}}, status_code=400)
    try:
        summary = service.run(repo_root=repo, limit_files=limit_files, profile=profile, language_filter=language_filter)
    except QualityError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    return JSONResponse({"quality": summary})


async def pipeline_quality_report_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_quality_or_error(context)
    if error is not None:
        return error
    _repo_id, repo, _repo_key, repo_error = resolve_repo_from_query(context, request)
    if repo_error is not None:
        return repo_error
    assert repo is not None
    try:
        summary = service.get_latest_report(repo_root=repo)
    except QualityError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=404)
    return JSONResponse({"quality": summary})
