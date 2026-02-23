"""L5 admission reject의 DONE/defer 분리 계약을 검증한다."""

from __future__ import annotations

from sari.core.models import (
    FileEnrichJobDTO,
    L4AdmissionDecisionDTO,
    L5ReasonCode,
    L5RejectReason,
)
from sari.services.collection.l3_orchestrator import L3Orchestrator
from sari.services.collection.l3_persist_service import L3PersistService
from sari.services.collection.l3_queue_transition_service import L3QueueTransitionService
from sari.services.collection.l3_scope_resolution_service import L3ScopeResolutionService
from sari.services.collection.l3_skip_eligibility_service import L3SkipEligibilityService
from sari.services.collection.l3_treesitter_preprocess_service import L3PreprocessDecision, L3PreprocessResultDTO


class _StubFileRow:
    def __init__(self) -> None:
        self.is_deleted = False
        self.content_hash = "h1"


class _StubFileRepo:
    def get_file(self, repo_root: str, relative_path: str):  # noqa: ANN001
        _ = (repo_root, relative_path)
        return _StubFileRow()


class _StubQueueRepo:
    def __init__(self) -> None:
        self.defer_calls: list[dict[str, object]] = []

    def defer_jobs_to_pending(self, *, job_ids: list[str], next_retry_at: str, defer_reason: str, now_iso: str) -> int:
        self.defer_calls.append(
            {
                "job_ids": list(job_ids),
                "next_retry_at": next_retry_at,
                "defer_reason": defer_reason,
                "now_iso": now_iso,
            }
        )
        return len(job_ids)


class _StubErrorPolicy:
    def __init__(self) -> None:
        self.events: list[str] = []

    def record_error_event(self, **kwargs) -> None:  # noqa: ANN003
        self.events.append(str(kwargs.get("error_code")))


class _StubL3BrokerAdmission:
    def is_broker_lease_denial(self, error_message: str) -> bool:
        _ = error_message
        return False

    def extract_lease_reason(self, error_message: str) -> str:
        _ = error_message
        return "budget"

    def map_defer_reason(self, lease_reason: str) -> str:
        _ = lease_reason
        return "broker_defer:budget"

    def defer_delay_seconds(self, lease_reason: str) -> int:
        _ = lease_reason
        return 30


class _NoopLspBackend:
    def extract(self, repo_root: str, relative_path: str, content_hash: str):  # noqa: ANN001
        raise AssertionError("extract should not be called on admission defer")


class _NoopQueueTransition:
    def __init__(self) -> None:
        self.defer_calls: list[str] = []

    def defer_after_l5_admission_rejection(self, *, job: FileEnrichJobDTO, admission: L4AdmissionDecisionDTO) -> bool:
        self.defer_calls.append(job.job_id)
        _ = admission
        return True

    def defer_after_preprocess_heavy(self, *, job: FileEnrichJobDTO, reason: str) -> bool:
        _ = reason
        self.defer_calls.append(job.job_id)
        return True

    def defer_after_broker_lease_denial(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        _ = (job, error_message)
        return False

    def escalate_scope_after_l3_extract_error(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        _ = (job, error_message)
        return False


def _job() -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-02-23T00:00:00+00:00",
        created_at="2026-02-23T00:00:00+00:00",
        updated_at="2026-02-23T00:00:00+00:00",
    )


def test_l3_queue_transition_defers_on_pressure_reject_reason() -> None:
    """pressure 계열 reject는 DONE이 아니라 queue defer로 전환되어야 한다."""
    queue_repo = _StubQueueRepo()
    error_policy = _StubErrorPolicy()
    service = L3QueueTransitionService(
        queue_repo=queue_repo,
        error_policy=error_policy,
        now_iso_supplier=lambda: "2026-02-23T00:00:00+00:00",
        broker_admission=_StubL3BrokerAdmission(),
        extract_error_code=lambda _message: "ERR_X",
        is_scope_escalation_trigger=lambda _code, _message: False,
        next_scope_level_for_escalation=lambda _scope: None,
    )
    decision = L4AdmissionDecisionDTO(
        admit_l5=False,
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        reject_reason=L5RejectReason.PRESSURE_BURST_EXCEEDED,
    )

    changed = service.defer_after_l5_admission_rejection(job=_job(), admission=decision)

    assert changed is True
    assert len(queue_repo.defer_calls) == 1
    assert queue_repo.defer_calls[0]["defer_reason"] == "l5_defer:pressure_burst_exceeded"


def test_l3_queue_transition_defers_on_preprocess_deferred_heavy() -> None:
    """DEFERRED_HEAVY 전처리는 queue defer로 전환되어야 한다."""
    queue_repo = _StubQueueRepo()
    error_policy = _StubErrorPolicy()
    service = L3QueueTransitionService(
        queue_repo=queue_repo,
        error_policy=error_policy,
        now_iso_supplier=lambda: "2026-02-23T00:00:00+00:00",
        broker_admission=_StubL3BrokerAdmission(),
        extract_error_code=lambda _message: "ERR_X",
        is_scope_escalation_trigger=lambda _code, _message: False,
        next_scope_level_for_escalation=lambda _scope: None,
    )

    changed = service.defer_after_preprocess_heavy(job=_job(), reason="l3_preprocess_large_file")

    assert changed is True
    assert len(queue_repo.defer_calls) == 1
    assert str(queue_repo.defer_calls[0]["defer_reason"]).startswith("l5_defer:deferred_heavy:")


def test_l3_orchestrator_marks_pending_when_admission_reject_is_deferred() -> None:
    """L3 admission reject(defer 대상)는 DONE이 아니라 PENDING으로 반환되어야 한다."""
    queue_transition = _NoopQueueTransition()
    skip = L3SkipEligibilityService(
        is_recent_tool_ready=lambda _job: False,
        resolve_l3_skip_reason=lambda _job: None,
        build_l3_skipped_readiness=lambda _job, _reason, _now_iso: None,  # type: ignore[return-value]
    )
    orchestrator = L3Orchestrator(
        file_repo=_StubFileRepo(),
        lsp_backend=_NoopLspBackend(),
        policy=type("P", (), {"retry_max_attempts": 3, "retry_backoff_base_sec": 1})(),
        error_policy=_StubErrorPolicy(),
        run_mode="prod",
        event_repo=None,
        deletion_hold_enabled=lambda: False,
        now_iso_supplier=lambda: "2026-02-23T00:00:00+00:00",
        record_enrich_latency=lambda _ms: None,
        result_builder=lambda **kwargs: kwargs,
        classify_failure_kind=lambda _msg: "TRANSIENT",
        schedule_l1_probe_after_l3_fallback=lambda _job: None,
        scope_resolution=L3ScopeResolutionService(),
        queue_transition=queue_transition,
        skip_eligibility=skip,
        persist_service=L3PersistService(record_scope_learning=lambda _job: None),
        evaluate_l5_admission=lambda _job, _lang: L4AdmissionDecisionDTO(
            admit_l5=False,
            reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
            reject_reason=L5RejectReason.PRESSURE_RATE_EXCEEDED,
        ),
        l5_admission_enforced=True,
    )

    result = orchestrator.process_job(_job())

    assert result["finished_status"] == "PENDING"
    assert result["done_id"] is None
    assert len(queue_transition.defer_calls) == 1


def test_l3_orchestrator_preprocess_skip_finishes_without_lsp() -> None:
    """refactored orchestrator는 전처리 skip 신호가 있으면 LSP 없이 완료해야 한다."""
    skip = L3SkipEligibilityService(
        is_recent_tool_ready=lambda _job: False,
        resolve_l3_skip_reason=lambda _job: None,
        build_l3_skipped_readiness=lambda _job, _reason, _now_iso: None,  # type: ignore[return-value]
    )

    class _StubPreprocess:
        def preprocess(self, *, relative_path: str, content_text: str, max_bytes: int = 0) -> L3PreprocessResultDTO:
            _ = (relative_path, content_text, max_bytes)
            return L3PreprocessResultDTO(
                symbols=[{"name": "alpha", "kind": "function", "line": 1, "end_line": 1}],
                degraded=False,
                decision=L3PreprocessDecision.L3_ONLY,
                source="tree_sitter",
                reason="l3_preprocess_only",
            )

    orchestrator = L3Orchestrator(
        file_repo=_StubFileRepo(),
        lsp_backend=_NoopLspBackend(),
        policy=type("P", (), {"retry_max_attempts": 3, "retry_backoff_base_sec": 1})(),
        error_policy=_StubErrorPolicy(),
        run_mode="prod",
        event_repo=None,
        deletion_hold_enabled=lambda: False,
        now_iso_supplier=lambda: "2026-02-23T00:00:00+00:00",
        record_enrich_latency=lambda _ms: None,
        result_builder=lambda **kwargs: kwargs,
        classify_failure_kind=lambda _msg: "TRANSIENT",
        schedule_l1_probe_after_l3_fallback=lambda _job: None,
        scope_resolution=L3ScopeResolutionService(),
        queue_transition=_NoopQueueTransition(),
        skip_eligibility=skip,
        persist_service=L3PersistService(record_scope_learning=lambda _job: None),
        preprocess_service=_StubPreprocess(),
        degraded_fallback_service=None,
        preprocess_max_bytes=1024,
    )

    result = orchestrator.process_job(_job())

    assert result["finished_status"] == "DONE"
    assert result["failure_update"] is None
    assert result["lsp_update"] is not None
    assert len(result["lsp_update"].symbols) == 1
