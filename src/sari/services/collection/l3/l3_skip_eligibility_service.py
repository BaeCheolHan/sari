"""L3 skip/recent-success 판정 서비스."""

from __future__ import annotations

from typing import Callable

from sari.core.models import FileEnrichJobDTO, ToolReadinessStateDTO


class L3SkipEligibilityService:
    """L3 스킵 판정 책임을 분리한다."""

    def __init__(
        self,
        *,
        is_recent_tool_ready: Callable[[FileEnrichJobDTO], bool],
        resolve_l3_skip_reason: Callable[[FileEnrichJobDTO], str | None],
        build_l3_skipped_readiness: Callable[[FileEnrichJobDTO, str, str], ToolReadinessStateDTO],
        is_recent_l5_ready: Callable[[FileEnrichJobDTO], bool] | None = None,
    ) -> None:
        self._is_recent_tool_ready = is_recent_tool_ready
        self._resolve_l3_skip_reason = resolve_l3_skip_reason
        self._build_l3_skipped_readiness = build_l3_skipped_readiness
        self._is_recent_l5_ready = is_recent_l5_ready or (lambda _: False)

    def is_recent_tool_ready(self, job: FileEnrichJobDTO) -> bool:
        return self._is_recent_tool_ready(job)

    def is_recent_l5_ready(self, job: FileEnrichJobDTO) -> bool:
        return bool(self._is_recent_l5_ready(job))

    def resolve_skip_reason(self, job: FileEnrichJobDTO) -> str | None:
        return self._resolve_l3_skip_reason(job)

    def build_skipped_readiness(self, *, job: FileEnrichJobDTO, reason: str, now_iso: str) -> ToolReadinessStateDTO:
        return self._build_l3_skipped_readiness(job, reason, now_iso)

