from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3_error_handling_service import L3ErrorHandlingService


def _job(*, scope_level: str | None = "module", scope_attempts: int = 0) -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="module/src/a.py",
        content_hash="h1",
        priority=100,
        enqueue_source="scan",
        status="pending",
        attempt_count=1,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        scope_level=scope_level,
        scope_attempts=scope_attempts,
    )


@dataclass
class _FakeErrorPolicy:
    events: list[dict[str, object]]

    def record_error_event(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class _FakeQueueRepo:
    def __init__(self) -> None:
        self.escalated: list[dict[str, object]] = []
        self.deferred: list[dict[str, object]] = []

    def escalate_scope_on_same_job(self, **kwargs: object) -> bool:
        self.escalated.append(kwargs)
        return True

    def defer_jobs_to_pending(self, **kwargs: object) -> int:
        self.deferred.append(kwargs)
        return 1


def test_l3_error_handling_scope_escalation_updates_job() -> None:
    queue_repo = _FakeQueueRepo()
    error_policy = _FakeErrorPolicy(events=[])
    service = L3ErrorHandlingService(
        queue_repo=queue_repo,
        error_policy=error_policy,
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
    )

    updated = service.try_escalate_scope_after_l3_extract_error(
        job=_job(scope_level="module", scope_attempts=0),
        error_message="[ERR_LSP_FILE_NOT_IN_SCOPE] out of scope",
    )

    assert updated is True
    assert len(queue_repo.escalated) == 1
    assert queue_repo.escalated[0]["next_scope_level"] == "repo"
    assert len(error_policy.events) == 1
    assert error_policy.events[0]["error_code"] == "ERR_L3_SCOPE_ESCALATED"


def test_l3_error_handling_broker_defer_updates_queue() -> None:
    queue_repo = _FakeQueueRepo()
    error_policy = _FakeErrorPolicy(events=[])
    service = L3ErrorHandlingService(
        queue_repo=queue_repo,
        error_policy=error_policy,
        now_iso_supplier=lambda: "2026-01-01T00:00:00+00:00",
    )

    updated = service.try_defer_after_broker_lease_denial(
        job=_job(),
        error_message="ERR_LSP_BROKER_LEASE_REQUIRED reason=cooldown",
    )

    assert updated is True
    assert len(queue_repo.deferred) == 1
    assert queue_repo.deferred[0]["defer_reason"] == "broker_defer:cooldown"
    assert len(error_policy.events) == 1
    assert error_policy.events[0]["error_code"] == "ERR_L3_DEFERRED_BY_BROKER"
