"""Pipeline HTTP 엔드포인트 공통 유틸리티."""

from __future__ import annotations

from starlette.responses import JSONResponse

from sari.core.exceptions import ValidationError
from sari.http.context import HttpContext


def error_response(*, code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def validation_error_response(exc: ValidationError) -> JSONResponse:
    return error_response(code=exc.context.code, message=exc.context.message, status_code=400)


def pipeline_control_or_error(context: HttpContext) -> tuple[object | None, JSONResponse | None]:
    service = context.pipeline_control_service
    if service is None:
        return (
            None,
            error_response(
                code="ERR_PIPELINE_ALERT_UNAVAILABLE",
                message="pipeline control is unavailable",
                status_code=503,
            ),
        )
    return (service, None)


def pipeline_quality_or_error(context: HttpContext) -> tuple[object | None, JSONResponse | None]:
    service = context.pipeline_quality_service
    if service is None:
        return (
            None,
            error_response(
                code="ERR_PIPELINE_QUALITY_UNAVAILABLE",
                message="pipeline quality is unavailable",
                status_code=503,
            ),
        )
    return (service, None)


def pipeline_perf_or_error(context: HttpContext) -> tuple[object | None, JSONResponse | None]:
    service = context.pipeline_perf_service
    if service is None:
        return (
            None,
            error_response(
                code="ERR_PIPELINE_PERF_UNAVAILABLE",
                message="pipeline perf is unavailable",
                status_code=503,
            ),
        )
    return (service, None)


def pipeline_lsp_matrix_or_error(context: HttpContext) -> tuple[object | None, JSONResponse | None]:
    service = context.pipeline_lsp_matrix_service
    if service is None:
        return (
            None,
            error_response(
                code="ERR_PIPELINE_LSP_MATRIX_UNAVAILABLE",
                message="pipeline lsp matrix is unavailable",
                status_code=503,
            ),
        )
    return (service, None)


def parse_limit_or_error(request) -> tuple[int | None, JSONResponse | None]:
    raw = str(request.query_params.get("limit", "20"))
    try:
        return (int(raw), None)
    except ValueError:
        return (
            None,
            error_response(code="ERR_INVALID_LIMIT", message="limit는 정수여야 합니다", status_code=400),
        )


def parse_optional_onoff_bool(*, raw_value: str, field_name: str) -> tuple[bool | None, JSONResponse | None]:
    normalized = raw_value.strip().lower()
    if normalized == "":
        return (None, None)
    if normalized in {"on", "true", "1"}:
        return (True, None)
    if normalized in {"off", "false", "0"}:
        return (False, None)
    return (
        None,
        error_response(
            code="ERR_POLICY_INVALID",
            message=f"{field_name}는 on/off여야 합니다",
            status_code=400,
        ),
    )


def parse_optional_int_params(request, *, field_names: tuple[str, ...]) -> tuple[dict[str, int | None], JSONResponse | None]:
    values: dict[str, int | None] = {}
    for field_name in field_names:
        raw = str(request.query_params.get(field_name, "")).strip()
        if raw == "":
            values[field_name] = None
            continue
        try:
            values[field_name] = int(raw)
        except ValueError:
            return (
                {},
                error_response(
                    code="ERR_POLICY_INVALID",
                    message=f"{field_name}는 정수여야 합니다",
                    status_code=400,
                ),
            )
    return (values, None)
