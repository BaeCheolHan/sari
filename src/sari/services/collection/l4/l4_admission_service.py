"""L4 admission 전용 서비스."""

from __future__ import annotations

import time

from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode, L5RequestMode
from sari.services.collection.l5_admission_policy import L5AdmissionInput, L5AdmissionPolicy, normalize_workspace_uid


class L4AdmissionService:
    """L4에서 L5 승격 여부를 판정한다."""

    def __init__(
        self,
        *,
        policy: L5AdmissionPolicy,
    ) -> None:
        self._policy = policy

    def evaluate_batch(
        self,
        *,
        repo_root: str,
        language_key: str,
        total_rate: float,
        batch_rate: float,
        cooldown_active: bool = False,
        reason_code: L5ReasonCode = L5ReasonCode.GOLDENSET_COVERAGE,
        caller: str = "enrich_engine",
        workload_kind: str = "INDEX_BUILD",
    ) -> L4AdmissionDecisionDTO:
        return self._policy.evaluate(
            admission=L5AdmissionInput(
                reason_code=reason_code,
                mode=L5RequestMode.BATCH,
                workspace_uid=normalize_workspace_uid(repo_root),
                total_rate=total_rate,
                batch_rate=batch_rate,
                cooldown_active=bool(cooldown_active),
                cost=1,
                now_ts=time.monotonic(),
                caller=caller,
                workload_kind=workload_kind,
            ),
            language_key=language_key,
        )
