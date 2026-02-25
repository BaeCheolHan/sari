"""Pipeline LSP matrix 관련 HTTP 엔드포인트."""

from __future__ import annotations

from starlette.responses import JSONResponse

from sari.core.exceptions import DaemonError
from sari.core.models import ErrorResponseDTO
from sari.http.context import HttpContext
from sari.http.pipeline_common import pipeline_lsp_matrix_or_error
from sari.http.request_parsers import (
    parse_fail_on_unavailable_from_query,
    parse_strict_all_languages_from_query,
    parse_strict_symbol_gate_from_query,
    read_required_languages_from_query,
    resolve_repo_from_query,
)


async def pipeline_lsp_matrix_run_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_lsp_matrix_or_error(context)
    if error is not None:
        return error
    _repo_id, repo, _repo_key, repo_error = resolve_repo_from_query(context, request)
    if repo_error is not None:
        return repo_error
    assert repo is not None
    required_languages, required_error = read_required_languages_from_query(request)
    if required_error is not None:
        return JSONResponse({"error": {"code": required_error.code, "message": required_error.message}}, status_code=400)
    fail_on_unavailable, fail_error = parse_fail_on_unavailable_from_query(request)
    if fail_error is not None:
        return JSONResponse({"error": {"code": fail_error.code, "message": fail_error.message}}, status_code=400)
    strict_all_languages, strict_error = parse_strict_all_languages_from_query(request)
    if strict_error is not None:
        return JSONResponse({"error": {"code": strict_error.code, "message": strict_error.message}}, status_code=400)
    strict_symbol_gate, strict_symbol_gate_error = parse_strict_symbol_gate_from_query(request)
    if strict_symbol_gate_error is not None:
        return JSONResponse({"error": {"code": strict_symbol_gate_error.code, "message": strict_symbol_gate_error.message}}, status_code=400)
    try:
        summary = service.run(
            repo_root=repo,
            required_languages=required_languages,
            fail_on_unavailable=fail_on_unavailable,
            strict_all_languages=strict_all_languages,
            strict_symbol_gate=strict_symbol_gate,
        )
    except DaemonError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    return JSONResponse({"lsp_matrix": summary})


async def pipeline_lsp_matrix_report_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_lsp_matrix_or_error(context)
    if error is not None:
        return error
    _repo_id, repo, _repo_key, repo_error = resolve_repo_from_query(context, request)
    if repo_error is not None:
        return repo_error
    assert repo is not None
    try:
        summary = service.get_latest_report(repo_root=repo)
    except DaemonError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=404)
    return JSONResponse({"lsp_matrix": summary})
