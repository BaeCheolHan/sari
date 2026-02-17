"""파이프라인 벤치마크 서비스 동작을 검증한다."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from sari.core.models import CollectionPolicyDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_benchmark_repository import PipelineBenchmarkRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.core.exceptions import BenchmarkError, CollectionError, ErrorContext
from sari.services.workspace_service import WorkspaceService
from sari.services.file_collection_service import FileCollectionService
from sari.services.pipeline_benchmark_service import BenchmarkLspExtractionBackend, PipelineBenchmarkService


def test_pipeline_benchmark_service_runs_and_returns_summary(tmp_path: Path) -> None:
    """벤치마크 실행은 요약 결과와 권고 정책을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    collection_service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineBenchmarkService.default_collection_policy(),
        lsp_backend=BenchmarkLspExtractionBackend(),
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=None,
    )
    benchmark_service = PipelineBenchmarkService(
        file_collection_service=collection_service,
        queue_repo=FileEnrichQueueRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        policy_repo=PipelinePolicyRepository(db_path),
        benchmark_repo=PipelineBenchmarkRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    summary = benchmark_service.run(repo_root=str(repo_dir), target_files=20, profile="default")

    assert summary["target_files"] == 20
    assert summary["status"] == "COMPLETED"
    assert "recommended_policy" in summary
    rec = summary["recommended_policy"]
    assert isinstance(rec["l3_p95_threshold_ms"], int)
    assert isinstance(rec["dead_ratio_threshold_bps"], int)


def test_pipeline_benchmark_service_supports_language_filter_and_per_language_report(tmp_path: Path) -> None:
    """벤치마크 실행은 language_filter/per_language_report 옵션을 반영해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    collection_service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineBenchmarkService.default_collection_policy(),
        lsp_backend=BenchmarkLspExtractionBackend(),
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=None,
    )
    benchmark_service = PipelineBenchmarkService(
        file_collection_service=collection_service,
        queue_repo=FileEnrichQueueRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        policy_repo=PipelinePolicyRepository(db_path),
        benchmark_repo=PipelineBenchmarkRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    summary = benchmark_service.run(
        repo_root=str(repo_dir),
        target_files=20,
        profile="default",
        language_filter=("python",),
        per_language_report=True,
    )

    assert summary["language_filter"] == ["python"]
    assert summary["per_language_report"] is True
    assert "per_language" in summary
    assert isinstance(summary["per_language"], list)
    assert len(summary["per_language"]) >= 1


def test_pipeline_benchmark_service_rejects_invalid_language_filter(tmp_path: Path) -> None:
    """지원하지 않는 language_filter는 명시적 오류를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    collection_service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineBenchmarkService.default_collection_policy(),
        lsp_backend=BenchmarkLspExtractionBackend(),
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=None,
    )
    benchmark_service = PipelineBenchmarkService(
        file_collection_service=collection_service,
        queue_repo=FileEnrichQueueRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        policy_repo=PipelinePolicyRepository(db_path),
        benchmark_repo=PipelineBenchmarkRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    with pytest.raises(BenchmarkError, match="unsupported language filter"):
        benchmark_service.run(
            repo_root=str(repo_dir),
            target_files=20,
            profile="default",
            language_filter=("not-a-language",),
        )


def test_pipeline_benchmark_wraps_runtime_failure_as_benchmark_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """런타임 오류는 명시적 BenchmarkError로 변환되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    collection_service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineBenchmarkService.default_collection_policy(),
        lsp_backend=BenchmarkLspExtractionBackend(),
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=None,
    )
    benchmark_service = PipelineBenchmarkService(
        file_collection_service=collection_service,
        queue_repo=FileEnrichQueueRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        policy_repo=PipelinePolicyRepository(db_path),
        benchmark_repo=PipelineBenchmarkRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    def _broken_prepare_dataset(*args: object, **kwargs: object) -> None:
        """데이터셋 준비 실패를 강제로 재현한다."""
        del args, kwargs
        raise RuntimeError("dataset bootstrap failed")

    monkeypatch.setattr(benchmark_service, "_prepare_dataset", _broken_prepare_dataset)

    with pytest.raises(BenchmarkError, match="benchmark failed: dataset bootstrap failed"):
        benchmark_service.run(repo_root=str(repo_dir), target_files=20, profile="default")


def test_pipeline_benchmark_drain_uses_multiple_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """벤치마크 큐 drain은 단일 루프가 아니라 멀티 워커로 처리해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    collection_service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=CollectionPolicyDTO(
            include_ext=(".py",),
            exclude_globs=(),
            max_file_size_bytes=256 * 1024,
            scan_interval_sec=180,
            max_enrich_batch=200,
            retry_max_attempts=2,
            retry_backoff_base_sec=1,
            queue_poll_interval_ms=50,
        ),
        lsp_backend=BenchmarkLspExtractionBackend(),
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=None,
    )
    benchmark_service = PipelineBenchmarkService(
        file_collection_service=collection_service,
        queue_repo=FileEnrichQueueRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        policy_repo=PipelinePolicyRepository(db_path),
        benchmark_repo=PipelineBenchmarkRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    remaining = {"value": 60}
    seen_threads: set[int] = set()
    remaining_lock = threading.Lock()

    def _fake_process_enrich_jobs(*, limit: int) -> int:
        """남은 작업 카운트를 줄이는 벤치용 워커 모킹 함수다."""
        del limit
        with remaining_lock:
            if remaining["value"] <= 0:
                return 0
            remaining["value"] -= 1
        seen_threads.add(threading.get_ident())
        time.sleep(0.005)
        return 1

    def _fake_status_counts() -> dict[str, int]:
        """남은 pending 개수를 기준으로 큐 상태를 반환한다."""
        with remaining_lock:
            pending = max(0, int(remaining["value"]))
        return {"PENDING": pending, "FAILED": 0, "RUNNING": 0, "DONE": 0, "DEAD": 0}

    monkeypatch.setattr(collection_service, "process_enrich_jobs", _fake_process_enrich_jobs)
    monkeypatch.setattr(benchmark_service._queue_repo, "get_status_counts", _fake_status_counts)

    benchmark_service._drain_enrich_queue(max_wait_sec=3.0)

    assert len(seen_threads) >= 2


def test_pipeline_benchmark_drain_worker_error_escalates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """워커 처리 오류는 침묵하지 않고 BenchmarkError로 승격해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    collection_service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineBenchmarkService.default_collection_policy(),
        lsp_backend=BenchmarkLspExtractionBackend(),
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=None,
    )
    benchmark_service = PipelineBenchmarkService(
        file_collection_service=collection_service,
        queue_repo=FileEnrichQueueRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        policy_repo=PipelinePolicyRepository(db_path),
        benchmark_repo=PipelineBenchmarkRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    def _broken_process_enrich_jobs(*, limit: int) -> int:
        """워커 실패를 강제로 재현한다."""
        del limit
        raise CollectionError(ErrorContext(code="ERR_ENRICH_JOB_FAILED", message="강제 실패"))

    monkeypatch.setattr(collection_service, "process_enrich_jobs", _broken_process_enrich_jobs)
    monkeypatch.setattr(
        benchmark_service._queue_repo,
        "get_status_counts",
        lambda: {"PENDING": 1, "FAILED": 0, "RUNNING": 0, "DONE": 0, "DEAD": 0},
    )

    with pytest.raises(BenchmarkError, match="benchmark enrich failed"):
        benchmark_service._drain_enrich_queue(max_wait_sec=1.0)
