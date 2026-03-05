from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_broker_admission_service import L3BrokerAdmissionService
from sari.services.collection.l3.l3_queue_transition_service import L3QueueTransitionService


def _job() -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="src/a.py",
        content_hash="h1",
        priority=100,
        enqueue_source="scan",
        status="RUNNING",
        attempt_count=1,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@dataclass
class _QueueRepoStub:
    deferred_calls: list[dict[str, object]]

    def defer_jobs_to_pending(self, **kwargs: object) -> int:
        self.deferred_calls.append(dict(kwargs))
        return 1


@dataclass
class _ErrorPolicyStub:
    events: list[dict[str, object]]

    def record_error_event(self, **kwargs: object) -> None:
        self.events.append(dict(kwargs))


def test_l3_queue_transition_defers_when_soft_limit_error_detected() -> None:
    queue_repo = _QueueRepoStub(deferred_calls=[])
    error_policy = _ErrorPolicyStub(events=[])
    service = L3QueueTransitionService(
        queue_repo=queue_repo,
        error_policy=error_policy,
        now_iso_supplier=lambda: "2026-01-01T00:00:00+00:00",
        broker_admission=L3BrokerAdmissionService(),
        extract_error_code=lambda message: "ERR_LSP_GLOBAL_SOFT_LIMIT" if "soft limit" in message else "ERR_UNKNOWN",
        is_scope_escalation_trigger=lambda code, message: False,
        next_scope_level_for_escalation=lambda level: None,
        min_defer_sec=5,
    )

    changed = service.defer_after_l3_extract_backpressure(
        job=_job(),
        error_message="LSP 전역 soft limit 도달: yaml@/workspace",
    )

    assert changed is True
    assert len(queue_repo.deferred_calls) == 1
    assert queue_repo.deferred_calls[0]["defer_reason"] == "l3_defer:lsp_backpressure"
    assert len(error_policy.events) == 1
    assert error_policy.events[0]["error_code"] == "ERR_L3_DEFERRED_BACKPRESSURE"

