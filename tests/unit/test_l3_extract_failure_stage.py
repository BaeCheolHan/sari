from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3_job_context import L3JobContext
from sari.services.collection.l3_stages.extract_failure_stage import L3ExtractFailureStage


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


class _QueueTransition:
    def __init__(self, *, defer: bool = False, escalate: bool = False) -> None:
        self._defer = defer
        self._escalate = escalate

    def defer_after_broker_lease_denial(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        _ = (job, error_message)
        return self._defer

    def escalate_scope_after_l3_extract_error(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        _ = (job, error_message)
        return self._escalate


@dataclass
class _PersistStage:
    called: int = 0

    def mark_failure(self, **kwargs: object) -> None:
        _ = kwargs
        self.called += 1


def test_extract_failure_stage_returns_pending_when_deferred() -> None:
    stage = L3ExtractFailureStage(
        queue_transition=_QueueTransition(defer=True),
        persist_stage=_PersistStage(),
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        record_error_event=None,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
    )
    status = stage.handle_extract_error(context=L3JobContext(), job=_job(), error_message="ERR")
    assert status == "PENDING"


def test_extract_failure_stage_marks_failure_when_not_deferred_or_escalated() -> None:
    persist = _PersistStage()
    events: list[dict[str, object]] = []
    stage = L3ExtractFailureStage(
        queue_transition=_QueueTransition(defer=False, escalate=False),
        persist_stage=persist,
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        record_error_event=lambda **kwargs: events.append(kwargs),
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
    )
    status = stage.handle_extract_error(context=L3JobContext(), job=_job(), error_message="ERR")
    assert status == "FAILED"
    assert persist.called == 1
    assert len(events) == 1
