"""파이프라인 품질 실행 저장소를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.pipeline_quality_repository import PipelineQualityRepository
from sari.db.schema import init_schema


def test_pipeline_quality_repository_create_complete_and_latest(tmp_path: Path) -> None:
    """실행 생성/완료 후 최신 실행 조회가 가능해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = PipelineQualityRepository(db_path)

    run_id = repo.create_run(repo_root="/repo", limit_files=100, profile="default", started_at="2026-02-16T10:00:00+00:00")
    assert run_id != ""

    repo.complete_run(
        run_id=run_id,
        finished_at="2026-02-16T10:01:00+00:00",
        status="PASSED",
        summary={
            "run_id": run_id,
            "precision": {"total": 99.9},
            "error_rate": 0.0,
        },
    )
    latest = repo.get_latest_run()
    assert latest is not None
    assert latest["run_id"] == run_id
    assert latest["status"] == "PASSED"
    assert latest["summary"]["precision"]["total"] == 99.9


def test_pipeline_quality_repository_get_latest_run_by_scope(tmp_path: Path) -> None:
    """scope_repo_root 필터로 최신 실행을 조회할 수 있어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = PipelineQualityRepository(db_path)

    run_a = repo.create_run(
        repo_root="/repo/a",
        scope_repo_root="/scope/a",
        limit_files=10,
        profile="default",
        started_at="2026-02-25T10:00:00+00:00",
    )
    repo.complete_run(
        run_id=run_a,
        finished_at="2026-02-25T10:00:10+00:00",
        status="PASSED",
        summary={"run_id": run_a},
    )

    run_b = repo.create_run(
        repo_root="/repo/b",
        scope_repo_root="/scope/b",
        limit_files=20,
        profile="strict",
        started_at="2026-02-25T10:01:00+00:00",
    )
    repo.complete_run(
        run_id=run_b,
        finished_at="2026-02-25T10:01:10+00:00",
        status="FAILED",
        summary={"run_id": run_b},
    )

    latest_a = repo.get_latest_run(scope_repo_root="/scope/a")
    assert latest_a is not None
    assert latest_a["run_id"] == run_a
    assert latest_a["scope_repo_root"] == "/scope/a"

    latest_b = repo.get_latest_run(scope_repo_root="/scope/b")
    assert latest_b is not None
    assert latest_b["run_id"] == run_b
    assert latest_b["scope_repo_root"] == "/scope/b"
