"""파이프라인 성능 측정 서비스를 검증한다."""

from __future__ import annotations

from contextlib import contextmanager
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
        self.exclude_context_calls: list[tuple[str, ...]] = []

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

    @contextmanager
    def temporary_scan_exclude_globs(self, globs: tuple[str, ...]):
        self.exclude_context_calls.append(tuple(globs))
        yield


class _FakeCollectionServiceWithoutReset:
    """reset capability가 없는 더미 수집 서비스다."""

    def scan_once(self, repo_root: str):  # noqa: ANN201
        del repo_root
        return type(
            "ScanResult",
            (),
            {"scanned_count": 10, "indexed_count": 10, "deleted_count": 0},
        )()

    def process_enrich_jobs(self, limit: int) -> int:
        del limit
        return 0


class _FakeCollectionServiceWithoutLspReset(_FakeCollectionServiceWithoutReset):
    """probe reset은 있지만 LSP reset capability는 없는 더미 수집 서비스다."""

    def reset_probe_state(self) -> None:
        return


class _RecoveringQueueRepository:
    """stale RUNNING 회수 경로를 검증하는 더미 저장소다."""

    def __init__(self) -> None:
        self._counts = {"PENDING": 0, "RUNNING": 1, "FAILED": 0, "DONE": 0, "DEAD": 0}
        self.recover_calls = 0

    def get_status_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def recover_stale_running_to_failed(self, now_iso: str, stale_before_iso: str) -> int:
        del now_iso, stale_before_iso
        self.recover_calls += 1
        if self._counts["RUNNING"] > 0:
            self._counts["RUNNING"] = 0
            self._counts["FAILED"] = 1
            return 1
        return 0


class _IdleCollectionService:
    def process_enrich_jobs(self, limit: int) -> int:
        del limit
        return 0


class _FakeFileRepoWithStateCounts:
    def get_enrich_state_counts(self) -> dict[str, int]:
        return {"TOOL_READY": 3, "L3_SKIPPED": 1}


class _FakeReadinessRepoWithCounts:
    def count_by_tool_ready(self) -> dict[str, int]:
        return {"tool_ready_true": 3, "tool_ready_false": 1}


class _FakeLspRepoWithCounts:
    def count_distinct_symbol_files(self) -> int:
        return 3


class _FakeCollectionServiceWithRepos(_FakeCollectionService):
    def __init__(self) -> None:
        super().__init__()
        self._file_repo = _FakeFileRepoWithStateCounts()
        self._readiness_repo = _FakeReadinessRepoWithCounts()
        self._lsp_repo = _FakeLspRepoWithCounts()


class _FakeBrokerSnapshot:
    def __init__(self) -> None:
        self.active_sessions_by_language = {"java": 1, "typescript": 2}
        self.active_sessions_by_budget_group = {"ts-vue": 2}


class _FakeBrokerForPerfSnapshot:
    def get_snapshot(self) -> _FakeBrokerSnapshot:
        return _FakeBrokerSnapshot()


class _FakeCollectionServiceWithPerfRuntimeSnapshot(_FakeCollectionServiceWithRepos):
    def __init__(self) -> None:
        super().__init__()
        self._lsp_session_broker = _FakeBrokerForPerfSnapshot()

    def _lsp_runtime_metrics_snapshot(self) -> dict[str, int]:
        return {
            "broker_active_sessions_java": 1,
            "broker_active_budget_group_ts-vue": 2,
            "broker_guard_reject_count": 3,
            "session_cache_hit_by_tier_single": 0,
            "session_eviction_churn_count": 0,
        }


class _FakeQueueRepositoryWithPendingDetails(_FakeQueueRepository):
    def __init__(self) -> None:
        super().__init__()
        self.pending_split_calls: list[str] = []
        self.pending_age_calls: list[str] = []

    def get_pending_split_counts(self, now_iso: str) -> dict[str, int]:
        self.pending_split_calls.append(now_iso)
        return {"PENDING_AVAILABLE": 4, "PENDING_DEFERRED": 7}

    def get_pending_age_stats(self, now_iso: str) -> dict[str, float | None]:
        self.pending_age_calls.append(now_iso)
        return {
            "oldest_pending_available_age_sec": 12.0,
            "oldest_pending_deferred_age_sec": 120.0,
            "p95_pending_available_age_sec": 9.5,
        }

    def get_eligible_counts(self, now_iso: str) -> dict[str, int]:
        del now_iso
        return {
            "eligible_total_count": 8,
            "eligible_done_count": 3,
            "eligible_failed_count": 1,
            "eligible_deferred_count": 7,
        }


class _FakeReadinessRepoWithGap:
    def count_by_tool_ready(self) -> dict[str, int]:
        return {"tool_ready_true": 10, "tool_ready_false": 0}


class _FakeLspRepoWithGap:
    def count_distinct_symbol_files(self) -> int:
        return 7


class _FakeCollectionServiceWithReposGap(_FakeCollectionService):
    def __init__(self) -> None:
        super().__init__()
        self._file_repo = _FakeFileRepoWithStateCounts()
        self._readiness_repo = _FakeReadinessRepoWithGap()
        self._lsp_repo = _FakeLspRepoWithGap()


class _FakeQueueRepositoryWithPendingDetailsAndGap(_FakeQueueRepositoryWithPendingDetails):
    def get_eligible_counts(self, now_iso: str) -> dict[str, int]:
        del now_iso
        return {
            "eligible_total_count": 12,
            "eligible_done_count": 10,
            "eligible_failed_count": 2,
            "eligible_deferred_count": 7,
        }


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
    # PR-A residual close: workspace_real gate는 threshold뿐 아니라 integrity(real LSP backend 등)도 반영한다.
    assert summary["gate_passed"] is False
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
    assert workspace["integrity"]["integrity_checks"]["measurement_backend_real_lsp"] is False


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


def test_pipeline_perf_service_rejects_missing_probe_reset_capability(tmp_path: Path) -> None:
    """probe reset 요청 시 capability가 없으면 명시 실패해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithoutReset(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    with pytest.raises(PerfError, match="reset_probe_state"):
        service.run(
            repo_root=str(repo_dir),
            target_files=2000,
            profile="realistic_v1",
            reset_probe_state=True,
        )


def test_pipeline_perf_service_rejects_missing_lsp_reset_capability(tmp_path: Path) -> None:
    """cold_lsp_reset 요청 시 capability가 없으면 명시 실패해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithoutLspReset(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    with pytest.raises(PerfError, match="reset_lsp_runtime"):
        service.run(
            repo_root=str(repo_dir),
            target_files=2000,
            profile="realistic_v1",
            cold_lsp_reset=True,
        )


def test_pipeline_perf_service_records_workspace_exclude_globs_in_run_context(tmp_path: Path) -> None:
    """workspace_exclude_globs는 run_context에 기록되고 scan context manager로 전달되어야 한다."""
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
        workspace_exclude_globs=("serena/test/resources/repos/**", "**/benchmark_dataset/**"),
    )
    workspace = next(item for item in summary["datasets"] if item["dataset_type"] == "workspace_real")
    assert workspace["run_context"]["config_snapshot"]["workspace_exclude_globs"] == [
        "serena/test/resources/repos/**",
        "**/benchmark_dataset/**",
    ]
    assert collection_service.exclude_context_calls == [("serena/test/resources/repos/**", "**/benchmark_dataset/**")]


def test_pipeline_perf_service_drain_recovers_stale_running_for_perf_only(tmp_path: Path) -> None:
    """perf drain 루프는 stale RUNNING을 회수해 timeout 없이 종료해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    queue_repo = _RecoveringQueueRepository()
    service = PipelinePerfService(
        file_collection_service=_IdleCollectionService(),
        queue_repo=queue_repo,
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    service._drain_enrich_queue(max_wait_sec=0.2)

    assert queue_repo.recover_calls >= 1
    assert service._last_drain_diagnostics["stale_running_recovered_count"] >= 1


def test_pipeline_perf_integrity_snapshot_includes_pending_split_age_and_eligible_counts(tmp_path: Path) -> None:
    """workspace integrity 스냅샷에 pending split/age와 strict eligible 집계가 포함되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _FakeQueueRepositoryWithPendingDetails()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithRepos(),
        queue_repo=queue_repo,
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()

    queue_detailed = snap["queue_counts_detailed"]
    assert isinstance(queue_detailed, dict)
    assert queue_detailed["PENDING_AVAILABLE"] == 4
    assert queue_detailed["PENDING_DEFERRED"] == 7
    pending_age = snap["pending_age_stats"]
    assert isinstance(pending_age, dict)
    assert pending_age["oldest_pending_available_age_sec"] == pytest.approx(12.0)
    assert pending_age["oldest_pending_deferred_age_sec"] == pytest.approx(120.0)
    assert snap["eligible_counts_mode"] == "strict_queue_phaseB_v1"
    assert snap["eligible_counts"]["eligible_total_count"] == 8
    assert snap["eligible_counts"]["eligible_deferred_count"] == 7
    assert len(queue_repo.pending_split_calls) == 1
    assert len(queue_repo.pending_age_calls) == 1
    assert queue_repo.pending_split_calls[0] == queue_repo.pending_age_calls[0]


def test_pipeline_perf_integrity_snapshot_includes_broker_snapshot_and_runtime_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _FakeQueueRepositoryWithPendingDetails()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithPerfRuntimeSnapshot(),
        queue_repo=queue_repo,
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()

    runtime_metrics = snap.get("lsp_runtime_metrics")
    assert isinstance(runtime_metrics, dict)
    assert runtime_metrics["broker_guard_reject_count"] == 3
    assert runtime_metrics["broker_active_budget_group_ts-vue"] == 2
    # PR4 baseline: warm session/eviction 지표 자리는 존재 (값은 0 가능)
    assert "session_cache_hit_by_tier_single" in runtime_metrics
    assert "session_eviction_churn_count" in runtime_metrics

    broker_snapshot = snap.get("broker_snapshot")
    assert isinstance(broker_snapshot, dict)
    assert broker_snapshot["active_sessions_by_language"]["java"] == 1
    assert broker_snapshot["active_sessions_by_budget_group"]["ts-vue"] == 2


def test_pipeline_perf_integrity_snapshot_adds_strict_eligible_match_check(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _FakeQueueRepositoryWithPendingDetails()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithRepos(),
        queue_repo=queue_repo,
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()
    checks = snap["integrity_checks"]
    assert checks["queue_running_zero"] is True
    assert checks["tool_ready_vs_symbol_files_match"] is True
    assert checks["eligible_done_vs_tool_ready_and_symbol_files_match"] is True


def test_build_dataset_result_for_workspace_real_fails_gate_when_integrity_check_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    result = service._build_dataset_result(
        dataset_type="workspace_real",
        repo_scope="/tmp/repo",
        done_count=4000,
        dead_count=0,
        l3_elapsed_sec=5.0,
        wall_time_sec=8.0,
        count_mode="delta",
        start_counts={},
        end_counts={},
        measurement_scope="workspace_real_isolated",
        run_context={},
        integrity_snapshot={
            "integrity_checks": {
                "measurement_backend_real_lsp": False,
                "queue_running_zero": True,
                "tool_ready_vs_symbol_files_match": True,
            }
        },
    )
    assert result["gate_passed"] is False


def test_build_dataset_result_for_workspace_real_passes_only_when_threshold_and_integrity_checks_pass(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    result = service._build_dataset_result(
        dataset_type="workspace_real",
        repo_scope="/tmp/repo",
        done_count=4000,
        dead_count=0,
        l3_elapsed_sec=5.0,
        wall_time_sec=8.0,
        count_mode="delta",
        start_counts={},
        end_counts={},
        measurement_scope="workspace_real_isolated",
        run_context={},
        integrity_snapshot={
            "integrity_checks": {
                "measurement_backend_real_lsp": True,
                "queue_running_zero": True,
                "tool_ready_vs_symbol_files_match": True,
                "eligible_done_vs_tool_ready_and_symbol_files_match": True,
            }
        },
    )
    assert result["gate_passed"] is True
    assert result["threshold_gate_passed"] is True
    assert result["integrity_gate_passed"] is True


def test_pipeline_perf_integrity_snapshot_allows_zero_symbol_tool_ready_gap_when_eligible_done_matches(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithReposGap(),
        queue_repo=_FakeQueueRepositoryWithPendingDetailsAndGap(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()
    checks = snap["integrity_checks"]
    # tool_ready=true 이지만 심볼 0건 파일(예: java/js)이 있을 수 있으므로 strict equality는 강제하지 않는다.
    assert checks["tool_ready_vs_symbol_files_match"] is True
    assert checks["eligible_done_vs_tool_ready_and_symbol_files_match"] is True
    assert snap["tool_readiness_symbol_gap_count"] == 3


def test_build_dataset_result_supports_real_lsp_phase1_profile_for_workspace_real(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    result = service._build_dataset_result(
        dataset_type="workspace_real",
        repo_scope="/tmp/repo",
        done_count=1800,
        dead_count=0,
        l3_elapsed_sec=30.0,   # 60 jobs/s
        wall_time_sec=36.0,
        count_mode="delta",
        start_counts={},
        end_counts={},
        measurement_scope="workspace_real_isolated",
        run_context={
            "config_snapshot": {"profile": "real_lsp_phase1_v1"},
            "backend_kind": "SolidLspExtractionBackend",
        },
        integrity_snapshot={
            "integrity_checks": {
                "measurement_backend_real_lsp": True,
                "queue_running_zero": True,
                "tool_ready_vs_symbol_files_match": True,
                "eligible_done_vs_tool_ready_and_symbol_files_match": True,
            }
        },
    )
    assert result["threshold_profile_applied"] == "real_lsp_phase1_v1"
    assert result["threshold_gate_passed"] is True
    assert result["integrity_gate_passed"] is True
    assert result["gate_passed"] is True
