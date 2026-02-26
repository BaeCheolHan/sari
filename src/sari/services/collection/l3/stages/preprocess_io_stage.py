"""L3 preprocess 파일 I/O + fallback 처리(stage)."""

from __future__ import annotations

import logging

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)

log = logging.getLogger(__name__)


class L3PreprocessIoStage:
    def __init__(
        self,
        *,
        preprocess_service: object | None,
        degraded_fallback_service: object | None,
        preprocess_max_bytes: int,
    ) -> None:
        self._preprocess_service = preprocess_service
        self._degraded_fallback_service = degraded_fallback_service
        self._preprocess_max_bytes = max(1, int(preprocess_max_bytes))

    def run(self, *, job: FileEnrichJobDTO, file_row: object) -> L3PreprocessResultDTO | None:
        preprocess_service = self._preprocess_service
        if preprocess_service is None:
            return None
        absolute_path = getattr(file_row, "absolute_path", None)
        try:
            if isinstance(absolute_path, str) and absolute_path.strip() != "":
                with open(absolute_path, "r", encoding="utf-8", errors="ignore") as handle:
                    content_text = handle.read()
            else:
                content_text = ""
            result = preprocess_service.preprocess(
                relative_path=job.relative_path,
                content_text=content_text,
                max_bytes=self._preprocess_max_bytes,
            )
            if (
                len(result.symbols) == 0
                and result.decision is not L3PreprocessDecision.DEFERRED_HEAVY
                and self._degraded_fallback_service is not None
            ):
                return self._degraded_fallback_service.fallback(
                    relative_path=job.relative_path,
                    content_text=content_text,
                )
            return result
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            log.warning(
                "L3 preprocess failed, returning explicit degraded NEEDS_L5 result (repo=%s, path=%s)",
                job.repo_root,
                job.relative_path,
                exc_info=True,
            )
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=True,
                decision=L3PreprocessDecision.NEEDS_L5,
                source="none",
                reason=f"l3_preprocess_exception:{type(exc).__name__}",
            )
