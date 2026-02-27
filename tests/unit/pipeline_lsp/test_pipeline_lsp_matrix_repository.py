"""LSP 매트릭스 실행 저장소를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.pipeline_lsp_matrix_repository import PipelineLspMatrixRepository
from sari.db.schema import init_schema


def test_pipeline_lsp_matrix_repository_create_complete_and_latest(tmp_path: Path) -> None:
    """실행 생성/완료 후 최신 실행 조회가 가능해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = PipelineLspMatrixRepository(db_path)

    run_id = repo.create_run(
        repo_root="/repo",
        required_languages=("python", "typescript"),
        fail_on_unavailable=True,
        strict_symbol_gate=True,
        started_at="2026-02-17T20:00:00+00:00",
    )
    assert run_id != ""

    summary = {
        "run_id": run_id,
        "repo_root": "/repo",
        "summary": {"total_languages": 2, "available_languages": 1, "unavailable_languages": 1},
        "gate": {"required_languages": ["python", "typescript"], "failed_required_languages": ["typescript"], "passed": False},
    }
    repo.complete_run(
        run_id=run_id,
        finished_at="2026-02-17T20:01:00+00:00",
        status="FAILED",
        summary=summary,
    )

    latest = repo.get_latest_run()
    assert latest is not None
    assert latest["run_id"] == run_id
    assert latest["status"] == "FAILED"
    assert latest["summary"]["gate"]["passed"] is False
