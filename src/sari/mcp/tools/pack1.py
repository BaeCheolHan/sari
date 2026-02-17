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
) -> dict[str, object]:
    """pack1 규격의 명시적 오류 응답을 생성한다."""
    errors_payload: list[dict[str, object]]
    if detailed_errors is None or len(detailed_errors) == 0:
        errors_payload = [{"code": error.code, "message": error.message}]
    else:
        errors_payload = detailed_errors
    return {
        "content": [{"type": "text", "text": error.message}],
        "structuredContent": {
            "items": [],
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
