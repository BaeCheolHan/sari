"""Read 관련 HTTP 엔드포인트를 제공한다."""

from __future__ import annotations

from starlette.responses import JSONResponse

from sari.core.models import ErrorResponseDTO
from sari.http.context import HttpContext
from sari.http.request_parsers import build_read_arguments, resolve_format, resolve_repo_from_query, resolve_repo_from_value
from sari.http.response_builders import read_response


async def read_endpoint(request) -> JSONResponse:
    """read 모드 기반 조회를 수행한다."""
    context: HttpContext = request.app.state.context
    if context.read_facade_service is None:
        error = ErrorResponseDTO(code="ERR_HTTP_READ_UNAVAILABLE", message="read service is unavailable")
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=503)
    repo_id, repo, repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    if repo_id is None or repo is None:
        raise ValueError("resolve_repo_from_query returned no error but repo_id/repo is None")
    mode_raw = str(request.query_params.get("mode", "")).strip().lower()
    if mode_raw == "":
        error = ErrorResponseDTO(code="ERR_MODE_REQUIRED", message="mode is required")
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    output_format, format_error = resolve_format(request.query_params.get("format"))
    if format_error is not None:
        return format_error
    source = {str(k): v for k, v in request.query_params.items()}
    arguments, arg_error = build_read_arguments(repo_root=repo, repo_key=repo_key, mode=mode_raw, source=source)
    if arg_error is not None:
        return arg_error
    if arguments is None:
        raise ValueError("build_read_arguments returned no error but arguments is None")
    arguments["repo_id"] = repo_id
    payload = context.read_facade_service.read(arguments=arguments)
    return read_response(payload=payload, output_format=output_format)


async def read_file_endpoint(request) -> JSONResponse:
    """`mode=file` 단축 엔드포인트."""
    query = dict(request.query_params)
    query["mode"] = "file"
    proxy_request = type("ReadProxyRequest", (), {"query_params": query, "app": request.app})()
    return await read_endpoint(proxy_request)


async def read_symbol_endpoint(request) -> JSONResponse:
    """`mode=symbol` 단축 엔드포인트."""
    query = dict(request.query_params)
    query["mode"] = "symbol"
    proxy_request = type("ReadProxyRequest", (), {"query_params": query, "app": request.app})()
    return await read_endpoint(proxy_request)


async def read_snippet_endpoint(request) -> JSONResponse:
    """`mode=snippet` 단축 엔드포인트."""
    query = dict(request.query_params)
    query["mode"] = "snippet"
    proxy_request = type("ReadProxyRequest", (), {"query_params": query, "app": request.app})()
    return await read_endpoint(proxy_request)


async def read_diff_preview_endpoint(request) -> JSONResponse:
    """diff preview 조회를 수행한다."""
    context: HttpContext = request.app.state.context
    if context.read_facade_service is None:
        error = ErrorResponseDTO(code="ERR_HTTP_READ_UNAVAILABLE", message="read service is unavailable")
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=503)
    try:
        body_raw = await request.json()
    except ValueError:
        error = ErrorResponseDTO(code="ERR_INVALID_JSON_BODY", message="invalid json body")
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    if not isinstance(body_raw, dict):
        error = ErrorResponseDTO(code="ERR_INVALID_JSON_BODY", message="json body must be object")
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400)
    repo_id, repo, repo_key, error_response = resolve_repo_from_value(context, body_raw.get("repo"))
    if error_response is not None:
        return error_response
    if repo_id is None or repo is None:
        raise ValueError("resolve_repo_from_value returned no error but repo_id/repo is None")
    output_format, format_error = resolve_format(body_raw.get("format"))
    if format_error is not None:
        return format_error
    arguments, arg_error = build_read_arguments(repo_root=repo, repo_key=repo_key, mode="diff_preview", source=body_raw)
    if arg_error is not None:
        return arg_error
    if arguments is None:
        raise ValueError("build_read_arguments returned no error but arguments is None")
    arguments["repo_id"] = repo_id
    payload = context.read_facade_service.read(arguments=arguments)
    return read_response(payload=payload, output_format=output_format)
