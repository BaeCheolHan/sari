from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_job_context import L3JobContext
from sari.services.collection.l3.stages.exception_stage import L3ExceptionStage


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

    def mark_failure(self, **kwargs: object) -> None:
        _ = kwargs
        self.called += 1


def test_exception_stage_records_failure_and_event() -> None:
    events: list[dict[str, object]] = []
    persist = _PersistStage()
    stage = L3ExceptionStage(
        persist_stage=persist,
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        record_error_event=lambda **kwargs: events.append(kwargs),
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        run_mode="prod",
    )
    dev_error = stage.handle_exception(context=L3JobContext(), job=_job(), exc=RuntimeError("boom"))
    assert dev_error is None
    assert persist.called == 1
    assert len(events) == 1


def test_exception_stage_returns_dev_error_in_dev_mode() -> None:
    stage = L3ExceptionStage(
        persist_stage=_PersistStage(),
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        record_error_event=None,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        run_mode="dev",
    )
    dev_error = stage.handle_exception(context=L3JobContext(), job=_job(), exc=ValueError("bad"))
    assert dev_error is not None
