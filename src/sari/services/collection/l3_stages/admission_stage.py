"""L5 admission evaluation stage for L3 orchestration."""

from __future__ import annotations

from typing import Callable

from sari.core.models import FileEnrichJobDTO, L4AdmissionDecisionDTO


class L3AdmissionStage:
    """Wrap admission evaluation callback and enforcement toggle."""

    def __init__(
        self,
        *,
        evaluate_l5_admission: Callable[[FileEnrichJobDTO, str], L4AdmissionDecisionDTO | None] | None,
        enforced: bool,
    ) -> None:
        self._evaluate_l5_admission = evaluate_l5_admission
        self._enforced = bool(enforced)

    def set_mode(
        self,
        *,
        evaluate_l5_admission: Callable[[FileEnrichJobDTO, str], L4AdmissionDecisionDTO | None] | None,
        enforced: bool,
    ) -> None:
        self._evaluate_l5_admission = evaluate_l5_admission
        self._enforced = bool(enforced)

    def evaluate(self, *, job: FileEnrichJobDTO, language: str) -> L4AdmissionDecisionDTO | None:
        if self._evaluate_l5_admission is None:
            return None
        return self._evaluate_l5_admission(job, language)

    @property
    def enforced(self) -> bool:
        return self._enforced

