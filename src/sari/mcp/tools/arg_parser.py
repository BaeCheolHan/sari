"""MCP 도구 인자 공통 파서."""

from __future__ import annotations

from sari.core.models import ErrorResponseDTO


def parse_positive_int(arguments: dict[str, object], key: str, default: int) -> tuple[int, ErrorResponseDTO | None]:
    """양수 정수 인자를 파싱한다."""
    raw = arguments.get(key, default)
    if not isinstance(raw, int) or raw <= 0:
        return 0, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} must be positive integer")
    return raw, None


def parse_non_negative_int(arguments: dict[str, object], key: str, default: int = 0) -> tuple[int, ErrorResponseDTO | None]:
    """0 이상 정수 인자를 파싱한다."""
    raw = arguments.get(key, default)
    if not isinstance(raw, int) or raw < 0:
        return 0, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} must be non-negative integer")
    return raw, None


def parse_optional_int(arguments: dict[str, object], key: str, default: int | None) -> tuple[int | None, ErrorResponseDTO | None]:
    """옵셔널 정수 인자를 파싱한다."""
    raw = arguments.get(key, default)
    if raw is None:
        return None, None
    if not isinstance(raw, int):
        return None, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} must be integer or null")
    return raw, None


def parse_optional_loose_int(
    arguments: dict[str, object],
    key: str,
    default: int | None = None,
) -> tuple[int | None, ErrorResponseDTO | None]:
    """옵셔널 정수 인자를 느슨한 규칙(문자열 숫자 허용)으로 파싱한다."""
    raw = arguments.get(key, default)
    if raw is None:
        return None, None
    if isinstance(raw, int):
        return raw, None
    if isinstance(raw, str):
        text = raw.strip()
        if text == "":
            return None, None
        try:
            return int(text), None
        except ValueError:
            return None, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} must be integer")
    return None, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} must be integer")


def parse_non_empty_string(arguments: dict[str, object], key: str) -> tuple[str, ErrorResponseDTO | None]:
    """필수 문자열 인자를 파싱한다."""
    raw = arguments.get(key)
    if not isinstance(raw, str) or raw.strip() == "":
        return "", ErrorResponseDTO(code=f"ERR_{key.upper()}_REQUIRED", message=f"{key} is required")
    return raw.strip(), None


def parse_optional_string(arguments: dict[str, object], key: str) -> str | None:
    """옵셔널 문자열 인자를 파싱한다."""
    raw = arguments.get(key)
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if value == "":
        return None
    return value


def parse_boolean(arguments: dict[str, object], key: str, default: bool = False) -> tuple[bool, ErrorResponseDTO | None]:
    """불리언/문자열 불리언 인자를 파싱한다."""
    raw = arguments.get(key, default)
    if isinstance(raw, bool):
        return raw, None
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True, None
        if normalized in {"0", "false", "no", "off"}:
            return False, None
    return False, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} must be boolean")


def parse_optional_boolean(arguments: dict[str, object], key: str) -> tuple[bool | None, ErrorResponseDTO | None]:
    """옵셔널 불리언 인자를 파싱한다."""
    raw = arguments.get(key)
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return raw, None
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized == "":
            return None, None
        if normalized in {"1", "true", "yes", "on"}:
            return True, None
        if normalized in {"0", "false", "no", "off"}:
            return False, None
    return None, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} must be boolean")


def parse_string_list(
    arguments: dict[str, object],
    key: str,
) -> tuple[tuple[str, ...] | None, ErrorResponseDTO | None]:
    """문자열 또는 문자열 배열을 문자열 튜플로 변환한다."""
    raw = arguments.get(key)
    if raw is None:
        return None, None
    if isinstance(raw, str):
        tokens = [token.strip() for token in raw.split(",") if token.strip() != ""]
        return (None if len(tokens) == 0 else tuple(tokens)), None
    if isinstance(raw, list):
        parsed: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                return None, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} items must be string")
            token = item.strip()
            if token != "":
                parsed.append(token)
        return (None if len(parsed) == 0 else tuple(parsed)), None
    return None, ErrorResponseDTO(code=f"ERR_INVALID_{key.upper()}", message=f"{key} must be string or string[]")
