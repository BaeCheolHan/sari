"""파이프라인 오류 상세 이벤트 저장소를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.schema import init_schema


def test_pipeline_error_event_repository_record_list_get_and_prune(tmp_path: Path) -> None:
    """오류 이벤트 저장/조회/정리 동작이 일관되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = PipelineErrorEventRepository(db_path)

    first_id = repo.record_event(
        occurred_at="2026-02-15T00:00:00+00:00",
        component="file_collection_service",
        phase="enrich_job",
        severity="error",
        repo_root="/repo",
        relative_path="a.py",
        job_id="job-1",
        attempt_count=1,
        error_code="ERR_ENRICH_JOB_FAILED",
        error_message="failed 1",
        error_type="RuntimeError",
        stacktrace_text="trace-1",
        context_data={"k": "v1"},
        worker_name="enrich_worker",
        run_mode="prod",
    )
    second_id = repo.record_event(
        occurred_at="2026-02-16T00:00:00+00:00",
        component="file_collection_service",
        phase="scheduler_scan",
        severity="critical",
        repo_root="/repo",
        relative_path="b.py",
        job_id="job-2",
        attempt_count=2,
        error_code="ERR_SCAN_FAILED",
        error_message="failed 2",
        error_type="CollectionError",
        stacktrace_text="trace-2",
        context_data={"k": "v2"},
        worker_name="scheduler",
        run_mode="dev",
    )
    assert first_id != second_id

    items = repo.list_events(limit=10)
    assert len(items) == 2
    assert items[0].event_id == second_id
    assert items[1].event_id == first_id

    detail = repo.get_event(first_id)
    assert detail is not None
    assert detail.error_code == "ERR_ENRICH_JOB_FAILED"
    assert detail.run_mode == "prod"
    assert detail.scope_type == "REPO"

    deleted = repo.prune(cutoff_iso="2026-02-15T12:00:00+00:00", max_rows=200_000)
    assert deleted == 1
    remained = repo.list_events(limit=10)
    assert len(remained) == 1
    assert remained[0].event_id == second_id
    assert remained[0].scope_type == "REPO"
