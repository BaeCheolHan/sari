"""MCP pack1 공통 응답 포맷 유틸을 제공한다."""

from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import ErrorResponseDTO


@dataclass(frozen=True)
class Pack1MetaDTO:
    """pack1 meta 필드를 표현한다."""

    candidate_count: int
    resolved_count: int
    cache_hit: bool | None
    errors: list[dict[str, object]]
    stabilization: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        """JSON 직렬화 가능한 딕셔너리로 변환한다."""
        payload: dict[str, object] = {
            "candidate_count": self.candidate_count,
            "resolved_count": self.resolved_count,
            "cache_hit": self.cache_hit,
            "errors": self.errors,
        }
        if self.stabilization is not None:
            payload["stabilization"] = self.stabilization
        return payload


def pack1_error(
    error: ErrorResponseDTO,
    detailed_errors: list[dict[str, object]] | None = None,
    stabilization: dict[str, object] | None = None,
    recovery_hint: str | None = None,
    expected: list[str] | None = None,
    received: list[str] | None = None,
    example: dict[str, object] | None = None,
    normalized_from: dict[str, str] | None = None,
) -> dict[str, object]:
    """pack1 규격의 명시적 오류 응답을 생성한다."""
    errors_payload: list[dict[str, object]]
    if detailed_errors is None or len(detailed_errors) == 0:
        errors_payload = [{"code": error.code, "message": error.message}]
    else:
        errors_payload = detailed_errors
    error_payload: dict[str, object] = {"code": error.code, "message": error.message}
    if expected is not None and len(expected) > 0:
        error_payload["expected"] = expected
    if received is not None:
        error_payload["received"] = received
    if example is not None:
        error_payload["example"] = example
    if normalized_from is not None and len(normalized_from) > 0:
        error_payload["normalized_from"] = normalized_from
    if recovery_hint is not None and recovery_hint.strip() != "":
        error_payload["recovery_hint"] = recovery_hint
    if recovery_hint is not None and recovery_hint.strip() != "":
        for item in errors_payload:
            if isinstance(item, dict):
                item["recovery_hint"] = recovery_hint
    for item in errors_payload:
        if not isinstance(item, dict):
            continue
        if expected is not None and len(expected) > 0:
            item["expected"] = expected
        if received is not None:
            item["received"] = received
        if example is not None:
            item["example"] = example
        if normalized_from is not None and len(normalized_from) > 0:
            item["normalized_from"] = normalized_from
    return {
        "content": [{"type": "text", "text": error.message}],
        "structuredContent": {
            "items": [],
            "error": error_payload,
            "meta": Pack1MetaDTO(
                candidate_count=0,
                resolved_count=0,
                cache_hit=None,
                errors=errors_payload,
                stabilization=stabilization,
            ).to_dict(),
        },
        "isError": True,
    }


def pack1_success(structured_content: dict[str, object]) -> dict[str, object]:
    """pack1 규격의 성공 응답을 생성한다."""
    return {
        "content": [{"type": "text", "text": "ok"}],
        "structuredContent": structured_content,
        "isError": False,
    }
