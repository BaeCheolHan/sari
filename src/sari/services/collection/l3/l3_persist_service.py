"""L3 persistence/scope-learning 보조 서비스."""

from __future__ import annotations

from typing import Callable

from sari.core.models import FileEnrichJobDTO


class L3PersistService:
    """L3 성공 후 학습성 side-effect를 분리한다."""

    def __init__(self, *, record_scope_learning: Callable[[FileEnrichJobDTO], None]) -> None:
        self._record_scope_learning = record_scope_learning

    def record_scope_learning(self, job: FileEnrichJobDTO) -> None:
        self._record_scope_learning(job)

