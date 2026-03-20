"""Preprocess stage for L3 orchestrator."""

from __future__ import annotations

from typing import Callable

from sari.core.models import FileEnrichJobDTO

from ..l3_job_context import L3JobContext
from ..l3_treesitter_preprocess_service import L3PreprocessResultDTO


class L3PreprocessStage:
    """Thin stage wrapper to isolate preprocess invocation contract."""

    def __init__(self, *, run_preprocess: Callable[..., L3PreprocessResultDTO | None]) -> None:
        self._run_preprocess = run_preprocess

    def execute(self, *, job: FileEnrichJobDTO, file_row: object, context: L3JobContext | None = None) -> L3PreprocessResultDTO | None:
        return self._run_preprocess(job=job, file_row=file_row, context=context)
