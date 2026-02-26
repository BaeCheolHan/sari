"""HTTP 요청 파싱 책임을 담당한다."""

from __future__ import annotations

from collections.abc import Mapping

from starlette.requests import Request
from starlette.responses import JSONResponse

from sari.core.repo.context_resolver import resolve_repo_context
from sari.core.models import ErrorResponseDTO
from sari.http.context import HttpContext


def resolve_repo_from_query(context: HttpContext, request: Request) -> tuple[str | None, str | None, str | None, JSONResponse | None]:
    """쿼리스트링의 repo를 검증하고 repo_id/repo_root/repo_key를 반환한다."""
    raw_repo = str(request.query_params.get("repo", "")).strip()
    if raw_repo == "":
        raw_repo = str(request.query_params.get("repo_id", "")).strip()
    resolved, error = resolve_repo_context(
        raw_repo=raw_repo,
        workspace_repo=context.workspace_repo,
        repo_registry_repo=context.repo_registry_repo,
        allow_absolute_input=True,
    )
    if error is not None:
        return (None, None, None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
    if resolved is None:
        raise ValueError("resolve_repo_context returned no error but resolved is None")
    return (resolved.repo_id, resolved.repo_root, resolved.repo_key, None)


def resolve_repo_from_value(context: HttpContext, raw_repo: object) -> tuple[str | None, str | None, str | None, JSONResponse | None]:
    """일반 값 입력의 repo를 검증하고 repo_id/repo_root/repo_key를 반환한다."""
    raw_value = raw_repo if isinstance(raw_repo, str) else ""
    resolved, error = resolve_repo_context(
        raw_repo=raw_value,
        workspace_repo=context.workspace_repo,
        repo_registry_repo=context.repo_registry_repo,
        allow_absolute_input=True,
    )
    if error is not None:
        return (None, None, None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
    if resolved is None:
        raise ValueError("resolve_repo_context returned no error but resolved is None")
    return (resolved.repo_id, resolved.repo_root, resolved.repo_key, None)


def to_int(value: object) -> int | None:
    """정수 입력을 안전하게 변환한다."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip() != "":
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def parse_language_filter_value(raw_value: object) -> tuple[tuple[str, ...] | None, ErrorResponseDTO | None]:
    """language_filter 값을 튜플로 파싱한다."""
    if raw_value is None:
        return (None, None)
    if isinstance(raw_value, str):
        parts = [token.strip() for token in raw_value.split(",") if token.strip() != ""]
        return (None if len(parts) == 0 else tuple(parts), None)
    if isinstance(raw_value, list):
        parsed: list[str] = []
        for item in raw_value:
            if not isinstance(item, str):
                return (
                    None,
                    ErrorResponseDTO(code="ERR_INVALID_LANGUAGE_FILTER", message="language_filter items must be string"),
                )
            token = item.strip()
            if token != "":
                parsed.append(token)
        return (None if len(parsed) == 0 else tuple(parsed), None)
    return (None, ErrorResponseDTO(code="ERR_INVALID_LANGUAGE_FILTER", message="language_filter must be string or string[]"))


def parse_bool_value(
    raw_value: object,
    *,
    error_code: str,
    field_name: str,
) -> tuple[bool, ErrorResponseDTO | None]:
    """불리언 형태 입력을 엄격하게 파싱한다."""
    if raw_value is None:
        return (False, None)
    if isinstance(raw_value, bool):
        return (raw_value, None)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return (True, None)
        if normalized in {"0", "false", "no", "off"}:
            return (False, None)
    return (False, ErrorResponseDTO(code=error_code, message=f"{field_name} must be boolean"))


def read_language_filter_from_query(request: Request) -> tuple[tuple[str, ...] | None, ErrorResponseDTO | None]:
    """쿼리스트링에서 language_filter를 읽어 파싱한다."""
    params = request.query_params
    if hasattr(params, "getlist"):
        values = [str(item).strip() for item in params.getlist("language_filter") if str(item).strip() != ""]
        if len(values) == 0:
            return (None, None)
        if len(values) == 1:
            return parse_language_filter_value(values[0])
        return parse_language_filter_value(values)
    return parse_language_filter_value(params.get("language_filter"))


def read_required_languages_from_query(request: Request) -> tuple[tuple[str, ...] | None, ErrorResponseDTO | None]:
    """쿼리스트링에서 required_languages를 읽어 파싱한다."""
    params = request.query_params
    if hasattr(params, "getlist"):
        values = [str(item).strip() for item in params.getlist("required_languages") if str(item).strip() != ""]
        if len(values) == 0:
            return (None, None)
        if len(values) == 1:
            return parse_language_filter_value(values[0])
        return parse_language_filter_value(values)
    return parse_language_filter_value(params.get("required_languages"))


def parse_fail_on_unavailable_from_query(request: Request) -> tuple[bool, ErrorResponseDTO | None]:
    """fail_on_unavailable 쿼리값을 파싱한다."""
    raw_value = request.query_params.get("fail_on_unavailable")
    if raw_value is None:
        return (True, None)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return (True, None)
        if normalized in {"0", "false", "no", "off"}:
            return (False, None)
    return (
        True,
        ErrorResponseDTO(code="ERR_INVALID_FAIL_ON_UNAVAILABLE", message="fail_on_unavailable must be boolean"),
    )


def parse_strict_all_languages_from_query(request: Request) -> tuple[bool, ErrorResponseDTO | None]:
    """strict_all_languages 쿼리값을 파싱한다."""
    raw_value = request.query_params.get("strict_all_languages")
    if raw_value is None:
        return (True, None)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return (True, None)
        if normalized in {"0", "false", "no", "off"}:
            return (False, None)
    return (
        True,
        ErrorResponseDTO(code="ERR_INVALID_STRICT_ALL_LANGUAGES", message="strict_all_languages must be boolean"),
    )


def parse_strict_symbol_gate_from_query(request: Request) -> tuple[bool, ErrorResponseDTO | None]:
    """strict_symbol_gate 쿼리값을 파싱한다."""
    raw_value = request.query_params.get("strict_symbol_gate")
    if raw_value is None:
        return (True, None)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return (True, None)
        if normalized in {"0", "false", "no", "off"}:
            return (False, None)
    return (
        True,
        ErrorResponseDTO(code="ERR_INVALID_STRICT_SYMBOL_GATE", message="strict_symbol_gate must be boolean"),
    )


def resolve_format(raw_format: object) -> tuple[str, JSONResponse | None]:
    """응답 포맷 파라미터를 검증한다."""
    if not isinstance(raw_format, str) or raw_format.strip() == "":
        return ("json", None)
    normalized = raw_format.strip().lower()
    if normalized in {"json", "pack1"}:
        return (normalized, None)
    error = ErrorResponseDTO(code="ERR_INVALID_FORMAT", message="format must be json or pack1")
    return ("json", JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))


def build_read_arguments(
    *,
    repo_root: str,
    repo_key: str | None,
    mode: str,
    source: Mapping[str, object],
) -> tuple[dict[str, object] | None, JSONResponse | None]:
    """read 호출 인자를 모드별로 검증해 구성한다."""
    arguments: dict[str, object] = {"repo": repo_root, "mode": mode}
    if isinstance(repo_key, str) and repo_key.strip() != "":
        arguments["repo_key"] = repo_key.strip()
    target = source.get("target")
    if mode in {"file", "symbol", "diff_preview"}:
        if not isinstance(target, str) or target.strip() == "":
            error = ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target is required")
            return (None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
        arguments["target"] = target.strip()
    if mode == "file":
        if "offset" in source:
            offset = to_int(source.get("offset"))
            if offset is None or offset < 0:
                error = ErrorResponseDTO(code="ERR_INVALID_OFFSET", message="offset must be non-negative integer")
                return (None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
            arguments["offset"] = offset
        if "limit" in source:
            limit = to_int(source.get("limit"))
            if limit is None or limit <= 0:
                error = ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer")
                return (None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
            arguments["limit"] = limit
    if mode == "symbol":
        if "limit" in source:
            limit = to_int(source.get("limit"))
            if limit is None or limit <= 0:
                error = ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer")
                return (None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
            arguments["limit"] = limit
        path = source.get("path")
        if isinstance(path, str) and path.strip() != "":
            arguments["path"] = path.strip()
    if mode == "snippet":
        snippet_target = source.get("target")
        tag = source.get("tag")
        target_value = snippet_target.strip() if isinstance(snippet_target, str) else ""
        tag_value = tag.strip() if isinstance(tag, str) else ""
        if target_value == "" and tag_value == "":
            error = ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target or tag is required")
            return (None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
        if target_value != "":
            arguments["target"] = target_value
        if tag_value != "":
            arguments["tag"] = tag_value
        if "limit" in source:
            limit = to_int(source.get("limit"))
            if limit is None or limit <= 0:
                error = ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer")
                return (None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
            arguments["limit"] = limit
    if mode == "diff_preview":
        content = source.get("content")
        if not isinstance(content, str):
            error = ErrorResponseDTO(code="ERR_CONTENT_REQUIRED", message="content is required")
            return (None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
        arguments["content"] = content
        against = source.get("against")
        if isinstance(against, str) and against.strip() != "":
            arguments["against"] = against.strip()
        if "max_preview_chars" in source:
            max_chars = to_int(source.get("max_preview_chars"))
            if max_chars is None or max_chars <= 0:
                error = ErrorResponseDTO(code="ERR_INVALID_MAX_PREVIEW", message="max_preview_chars must be positive integer")
                return (None, JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=400))
            arguments["max_preview_chars"] = max_chars
    return (arguments, None)
