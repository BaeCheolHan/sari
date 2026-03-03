"""Stage baseline 저장소를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.pipeline_stage_baseline_repository import PipelineStageBaselineRepository
from sari.db.schema import init_schema


def test_pipeline_stage_baseline_repository_initializes_once_and_persists(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = PipelineStageBaselineRepository(db_path)

    assert repo.get_l4_admission_rate_baseline_p50() is None
    assert repo.get_l4_admission_rate_baseline_samples() == 0
    assert repo.get_p95_pending_available_age_baseline_sec() is None
    assert repo.get_p95_pending_available_age_baseline_samples() == 0

    first = repo.initialize_l4_admission_rate_baseline(5.0)
    second = repo.initialize_l4_admission_rate_baseline(9.0)

    reloaded = PipelineStageBaselineRepository(db_path)
    assert first is True
    assert second is False
    assert reloaded.get_l4_admission_rate_baseline_p50() == 5.0
    assert reloaded.get_l4_admission_rate_baseline_samples() == 1

    first_pending = repo.initialize_p95_pending_available_age_baseline(8.0)
    second_pending = repo.initialize_p95_pending_available_age_baseline(15.0)
    assert first_pending is True
    assert second_pending is False
    assert reloaded.get_p95_pending_available_age_baseline_sec() == 8.0
    assert reloaded.get_p95_pending_available_age_baseline_samples() == 1
