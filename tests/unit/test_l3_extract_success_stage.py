from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO, L4AdmissionDecisionDTO, L5ReasonCode
from sari.services.collection.l3.l3_job_context import L3JobContext
from sari.services.collection.l3.stages.extract_success_stage import L3ExtractSuccessStage


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


@dataclass
class _PersistStage:
    called: int = 0
    reason_codes: list[L5ReasonCode] | None = None

    def apply_l5_success(self, **kwargs: object) -> None:
        self.called += 1
        if self.reason_codes is None:
            self.reason_codes = []
        self.reason_codes.append(kwargs["reason_code"])  # type: ignore[index]


@dataclass
class _Extraction:
    symbols: list[dict[str, object]]
    relations: list[dict[str, object]]


def test_extract_success_stage_persists_and_marks_done() -> None:
    persist = _PersistStage()
    shadow_calls: list[dict[str, object]] = []
    stage = L3ExtractSuccessStage(
        persist_stage=persist,
        record_quality_shadow_compare=lambda **kwargs: shadow_calls.append(kwargs),
    )
    status = stage.handle_success(
        context=L3JobContext(),
        job=_job(),
        language="python",
        preprocess_result=None,
        admission_decision=None,
        extraction=_Extraction(symbols=[{"name": "a"}], relations=[]),
        now_iso="2026-01-01T00:00:00Z",
    )
    assert status == "DONE"
    assert persist.called == 1
    assert persist.reason_codes == [L5ReasonCode.GOLDENSET_COVERAGE]
    assert len(shadow_calls) == 1


def test_extract_success_stage_uses_admission_reason_when_present() -> None:
    persist = _PersistStage()
    stage = L3ExtractSuccessStage(
        persist_stage=persist,
        record_quality_shadow_compare=lambda **kwargs: None,
    )
    decision = L4AdmissionDecisionDTO(
        admit_l5=True,
        reason_code=L5ReasonCode.UNRESOLVED_SYMBOL,
        reject_reason=None,
    )
    _ = stage.handle_success(
        context=L3JobContext(),
        job=_job(),
        language="python",
        preprocess_result=None,
        admission_decision=decision,
        extraction=_Extraction(symbols=[], relations=[]),
        now_iso="2026-01-01T00:00:00Z",
    )
    assert persist.reason_codes == [L5ReasonCode.UNRESOLVED_SYMBOL]
