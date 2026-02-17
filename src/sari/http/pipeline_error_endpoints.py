"""파이프라인 오류 조회용 HTTP 엔드포인트를 제공한다."""

from __future__ import annotations

import html

from starlette.responses import HTMLResponse, JSONResponse

from sari.core.exceptions import ValidationError
from sari.core.models import ErrorResponseDTO
from sari.core.repo_resolver import resolve_repo_root
from sari.http.context import HttpContext


def _error_json(code: str, message: str, status_code: int) -> JSONResponse:
    """공통 오류 응답을 생성한다."""
    payload = ErrorResponseDTO(code=code, message=message)
    return JSONResponse({"error": {"code": payload.code, "message": payload.message}}, status_code=status_code)


def _resolve_repo_root_or_error(context: HttpContext, repo: str) -> tuple[str | None, JSONResponse | None]:
    """선택적 repo 파라미터를 실제 repo_root로 해석한다."""
    normalized_repo = repo.strip()
    if normalized_repo == "":
        return (None, None)
    try:
        resolved = resolve_repo_root(
            repo_or_path=normalized_repo,
            workspace_paths=[item.path for item in context.workspace_repo.list_all()],
        )
    except ValidationError as exc:
        return (None, _error_json(code=exc.context.code, message=exc.context.message, status_code=400))
    return (resolved, None)


async def pipeline_errors_api_endpoint(request) -> JSONResponse:
    """파이프라인 오류 이벤트 목록을 반환한다."""
    context: HttpContext = request.app.state.context
    if context.file_collection_service is None:
        return _error_json(
            code="ERR_PIPELINE_ERROR_UNAVAILABLE",
            message="file collection is unavailable",
            status_code=503,
        )

    limit_raw = str(request.query_params.get("limit", "50"))
    offset_raw = str(request.query_params.get("offset", "0"))
    repo = str(request.query_params.get("repo", ""))
    code = str(request.query_params.get("code", "")).strip()
    try:
        limit = max(1, min(500, int(limit_raw)))
        offset = max(0, int(offset_raw))
    except ValueError:
        return _error_json(
            code="ERR_INVALID_LIMIT",
            message="limit/offset은 정수여야 합니다",
            status_code=400,
        )

    resolved_repo, error_response = _resolve_repo_root_or_error(context=context, repo=repo)
    if error_response is not None:
        return error_response
    items = context.file_collection_service.list_error_events(
        limit=limit,
        offset=offset,
        repo_root=resolved_repo,
        error_code=code if code != "" else None,
    )
    return JSONResponse({"items": items})


async def pipeline_error_detail_api_endpoint(request) -> JSONResponse:
    """단일 파이프라인 오류 이벤트 상세를 반환한다."""
    context: HttpContext = request.app.state.context
    if context.file_collection_service is None:
        return _error_json(
            code="ERR_PIPELINE_ERROR_UNAVAILABLE",
            message="file collection is unavailable",
            status_code=503,
        )
    event_id = str(request.path_params.get("event_id", "")).strip()
    if event_id == "":
        return _error_json(code="ERR_EVENT_ID_REQUIRED", message="event_id is required", status_code=400)
    item = context.file_collection_service.get_error_event(event_id=event_id)
    if item is None:
        return _error_json(code="ERR_EVENT_NOT_FOUND", message="event not found", status_code=404)
    return JSONResponse({"item": item})


async def pipeline_errors_html_endpoint(request) -> HTMLResponse:
    """파이프라인 오류 목록을 HTML 페이지로 렌더링한다."""
    context: HttpContext = request.app.state.context
    if context.file_collection_service is None:
        return HTMLResponse("<h1>file collection is unavailable</h1>", status_code=503)
    items = context.file_collection_service.list_error_events(limit=100, offset=0)
    rows: list[str] = []
    for item in items:
        event_id = html.escape(str(item.get("event_id", "")))
        occurred_at = html.escape(str(item.get("occurred_at", "")))
        component = html.escape(str(item.get("component", "")))
        phase = html.escape(str(item.get("phase", "")))
        error_code = html.escape(str(item.get("error_code", "")))
        message = html.escape(str(item.get("error_message", "")))
        rows.append(
            f"<tr><td>{occurred_at}</td><td>{component}</td><td>{phase}</td>"
            f"<td><a href='/pipeline/errors/{event_id}'>{error_code}</a></td><td>{message}</td></tr>"
        )
    table_rows = "".join(rows)
    html_body = (
        "<html><head><title>Pipeline Errors</title></head><body>"
        "<h1>Pipeline Error Events</h1>"
        "<table border='1' cellspacing='0' cellpadding='6'>"
        "<tr><th>Occurred At</th><th>Component</th><th>Phase</th><th>Error Code</th><th>Message</th></tr>"
        f"{table_rows}</table></body></html>"
    )
    return HTMLResponse(html_body, status_code=200)


async def pipeline_error_detail_html_endpoint(request) -> HTMLResponse:
    """파이프라인 오류 상세를 HTML 페이지로 렌더링한다."""
    context: HttpContext = request.app.state.context
    if context.file_collection_service is None:
        return HTMLResponse("<h1>file collection is unavailable</h1>", status_code=503)
    event_id = str(request.path_params.get("event_id", "")).strip()
    item = context.file_collection_service.get_error_event(event_id=event_id)
    if item is None:
        return HTMLResponse("<h1>event not found</h1>", status_code=404)
    details: list[str] = []
    keys = [
        "event_id",
        "occurred_at",
        "component",
        "phase",
        "severity",
        "scope_type",
        "repo_root",
        "relative_path",
        "job_id",
        "attempt_count",
        "error_code",
        "error_message",
        "error_type",
        "worker_name",
        "run_mode",
    ]
    for key in keys:
        details.append(f"<tr><th>{html.escape(key)}</th><td>{html.escape(str(item.get(key)))}</td></tr>")
    stacktrace = html.escape(str(item.get("stacktrace_text", "")))
    context_json = html.escape(str(item.get("context_json", "")))
    html_body = (
        "<html><head><title>Pipeline Error Detail</title></head><body>"
        "<h1>Pipeline Error Detail</h1><p><a href='/pipeline/errors'>Back to list</a></p>"
        "<table border='1' cellspacing='0' cellpadding='6'>"
        + "".join(details)
        + "</table><h2>Stacktrace</h2><pre>"
        + stacktrace
        + "</pre><h2>Context</h2><pre>"
        + context_json
        + "</pre></body></html>"
    )
    return HTMLResponse(html_body, status_code=200)
