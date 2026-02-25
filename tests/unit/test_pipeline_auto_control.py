"""파이프라인 자동 제어 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.schema import init_schema
from sari.services.pipeline.control_service import PipelineControlService


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


def test_pipeline_stage_rollout_applies_runtime_toggles(tmp_path: Path) -> None:
    """Stage exit 결과에 따라 런타임 토글을 적용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    policy_repo = PipelinePolicyRepository(db_path)
    event_repo = PipelineJobEventRepository(db_path)
    queue_repo = FileEnrichQueueRepository(db_path)
    state_repo = PipelineControlStateRepository(db_path)

    l5_calls: list[tuple[bool, bool]] = []
    resolve_calls: list[bool] = []
    service = PipelineControlService(
        policy_repo=policy_repo,
        event_repo=event_repo,
        queue_repo=queue_repo,
        control_state_repo=state_repo,
        set_l5_admission_mode=lambda shadow_enabled, enforced: l5_calls.append((shadow_enabled, enforced)),
        set_search_resolve_symbols_default=lambda enabled: resolve_calls.append(enabled),
    )

    action = service.evaluate_stage_rollout(
        summary={
            "datasets": [
                {
                    "dataset_type": "workspace_real",
                    "integrity": {
                        "stage_exit": {
                            "stage_a_to_b": {"passed": True},
                            "stage_b_to_c": {"passed": True},
                        }
                    },
                }
            ]
        }
    )

    assert action["changed"] is True
    assert action["l5_admission_enforced"] is True
    assert action["resolve_symbols_default"] is False
    assert l5_calls[-1] == (True, True)
    assert resolve_calls[-1] is False
    assert "stage_rollout:rollout_applied" in service.get_auto_control_state().last_action


def test_pipeline_stage_rollout_handles_missing_report_gracefully(tmp_path: Path) -> None:
    """stage_exit 리포트가 없어도 예외 없이 NO_REPORT를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelineControlService(
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=PipelineJobEventRepository(db_path),
        queue_repo=FileEnrichQueueRepository(db_path),
        control_state_repo=PipelineControlStateRepository(db_path),
    )

    action = service.evaluate_stage_rollout(summary={})

    assert action["action"] == "NO_REPORT"
    assert action["changed"] is False
    assert service.get_auto_control_state().last_action == "stage_rollout:no_report"


def test_pipeline_stage_rollout_no_change_updates_state_action(tmp_path: Path) -> None:
    """동일 Stage 입력 재평가 시 NO_CHANGE가 상태에 반영되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelineControlService(
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=PipelineJobEventRepository(db_path),
        queue_repo=FileEnrichQueueRepository(db_path),
        control_state_repo=PipelineControlStateRepository(db_path),
        set_l5_admission_mode=lambda shadow_enabled, enforced: None,
        set_search_resolve_symbols_default=lambda enabled: None,
    )
    summary = {
        "datasets": [
            {
                "dataset_type": "workspace_real",
                "integrity": {
                    "stage_exit": {
                        "stage_a_to_b": {"passed": True},
                        "stage_b_to_c": {"passed": False},
                    }
                },
            }
        ]
    }
    first = service.evaluate_stage_rollout(summary=summary)
    second = service.evaluate_stage_rollout(summary=summary)

    assert first["action"] == "ROLLOUT_APPLIED"
    assert second["action"] == "NO_CHANGE"
    assert "stage_rollout:no_change" in service.get_auto_control_state().last_action


def test_pipeline_stage_rollout_state_payload_parses_last_action(tmp_path: Path) -> None:
    """stage rollout 상태 조회는 last_action 파싱 결과를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelineControlService(
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=PipelineJobEventRepository(db_path),
        queue_repo=FileEnrichQueueRepository(db_path),
        control_state_repo=PipelineControlStateRepository(db_path),
    )
    service.evaluate_stage_rollout(
        summary={
            "datasets": [
                {
                    "dataset_type": "workspace_real",
                    "integrity": {
                        "stage_exit": {
                            "stage_a_to_b": {"passed": True},
                            "stage_b_to_c": {"passed": False},
                        }
                    },
                }
            ]
        }
    )

    rollout = service.get_stage_rollout_state()

    assert rollout["available"] is True
    assert rollout["action"] in {"rollout_applied", "no_change"}
    assert rollout["stage_a_passed"] is True
    assert rollout["stage_b_passed"] is False


def test_pipeline_stage_rollout_applies_rollback_when_stage_regresses(tmp_path: Path) -> None:
    """stage가 pass에서 fail로 역전되면 토글도 자동 롤백되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    l5_calls: list[tuple[bool, bool]] = []
    resolve_calls: list[bool] = []
    service = PipelineControlService(
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=PipelineJobEventRepository(db_path),
        queue_repo=FileEnrichQueueRepository(db_path),
        control_state_repo=PipelineControlStateRepository(db_path),
        set_l5_admission_mode=lambda shadow_enabled, enforced: l5_calls.append((shadow_enabled, enforced)),
        set_search_resolve_symbols_default=lambda enabled: resolve_calls.append(enabled),
    )

    pass_summary = {
        "datasets": [
            {
                "dataset_type": "workspace_real",
                "integrity": {"stage_exit": {"stage_a_to_b": {"passed": True}, "stage_b_to_c": {"passed": True}}},
            }
        ]
    }
    fail_summary = {
        "datasets": [
            {
                "dataset_type": "workspace_real",
                "integrity": {"stage_exit": {"stage_a_to_b": {"passed": False}, "stage_b_to_c": {"passed": False}}},
            }
        ]
    }

    first = service.evaluate_stage_rollout(summary=pass_summary)
    second = service.evaluate_stage_rollout(summary=fail_summary)

    assert first["action"] == "ROLLOUT_APPLIED"
    assert second["action"] == "ROLLOUT_APPLIED"
    assert second["changed"] is True
    assert second["l5_admission_enforced"] is False
    assert second["resolve_symbols_default"] is True
    assert l5_calls[-1] == (True, False)
    assert resolve_calls[-1] is True


def test_pipeline_stage_rollout_reuses_persisted_targets_after_restart(tmp_path: Path) -> None:
    """서비스 재생성(재시작) 후에도 저장된 rollout 상태를 읽어 불필요한 changed를 만들지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    state_repo = PipelineControlStateRepository(db_path)
    policy_repo = PipelinePolicyRepository(db_path)
    event_repo = PipelineJobEventRepository(db_path)
    queue_repo = FileEnrichQueueRepository(db_path)
    summary = {
        "datasets": [
            {
                "dataset_type": "workspace_real",
                "integrity": {"stage_exit": {"stage_a_to_b": {"passed": True}, "stage_b_to_c": {"passed": False}}},
            }
        ]
    }

    first_calls: list[tuple[bool, bool]] = []
    first_resolve_calls: list[bool] = []
    first_service = PipelineControlService(
        policy_repo=policy_repo,
        event_repo=event_repo,
        queue_repo=queue_repo,
        control_state_repo=state_repo,
        set_l5_admission_mode=lambda shadow_enabled, enforced: first_calls.append((shadow_enabled, enforced)),
        set_search_resolve_symbols_default=lambda enabled: first_resolve_calls.append(enabled),
    )
    first = first_service.evaluate_stage_rollout(summary=summary)
    assert first["action"] == "ROLLOUT_APPLIED"
    assert first_calls[-1] == (True, False)
    assert first_resolve_calls[-1] is False

    second_calls: list[tuple[bool, bool]] = []
    second_resolve_calls: list[bool] = []
    restarted_service = PipelineControlService(
        policy_repo=policy_repo,
        event_repo=event_repo,
        queue_repo=queue_repo,
        control_state_repo=state_repo,
        set_l5_admission_mode=lambda shadow_enabled, enforced: second_calls.append((shadow_enabled, enforced)),
        set_search_resolve_symbols_default=lambda enabled: second_resolve_calls.append(enabled),
    )
    second = restarted_service.evaluate_stage_rollout(summary=summary)
    assert second["action"] == "NO_CHANGE"
    assert second["changed"] is False
    assert second_calls[-1] == (True, False)
    assert second_resolve_calls[-1] is False
