"""파이프라인 성능 측정 서비스를 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from sari.core.exceptions import PerfError
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository
from sari.db.schema import init_schema
from sari.services.pipeline_perf_service import PipelinePerfService


class _FakeBenchmarkService:
    """샘플 벤치 결과를 고정 반환하는 더미 서비스다."""

    def run(
        self,
        repo_root: str,
        target_files: int,
        profile: str,
        language_filter: tuple[str, ...] | None = None,
        per_language_report: bool = False,
    ) -> dict[str, object]:
        """샘플 벤치 요약을 반환한다."""
        del repo_root, profile, language_filter, per_language_report
        return {
            "status": "COMPLETED",
            "target_files": target_files,
            "scan": {"ingest_latency_ms_p95": 1000},
            "enrich": {"completion_sec": 8.0, "done_count": 2000, "dead_count": 0},
        }


class _FakeQueueRepository:
    """큐 상태를 고정 반환하는 더미 저장소다."""

    def __init__(self) -> None:
        """호출 순서별 스냅샷을 초기화한다."""
        self._calls = 0

    def get_status_counts(self) -> dict[str, int]:
        """DONE/DEAD 기준 상태를 반환한다."""
        self._calls += 1
        if self._calls == 1:
            return {"PENDING": 0, "RUNNING": 0, "FAILED": 0, "DONE": 0, "DEAD": 0}
        if self._calls == 2:
            return {"PENDING": 100, "RUNNING": 0, "FAILED": 0, "DONE": 0, "DEAD": 0}
        return {"PENDING": 0, "RUNNING": 0, "FAILED": 0, "DONE": 1000, "DEAD": 0}


class _FakeCollectionService:
    """scan/process 호출을 흉내내는 더미 수집 서비스다."""

    def __init__(self) -> None:
        """보강 처리 호출 횟수를 초기화한다."""
        self._calls = 0
        self.reset_runtime_state_calls = 0
        self.reset_probe_state_calls = 0
        self.reset_lsp_runtime_calls = 0

    def scan_once(self, repo_root: str):  # noqa: ANN201
        """스캔 더미 결과를 반환한다."""
        del repo_root
        return type(
            "ScanResult",
            (),
            {"scanned_count": 1000, "indexed_count": 1000, "deleted_count": 0},
        )()

    def process_enrich_jobs(self, limit: int) -> int:
        """큐가 이미 비어 있다고 가정한다."""
        del limit
        self._calls += 1
        if self._calls == 1:
            return 100
        return 0

    def reset_runtime_state(self) -> None:
        """fresh_db 사전 리셋 호출을 기록한다."""
        self.reset_runtime_state_calls += 1

    def reset_probe_state(self) -> None:
        """probe 상태 리셋 호출을 기록한다."""
        self.reset_probe_state_calls += 1

    def reset_lsp_runtime(self) -> None:
        """cold LSP 리셋 호출을 기록한다."""
        self.reset_lsp_runtime_calls += 1


def test_pipeline_perf_service_run_returns_gate_summary(tmp_path: Path) -> None:
    """실행 결과에 혼합지표와 게이트 판정이 포함되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()

    service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    summary = service.run(repo_root=str(repo_dir), target_files=2000, profile="realistic_v1")
    assert summary["status"] == "COMPLETED"
    assert summary["threshold_profile"] == "realistic_v1"
    assert summary["dataset_mode"] == "isolated"
    assert summary["gate_passed"] is True
    datasets = summary["datasets"]
    assert isinstance(datasets, list)
    assert len(datasets) == 2
    assert {item["dataset_type"] for item in datasets} == {"sample_2k", "workspace_real"}
    workspace = next(item for item in datasets if item["dataset_type"] == "workspace_real")
    assert workspace["count_mode"] == "delta"
    assert workspace["measurement_scope"] == "workspace_real_isolated"
    assert workspace["done_count"] == 1000
    assert workspace["dead_count"] == 0
    assert isinstance(workspace["start_counts"], dict)
    assert isinstance(workspace["end_counts"], dict)
    assert workspace["run_context"]["fresh_db"] is False
    assert workspace["run_context"]["pre_state_reset"] is False


def test_pipeline_perf_service_rejects_invalid_repo(tmp_path: Path) -> None:
    """존재하지 않는 repo 경로는 명시 오류여야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    with pytest.raises(PerfError, match="repo 경로를 찾을 수 없습니다"):
        service.run(repo_root=str(tmp_path / "missing"), target_files=2000, profile="realistic_v1")


def test_pipeline_perf_service_rejects_invalid_dataset_mode(tmp_path: Path) -> None:
    """dataset_mode가 허용값이 아니면 명시 오류를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    with pytest.raises(PerfError, match="dataset_mode must be isolated or legacy"):
        service.run(repo_root=str(repo_dir), target_files=2000, profile="realistic_v1", dataset_mode="invalid")


def test_pipeline_perf_service_applies_cold_reset_options(tmp_path: Path) -> None:
    """cold 측정 옵션이 사전 리셋 루틴을 호출해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    collection_service = _FakeCollectionService()
    service = PipelinePerfService(
        file_collection_service=collection_service,
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    summary = service.run(
        repo_root=str(repo_dir),
        target_files=2000,
        profile="realistic_v1",
        dataset_mode="isolated",
        fresh_db=True,
        reset_probe_state=True,
        cold_lsp_reset=True,
    )
    assert summary["status"] == "COMPLETED"
    assert collection_service.reset_runtime_state_calls == 1
    assert collection_service.reset_probe_state_calls == 1
    assert collection_service.reset_lsp_runtime_calls == 1
    datasets = summary["datasets"]
    workspace = next(item for item in datasets if item["dataset_type"] == "workspace_real")
    assert workspace["run_context"]["fresh_db"] is True
    assert workspace["run_context"]["pre_state_reset"] is True
    assert workspace["run_context"]["cold_lsp_reset"] is True
