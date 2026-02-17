"""파이프라인 자동 제어 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.schema import init_schema
from sari.services.pipeline_control_service import PipelineControlService


def test_pipeline_auto_control_enables_hold_on_warn_and_releases_on_ok(tmp_path: Path) -> None:
    """자동제어 tick은 경고 시 hold를 켜고 정상 시 해제해야 한다."""
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

    service.set_auto_hold_enabled(True)
    state = service.get_auto_control_state()
    assert state.auto_hold_enabled is True
    assert state.auto_hold_active is False

    now_iso = "2026-02-16T10:00:00+00:00"
    event_repo.record_event("j1", "DEAD", 120, now_iso)
    event_repo.record_event("j2", "DONE", 150, now_iso)
    action = service.evaluate_auto_hold(now_iso=now_iso)
    assert action["action"] == "HOLD_ENABLED"
    assert service.get_policy().deletion_hold is True
    assert service.get_auto_control_state().auto_hold_active is True

    later_iso = "2026-02-16T10:10:00+00:00"
    release_action = service.evaluate_auto_hold(now_iso=later_iso)
    assert release_action["action"] == "HOLD_RELEASED"
    assert service.get_policy().deletion_hold is False
    assert service.get_auto_control_state().auto_hold_active is False
