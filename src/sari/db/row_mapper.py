"""SQLite Row 엄격 매핑 유틸리티를 제공한다."""

from __future__ import annotations

from collections.abc import Mapping

from sari.core.exceptions import ErrorContext, ValidationError


def _mapping_error(message: str) -> ValidationError:
    """DB 매핑 오류를 표준 ValidationError로 생성한다."""
    return ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message=message))


def row_str(row: Mapping[str, object], field_name: str) -> str:
    """문자열 필드를 엄격하게 읽는다."""
    value = row[field_name]
    if not isinstance(value, str) or value == "":
        raise _mapping_error(f"{field_name} must be non-empty str")
    return value


def row_optional_str(row: Mapping[str, object], field_name: str) -> str | None:
    """nullable 문자열 필드를 엄격하게 읽는다."""
    value = row[field_name]
    if value is None:
        return None
    if not isinstance(value, str):
        raise _mapping_error(f"{field_name} must be str|null")
    return value


def row_int(row: Mapping[str, object], field_name: str) -> int:
    """정수 필드를 엄격하게 읽는다."""
    value = row[field_name]
    if not isinstance(value, int):
        raise _mapping_error(f"{field_name} must be int")
    return value


def row_bool(row: Mapping[str, object], field_name: str) -> bool:
    """불리언(정수 플래그 포함) 필드를 엄격하게 읽는다."""
    value = row[field_name]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    raise _mapping_error(f"{field_name} must be bool|int")


def row_bytes(row: Mapping[str, object], field_name: str) -> bytes:
    """바이트 필드를 엄격하게 읽는다."""
    value = row[field_name]
    if not isinstance(value, bytes):
        raise _mapping_error(f"{field_name} must be bytes")
    return value
