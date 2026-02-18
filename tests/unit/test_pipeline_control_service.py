"""파이프라인 운영 제어 서비스 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.schema import init_schema
from sari.services.pipeline_control_service import PipelineControlService


def test_pipeline_alert_status_computes_p95_and_dead_ratio(tmp_path: Path) -> None:
    """알람 스냅샷은 p95 지연과 DEAD 비율을 계산해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    policy_repo = PipelinePolicyRepository(db_path)
    event_repo = PipelineJobEventRepository(db_path)
    queue_repo = FileEnrichQueueRepository(db_path)
    state_repo = PipelineControlStateRepository(db_path)
    service = PipelineControlService(
        policy_repo=policy_repo,
        event_repo=event_repo,
        queue_repo=queue_repo,
        control_state_repo=state_repo,
    )

    now_iso = "2026-02-16T10:00:00+00:00"
    queue_repo.enqueue("/repo", "a.py", "h1", 30, "scan", now_iso)
    queue_repo.enqueue("/repo", "b.py", "h2", 30, "scan", now_iso)
    queue_repo.enqueue("/repo", "c.py", "h3", 30, "scan", now_iso)

    event_repo.record_event("j1", "DONE", 120, now_iso)
    event_repo.record_event("j2", "DONE", 180, now_iso)
    event_repo.record_event("j3", "DEAD", 150, now_iso)

    snapshot = service.get_alert_status(now_iso=now_iso)

    assert snapshot.window_seconds == 300
    assert snapshot.event_count == 3
    assert snapshot.dead_count == 1
    assert snapshot.l3_p95_ms >= 150
    assert snapshot.dead_ratio_bps > 0
    assert snapshot.policy.dead_ratio_threshold_bps == 10


def test_pipeline_dead_job_actions_requeue_and_purge(tmp_path: Path) -> None:
    """DEAD 작업은 재큐잉과 폐기가 가능해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    policy_repo = PipelinePolicyRepository(db_path)
    event_repo = PipelineJobEventRepository(db_path)
    queue_repo = FileEnrichQueueRepository(db_path)
    state_repo = PipelineControlStateRepository(db_path)
    service = PipelineControlService(
        policy_repo=policy_repo,
        event_repo=event_repo,
        queue_repo=queue_repo,
        control_state_repo=state_repo,
    )

    now_iso = "2026-02-16T10:00:00+00:00"
    job_id = queue_repo.enqueue("/repo", "x.py", "hx", 30, "scan", now_iso)
    queue_repo.mark_failed_with_backoff(job_id, "e1", now_iso, dead_threshold=1, backoff_base_sec=1)

    dead_items = service.list_dead_jobs(repo_root="/repo", limit=10)
    assert len(dead_items) == 1

    requeue_result = service.requeue_dead_jobs(repo_root="/repo", limit=10, now_iso=now_iso)
    assert requeue_result.requeued_count == 1
    assert requeue_result.repo_scope == "repo"
    assert requeue_result.executed_at == now_iso
    assert isinstance(requeue_result.queue_snapshot, dict)
    assert "FAILED" in requeue_result.queue_snapshot

    queue_repo.mark_failed_with_backoff(job_id, "e2", now_iso, dead_threshold=1, backoff_base_sec=1)
    purge_result = service.purge_dead_jobs(repo_root="/repo", limit=10)
    assert purge_result.purged_count == 1
    assert purge_result.repo_scope == "repo"
    assert isinstance(purge_result.executed_at, str)
    assert isinstance(purge_result.queue_snapshot, dict)


def test_pipeline_dead_job_actions_support_all_scope(tmp_path: Path) -> None:
    """all=true 경로는 limit 없이 DEAD 작업 전체를 처리해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    policy_repo = PipelinePolicyRepository(db_path)
    event_repo = PipelineJobEventRepository(db_path)
    queue_repo = FileEnrichQueueRepository(db_path)
    state_repo = PipelineControlStateRepository(db_path)
    service = PipelineControlService(
        policy_repo=policy_repo,
        event_repo=event_repo,
        queue_repo=queue_repo,
        control_state_repo=state_repo,
    )

    now_iso = "2026-02-16T10:00:00+00:00"
    for index in range(3):
        job_id = queue_repo.enqueue("/repo", f"x{index}.py", f"h{index}", 30, "scan", now_iso)
        queue_repo.mark_failed_with_backoff(job_id, "e1", now_iso, dead_threshold=1, backoff_base_sec=1)

    requeue_result = service.requeue_dead_jobs(repo_root="/repo", limit=1, now_iso=now_iso, all_scopes=True)
    assert requeue_result.requeued_count == 3
    assert requeue_result.repo_scope == "all"

    for index in range(3):
        job_id = queue_repo.enqueue("/repo", f"y{index}.py", f"hy{index}", 30, "scan", now_iso)
        queue_repo.mark_failed_with_backoff(job_id, "e2", now_iso, dead_threshold=1, backoff_base_sec=1)
    purge_result = service.purge_dead_jobs(repo_root="/repo", limit=1, all_scopes=True)
    assert purge_result.purged_count == 3
    assert purge_result.repo_scope == "all"
