"""파이프라인 벤치마크 실행 저장소를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.pipeline_benchmark_repository import PipelineBenchmarkRepository
from sari.db.schema import init_schema


def test_pipeline_benchmark_repository_create_complete_and_latest(tmp_path: Path) -> None:
    """실행 생성/완료 후 최신 실행 조회가 가능해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = PipelineBenchmarkRepository(db_path)

    run_id = repo.create_run(repo_root="/repo", target_files=100, profile="default", started_at="2026-02-16T10:00:00+00:00")
    assert run_id != ""

    repo.complete_run(
        run_id=run_id,
        finished_at="2026-02-16T10:01:00+00:00",
        status="COMPLETED",
        summary={
            "run_id": run_id,
            "target_files": 100,
            "dead_ratio_bps": 0,
        },
    )
    latest = repo.get_latest_run()
    assert latest is not None
    assert latest["run_id"] == run_id
    assert latest["status"] == "COMPLETED"
    assert latest["summary"]["target_files"] == 100
