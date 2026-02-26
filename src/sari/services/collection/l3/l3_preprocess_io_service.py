"""L3 preprocess의 파일 I/O + fallback 조합 책임을 분리한 서비스."""

from __future__ import annotations

import logging

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)

log = logging.getLogger(__name__)


class L3PreprocessIoService:
    """파일 읽기, preprocess 호출, degraded fallback을 일관되게 처리한다."""

    def __init__(self, *, preprocess_service: object, fallback_service: object | None) -> None:
        self._preprocess_service = preprocess_service
        self._fallback_service = fallback_service

    def run(
        self,
        *,
        job: FileEnrichJobDTO,
        file_row: object,
        max_bytes: int,
    ) -> L3PreprocessResultDTO | None:
        if self._preprocess_service is None:
            return None
        absolute_path = getattr(file_row, "absolute_path", None)
        try:
            if isinstance(absolute_path, str) and absolute_path.strip() != "":
                with open(absolute_path, "r", encoding="utf-8", errors="ignore") as handle:
                    content_text = handle.read()
            else:
                content_text = ""
            result = self._preprocess_service.preprocess(
                relative_path=job.relative_path,
                content_text=content_text,
                max_bytes=max_bytes,
            )
            if len(result.symbols) == 0 and result.decision is not L3PreprocessDecision.DEFERRED_HEAVY:
                fallback = self._fallback_service
                if fallback is not None:
                    return fallback.fallback(relative_path=job.relative_path, content_text=content_text)
            return result
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            log.warning(
                "L3 preprocess I/O service failed, returning explicit degraded NEEDS_L5 result (repo=%s, path=%s)",
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
