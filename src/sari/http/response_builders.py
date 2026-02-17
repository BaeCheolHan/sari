"""HTTP 응답 변환 책임을 담당한다."""

from __future__ import annotations

from collections.abc import Mapping

from starlette.responses import JSONResponse


def read_error_status_code(error_code: str) -> int:
    """read 계열 오류코드를 HTTP 상태코드로 매핑한다."""
    if error_code in {"ERR_FILE_NOT_FOUND", "ERR_EVENT_NOT_FOUND"}:
        return 404
    if error_code in {"ERR_HTTP_READ_INTERNAL", "ERR_DAEMON_INTERNAL"}:
        return 500
    return 400


def extract_read_error(payload: Mapping[str, object]) -> tuple[str, str]:
    """pack1 read 응답에서 오류코드/메시지를 추출한다."""
    structured = payload.get("structuredContent")
    if isinstance(structured, dict):
        meta = structured.get("meta")
        if isinstance(meta, dict):
            errors = meta.get("errors")
            if isinstance(errors, list) and len(errors) > 0 and isinstance(errors[0], dict):
                first = errors[0]
                code = str(first.get("code", "")).strip()
                message = str(first.get("message", "")).strip()
                if code != "":
                    return (code, message if message != "" else "read failed")
    content = payload.get("content")
    if isinstance(content, list) and len(content) > 0 and isinstance(content[0], dict):
        message_raw = content[0].get("text")
        if isinstance(message_raw, str) and message_raw.strip() != "":
            return ("ERR_READ_FAILED", message_raw.strip())
    return ("ERR_READ_FAILED", "read failed")


def pack1_to_http_json(payload: dict[str, object]) -> tuple[dict[str, object], int]:
    """pack1 응답을 HTTP JSON 바디 + 상태코드로 변환한다."""
    is_error = bool(payload.get("isError", False))
    structured = payload.get("structuredContent")
    if not isinstance(structured, dict):
        code, message = extract_read_error(payload)
        return ({"error": {"code": code, "message": message}}, read_error_status_code(code))
    meta = structured.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    items = structured.get("items")
    if not isinstance(items, list):
        items = []
    if is_error:
        code, message = extract_read_error(payload)
        return ({"error": {"code": code, "message": message}, "meta": meta}, read_error_status_code(code))
    return ({"items": items, "meta": meta}, 200)


def read_response(payload: dict[str, object], output_format: str) -> JSONResponse:
    """read 결과를 요청 포맷에 맞춘 HTTP 응답으로 반환한다."""
    if output_format == "pack1":
        status_code = 200
        if bool(payload.get("isError", False)):
            code, _ = extract_read_error(payload)
            status_code = read_error_status_code(code)
        return JSONResponse(payload, status_code=status_code)
    body, status_code = pack1_to_http_json(payload)
    return JSONResponse(body, status_code=status_code)
