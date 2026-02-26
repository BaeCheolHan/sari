"""파이프라인 진행률/ETA 메트릭을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CollectionPolicyDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.collection.service import FileCollectionService
from sari.services.lsp_extraction_contracts import LspExtractionBackend, LspExtractionResultDTO


class _NoopLspBackend(LspExtractionBackend):
    """L3 추출을 성공 처리하는 테스트 더블이다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        del repo_root, relative_path, content_hash
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)


def _policy() -> CollectionPolicyDTO:
    return CollectionPolicyDTO(
        include_ext=(".py",),
        exclude_globs=("**/.git/**",),
        max_file_size_bytes=512 * 1024,
        scan_interval_sec=120,
        max_enrich_batch=100,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        queue_poll_interval_ms=100,
    )


def test_pipeline_metrics_exposes_progress_and_eta_fields(tmp_path: Path) -> None:
    """메트릭에는 진행률(%)과 ETA 필드가 포함되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-progress"
    repo_dir.mkdir()
    for index in range(3):
        (repo_dir / f"f{index}.py").write_text(f"def f{index}():\n    return {index}\n", encoding="utf-8")

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
    )
    service.scan_once(str(repo_dir.resolve()))
    service.process_enrich_jobs(limit=100)

    metrics = service.get_pipeline_metrics().to_dict()
    assert "progress_percent_l2" in metrics
    assert "progress_percent_l3" in metrics
    assert "eta_l2_sec" in metrics
    assert "eta_l3_sec" in metrics
    assert "eta_confidence_bps" in metrics
    assert "eta_window_sec" in metrics
    assert "throughput_ema" in metrics
    assert "remaining_jobs_l2" in metrics
    assert "remaining_jobs_l3" in metrics
    assert "watcher_queue_depth" in metrics
    assert "watcher_drop_count" in metrics
    assert "watcher_overflow_count" in metrics
    assert "watcher_last_overflow_at" in metrics
    assert "lsp_instance_count" in metrics
    assert "lsp_forced_kill_count" in metrics
    assert "lsp_stop_timeout_count" in metrics
    assert "lsp_orphan_suspect_count" in metrics


def test_pipeline_metrics_returns_unknown_eta_when_throughput_unstable(tmp_path: Path) -> None:
    """초기 샘플이 부족한 구간에서는 ETA를 -1(미확정)로 유지해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-eta-unstable"
    repo_dir.mkdir()
    (repo_dir / "f0.py").write_text("def f0():\n    return 0\n", encoding="utf-8")

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
    )
    service.scan_once(str(repo_dir.resolve()))
    metrics = service.get_pipeline_metrics().to_dict()
    assert metrics["eta_l2_sec"] in (-1, 0)
    assert metrics["eta_l3_sec"] in (-1, 0)
