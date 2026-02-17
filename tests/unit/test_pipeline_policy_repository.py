"""파이프라인 정책 저장소 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.schema import init_schema


def test_pipeline_policy_repository_returns_default_policy(tmp_path: Path) -> None:
    """초기 조회 시 기본 정책이 생성되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = PipelinePolicyRepository(db_path)

    policy = repo.get_policy()

    assert policy.deletion_hold is False
    assert policy.l3_p95_threshold_ms == 180_000
    assert policy.dead_ratio_threshold_bps == 10
    assert policy.enrich_worker_count == 4


def test_pipeline_policy_repository_updates_fields(tmp_path: Path) -> None:
    """부분 업데이트가 정책에 반영되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = PipelinePolicyRepository(db_path)

    updated = repo.update_policy(
        deletion_hold=True,
        l3_p95_threshold_ms=210_000,
        dead_ratio_threshold_bps=25,
        enrich_worker_count=8,
    )

    assert updated.deletion_hold is True
    assert updated.l3_p95_threshold_ms == 210_000
    assert updated.dead_ratio_threshold_bps == 25
    assert updated.enrich_worker_count == 8
