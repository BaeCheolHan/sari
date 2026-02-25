"""pack1 envelope 조립을 중앙화한 공통 빌더."""

from __future__ import annotations

from sari.core.models import ErrorResponseDTO
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success


class Pack1EnvelopeBuilder:
    """MCP tool 응답(pack1)의 공통 조립기."""

    def build_success(
        self,
        *,
        items: list[dict[str, object]],
        candidate_count: int,
        resolved_count: int,
        cache_hit: bool | None,
        errors: list[dict[str, object]] | None = None,
        stabilization: dict[str, object] | None = None,
        warnings: list[dict[str, object]] | None = None,
        meta_extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """성공 응답을 생성한다."""
        meta = Pack1MetaDTO(
            candidate_count=candidate_count,
            resolved_count=resolved_count,
            cache_hit=cache_hit,
            errors=errors if errors is not None else [],
            stabilization=stabilization,
            warnings=warnings,
        ).to_dict()
        if meta_extra is not None and len(meta_extra) > 0:
            meta.update(meta_extra)
        return pack1_success({"items": items, "meta": meta})

    def build_error(
        self,
        *,
        error: ErrorResponseDTO,
        detailed_errors: list[dict[str, object]] | None = None,
        stabilization: dict[str, object] | None = None,
        recovery_hint: str | None = None,
        expected: list[str] | None = None,
        received: list[str] | None = None,
        example: dict[str, object] | None = None,
        normalized_from: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """오류 응답을 생성한다."""
        return pack1_error(
            error=error,
            detailed_errors=detailed_errors,
            stabilization=stabilization,
            recovery_hint=recovery_hint,
            expected=expected,
            received=received,
            example=example,
            normalized_from=normalized_from,
        )

