from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO, L4AdmissionDecisionDTO, L5ReasonCode, L5RejectReason
from sari.services.collection.l3.l3_job_context import L3JobContext
from sari.services.collection.l3.stages.decision_stage import L3DecisionStage
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)


def _job() -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="src/a.py",
        content_hash="h1",
        priority=100,
        enqueue_source="scan",
        status="pending",
        attempt_count=1,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


class _SkipEligibility:
    def __init__(self, *, recent: bool = False, reason: str | None = None) -> None:
        self._recent = recent
        self._reason = reason

    def is_recent_tool_ready(self, job: FileEnrichJobDTO) -> bool:
        _ = job
        return self._recent

    def resolve_skip_reason(self, job: FileEnrichJobDTO) -> str | None:
        _ = job
        return self._reason

    def build_skipped_readiness(self, *, job: FileEnrichJobDTO, reason: str, now_iso: str):
        _ = (job, now_iso)
        return {"reason": reason}


class _Scope:
    def resolve_language(self, relative_path: str) -> str:
        _ = relative_path
        return "python"


class _Admission:
    def __init__(self, decision: L4AdmissionDecisionDTO | None) -> None:
        self._decision = decision

    def evaluate(self, *, job: FileEnrichJobDTO, language: str) -> L4AdmissionDecisionDTO | None:
        _ = (job, language)
        return self._decision


@dataclass
class _QueueTransition:
    defer_l5: bool = False
    defer_heavy: bool = False

    def defer_after_l5_admission_rejection(self, *, job: FileEnrichJobDTO, admission: L4AdmissionDecisionDTO) -> bool:
        _ = (job, admission)
        return self.defer_l5

    def defer_after_preprocess_heavy(self, *, job: FileEnrichJobDTO, reason: str) -> bool:
        _ = (job, reason)
        return self.defer_heavy


@dataclass
class _Persist:
    recent_called: int = 0
    l3_only_called: int = 0

    def mark_recent_ready(self, **kwargs: object) -> None:
        _ = kwargs
        self.recent_called += 1

    def apply_l3_only_success(self, **kwargs: object) -> None:
        _ = kwargs
        self.l3_only_called += 1


def test_decision_stage_recent_ready_short_circuits_done() -> None:
    stage = L3DecisionStage(
        skip_eligibility=_SkipEligibility(recent=True),
        scope_resolution=_Scope(),
        admission_stage=_Admission(None),
        queue_transition=_QueueTransition(),
        persist_stage=_Persist(),
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        admission_enforced=False,
    )
    out = stage.evaluate(context=L3JobContext(), job=_job(), preprocess_result=None)
    assert out.finished_status == "DONE"
    assert out.should_extract is False


def test_decision_stage_allows_extract_when_admitted_and_not_l3_only() -> None:
    decision = L4AdmissionDecisionDTO(
        admit_l5=True,
        reason_code=L5ReasonCode.UNRESOLVED_SYMBOL,
        reject_reason=None,
    )
    stage = L3DecisionStage(
        skip_eligibility=_SkipEligibility(),
        scope_resolution=_Scope(),
        admission_stage=_Admission(decision),
        queue_transition=_QueueTransition(),
        persist_stage=_Persist(),
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        admission_enforced=True,
    )
    out = stage.evaluate(
        context=L3JobContext(),
        job=_job(),
        preprocess_result=L3PreprocessResultDTO(
            symbols=[],
            degraded=False,
            decision=L3PreprocessDecision.NEEDS_L5,
            source="tree_sitter",
            reason="needs_l5",
        ),
    )
    assert out.finished_status is None
    assert out.should_extract is True


def test_decision_stage_reject_without_enforce_marks_done_skip() -> None:
    decision = L4AdmissionDecisionDTO(
        admit_l5=False,
        reason_code=None,
        reject_reason=L5RejectReason.MODE_NOT_ALLOWED,
    )
    context = L3JobContext()
    stage = L3DecisionStage(
        skip_eligibility=_SkipEligibility(),
        scope_resolution=_Scope(),
        admission_stage=_Admission(decision),
        queue_transition=_QueueTransition(),
        persist_stage=_Persist(),
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        admission_enforced=False,
    )
    out = stage.evaluate(context=context, job=_job(), preprocess_result=None)
    assert out.finished_status == "DONE"
    assert out.should_extract is False
    assert context.done_id == "j1"
