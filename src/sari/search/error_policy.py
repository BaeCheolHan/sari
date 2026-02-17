"""검색 오류 승격 정책을 정의한다."""

from __future__ import annotations

from sari.core.models import SearchErrorDTO

SEARCH_ERROR_CLIENT = "CLIENT"
SEARCH_ERROR_FATAL = "FATAL"

_CLIENT_ERROR_CODES = {
    "ERR_REPO_REQUIRED",
    "ERR_QUERY_REQUIRED",
    "ERR_INVALID_LIMIT",
}


def classify_search_error(code: str) -> str:
    """오류 코드를 CLIENT/FATAL 중 하나로 분류한다."""
    if code in _CLIENT_ERROR_CODES:
        return SEARCH_ERROR_CLIENT
    return SEARCH_ERROR_FATAL


def has_fatal_errors(errors: list[SearchErrorDTO]) -> bool:
    """오류 목록에 FATAL이 포함됐는지 반환한다."""
    for error in errors:
        if error.severity == SEARCH_ERROR_FATAL:
            return True
    return False
