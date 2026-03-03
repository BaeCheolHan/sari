"""파이프라인 성능 측정 서비스를 검증한다."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from sari.core.exceptions import PerfError
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository
from sari.db.repositories.pipeline_stage_baseline_repository import PipelineStageBaselineRepository
from sari.db.schema import init_schema
from sari.services.pipeline.perf_service import PipelinePerfService


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


class _HeavyDeferredOnlyQueueRepository:
    """L5 heavy defer pending만 남은 상태를 흉내내는 더미 저장소다."""

    def __init__(self) -> None:
        self._counts = {"PENDING": 8, "RUNNING": 0, "FAILED": 0, "DONE": 3975, "DEAD": 0}

    def get_status_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def count_pending_perf_ignorable(self) -> int:
        return 8


class _HeavyDeferredPromotableQueueRepository:
    """heavy defer pending이 마지막에 강제 승격되는지 검증하는 더미 저장소다."""

    def __init__(self) -> None:
        self._pending = 4
        self.promote_calls = 0
        self.promoted_job_ids: list[str] = []

    def get_status_counts(self) -> dict[str, int]:
        return {
            "PENDING": self._pending,
            "RUNNING": 0,
            "FAILED": 0,
            "DONE": 100,
            "DEAD": 0,
        }

    def count_pending_perf_ignorable(self) -> int:
        return self._pending

    def list_pending_perf_ignorable_job_ids(self, limit: int = 256) -> list[str]:
        del limit
        if self._pending <= 0:
            return []
        return [f"job-{i}" for i in range(self._pending)]

    def promote_to_l3_many(self, job_ids: list[str], now_iso: str) -> None:
        del now_iso
        self.promote_calls += 1
        self.promoted_job_ids.extend(job_ids)
        self._pending = 0


class _FakeCollectionServiceWithSeparateL3(_IdleCollectionService):
    def __init__(self) -> None:
        self.l2_calls = 0
        self.l3_calls = 0
        self.unified_calls = 0

    def process_enrich_jobs(self, limit: int) -> int:
        del limit
        self.unified_calls += 1
        return 0

    def process_enrich_jobs_l2(self, limit: int) -> int:
        del limit
        self.l2_calls += 1
        return 1 if self.l2_calls == 1 else 0

    def process_enrich_jobs_l3(self, limit: int) -> int:
        del limit
        self.l3_calls += 1
        return 1 if self.l3_calls == 1 else 0


class _FakeCollectionServiceWithL5Only(_IdleCollectionService):
    def __init__(self) -> None:
        self.l5_calls = 0

    def process_enrich_jobs_l5(self, limit: int) -> int:
        del limit
        self.l5_calls += 1
        return 1 if self.l5_calls == 1 else 0


class _L5OnlyPendingQueueRepository:
    """L5 lane만 남은 상태를 흉내내는 큐 저장소."""

    def __init__(self, collection: _FakeCollectionServiceWithL5Only) -> None:
        self._collection = collection

    def get_status_counts(self) -> dict[str, int]:
        pending = 1 if self._collection.l5_calls == 0 else 0
        done = 0 if self._collection.l5_calls == 0 else 1
        return {"PENDING": pending, "RUNNING": 0, "FAILED": 0, "DONE": done, "DEAD": 0}



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


class _FakeCollectionServiceWithQualityShadowSummary(_FakeCollectionServiceWithPerfRuntimeSnapshot):
    def _l3_quality_shadow_summary_snapshot(self) -> dict[str, object]:
        return {
            "enabled": True,
            "sampled_files": 2,
            "sampled_files_by_language": {"java": 2},
            "avg_recall_proxy_by_language": {"java": 0.75},
            "avg_precision_proxy_by_language": {"java": 0.5},
            "avg_kind_match_rate_by_language": {"java": 1.0},
            "avg_position_match_rate_by_language": {"java": 1.0},
            "quality_flags_top_counts": {"ast_missing_symbols": 1},
            "shadow_eval_errors": 0,
        }


class _FakeReadinessRepoForStageGate:
    def count_by_tool_ready(self) -> dict[str, int]:
        return {"tool_ready_true": 95, "tool_ready_false": 5}


class _FakeCollectionServiceForStageGate(_FakeCollectionServiceWithPerfRuntimeSnapshot):
    def __init__(self) -> None:
        super().__init__()
        self._readiness_repo = _FakeReadinessRepoForStageGate()

    def _lsp_runtime_metrics_snapshot(self) -> dict[str, int]:
        return {
            "broker_active_sessions_java": 1,
            "broker_active_budget_group_ts-vue": 2,
            "broker_guard_reject_count": 3,
            "session_cache_hit_by_tier_single": 0,
            "session_eviction_churn_count": 0,
            "l5_total_decisions": 100,
            "l5_total_admitted": 4,
            "l5_batch_decisions": 100,
            "l5_batch_admitted": 1,
            "search_quality_regression_pct": 0,
        }


class _FakeCollectionServiceForStageGateHigherL5(_FakeCollectionServiceForStageGate):
    def _lsp_runtime_metrics_snapshot(self) -> dict[str, int]:
        return {
            "broker_active_sessions_java": 1,
            "broker_active_budget_group_ts-vue": 2,
            "broker_guard_reject_count": 3,
            "session_cache_hit_by_tier_single": 0,
            "session_eviction_churn_count": 0,
            "l5_total_decisions": 100,
            "l5_total_admitted": 8,
            "l5_batch_decisions": 100,
            "l5_batch_admitted": 1,
            "search_quality_regression_pct": 0,
        }


class _FakeCollectionServiceWithL5ModeSwitch(_FakeCollectionServiceWithRepos):
    def __init__(self) -> None:
        super().__init__()
        self._shadow_enabled = False
        self._enforced = False
        self.mode_calls: list[tuple[bool, bool]] = []

    def set_l5_admission_mode(self, *, shadow_enabled: bool, enforced: bool) -> None:
        self._shadow_enabled = bool(shadow_enabled)
        self._enforced = bool(enforced)
        self.mode_calls.append((self._shadow_enabled, self._enforced))

    def _lsp_runtime_metrics_snapshot(self) -> dict[str, int]:
        if self._shadow_enabled:
            return {
                "l5_total_decisions": 100,
                "l5_total_admitted": 5,
                "l5_batch_decisions": 80,
                "l5_batch_admitted": 1,
                "search_quality_regression_pct": 0,
            }
        return {
            "l5_total_decisions": 0,
            "l5_total_admitted": 0,
            "l5_batch_decisions": 0,
            "l5_batch_admitted": 0,
        }


class _FakeCollectionServiceWithL5AndL3ModeSwitch(_FakeCollectionServiceWithL5ModeSwitch):
    def __init__(self) -> None:
        super().__init__()
        self.l3_quality_shadow_mode_calls: list[tuple[bool, float, int, tuple[str, ...]]] = []
        self._l3_quality_shadow_enabled = False
        self._l3_quality_shadow_sample_rate = 0.0
        self._l3_quality_shadow_max_files = 0
        self._l3_quality_shadow_lang_allowlist: tuple[str, ...] = ()

    def set_l3_quality_shadow_mode(
        self,
        *,
        enabled: bool,
        sample_rate: float,
        max_files: int,
        lang_allowlist: tuple[str, ...],
    ) -> None:
        self._l3_quality_shadow_enabled = bool(enabled)
        self._l3_quality_shadow_sample_rate = float(sample_rate)
        self._l3_quality_shadow_max_files = int(max_files)
        self._l3_quality_shadow_lang_allowlist = tuple(str(item) for item in lang_allowlist)
        self.l3_quality_shadow_mode_calls.append(
            (
                bool(enabled),
                float(sample_rate),
                int(max_files),
                tuple(str(item) for item in lang_allowlist),
            )
        )

    def get_l3_quality_shadow_mode(self) -> dict[str, object]:
        return {
            "enabled": bool(self._l3_quality_shadow_enabled),
            "sample_rate": float(self._l3_quality_shadow_sample_rate),
            "max_files": int(self._l3_quality_shadow_max_files),
            "lang_allowlist": tuple(self._l3_quality_shadow_lang_allowlist),
        }


class _FakeQueueRepositoryForStageGate(_FakeQueueRepository):
    def __init__(self) -> None:
        super().__init__()
        self.pending_split_calls: list[str] = []
        self.pending_age_calls: list[str] = []

    def get_pending_split_counts(self, now_iso: str) -> dict[str, int]:
        self.pending_split_calls.append(now_iso)
        return {
            "PENDING_AVAILABLE": 18,
            "PENDING_DEFERRED": 2,
            "PENDING_DEFERRED_FAST": 1,
            "PENDING_DEFERRED_HEAVY": 1,
        }

    def get_pending_age_stats(self, now_iso: str) -> dict[str, float | None]:
        self.pending_age_calls.append(now_iso)
        return {
            "oldest_pending_available_age_sec": 20.0,
            "oldest_pending_deferred_age_sec": 60.0,
            "p95_pending_available_age_sec": 8.0,
        }

    def get_deferred_drop_stats(self, top_k: int = 10) -> dict[str, object]:
        del top_k
        return {
            "dropped_total": 2,
            "dropped_ttl_expired_count": 1,
            "dropped_cap_total_count": 1,
            "dropped_cap_workspace_count": 0,
            "by_reason": {"l5_drop:ttl_expired": 1, "l5_drop:deferred_cap_total": 1},
            "by_workspace_topk": [{"repo_root": "/repo", "count": 2}],
            "by_language_topk": [{"language": "py", "count": 2}],
        }


class _FakeQueueRepositoryNoPendingForStageGate(_FakeQueueRepositoryForStageGate):
    def get_pending_split_counts(self, now_iso: str) -> dict[str, int]:
        self.pending_split_calls.append(now_iso)
        return {
            "PENDING_AVAILABLE": 0,
            "PENDING_DEFERRED": 0,
            "PENDING_DEFERRED_FAST": 0,
            "PENDING_DEFERRED_HEAVY": 0,
        }

    def get_pending_age_stats(self, now_iso: str) -> dict[str, float | None]:
        self.pending_age_calls.append(now_iso)
        return {
            "oldest_pending_available_age_sec": None,
            "oldest_pending_deferred_age_sec": None,
            "p95_pending_available_age_sec": None,
        }


class _FakeQueueRepositoryEligibleSubset(_FakeQueueRepository):
    def get_eligible_counts(self, now_iso: str) -> dict[str, int]:
        del now_iso
        return {
            "eligible_total_count": 35,
            "eligible_done_count": 35,
            "eligible_failed_count": 0,
            "eligible_deferred_count": 0,
        }


class _FakeQueueRepositoryWithPendingDetails(_FakeQueueRepository):
    def __init__(self) -> None:
        super().__init__()
        self.pending_split_calls: list[str] = []
        self.pending_age_calls: list[str] = []

    def get_pending_split_counts(self, now_iso: str) -> dict[str, int]:
        self.pending_split_calls.append(now_iso)
        return {
            "PENDING_AVAILABLE": 4,
            "PENDING_DEFERRED": 7,
            "PENDING_DEFERRED_FAST": 3,
            "PENDING_DEFERRED_HEAVY": 2,
        }

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


class _FakeQueueRepositoryForStageGateHigherPendingAge(_FakeQueueRepositoryForStageGate):
    def get_pending_age_stats(self, now_iso: str) -> dict[str, float | None]:
        self.pending_age_calls.append(now_iso)
        return {
            "oldest_pending_available_age_sec": 30.0,
            "oldest_pending_deferred_age_sec": 300.0,
            "p95_pending_available_age_sec": 12.0,
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
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    service._drain_enrich_queue(max_wait_sec=0.2)

    assert queue_repo.recover_calls >= 1
    assert service._last_drain_diagnostics["stale_running_recovered_count"] >= 1


def test_pipeline_perf_service_drain_processes_separate_l3_queue(tmp_path: Path) -> None:
    """drain 루프는 L2뿐 아니라 L3 큐도 함께 소진해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _FakeQueueRepository()
    collection = _FakeCollectionServiceWithSeparateL3()
    service = PipelinePerfService(
        file_collection_service=collection,
        queue_repo=queue_repo,
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    service._drain_enrich_queue(max_wait_sec=1.0)

    assert collection.l2_calls >= 1
    assert collection.l3_calls >= 1
    assert collection.unified_calls == 0


def test_pipeline_perf_service_drain_processes_l5_lane_queue(tmp_path: Path) -> None:
    """L5 lane pending이 남은 경우 drain 루프는 L5 processor를 호출해 소진해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    collection = _FakeCollectionServiceWithL5Only()
    queue_repo = _L5OnlyPendingQueueRepository(collection=collection)
    service = PipelinePerfService(
        file_collection_service=collection,
        queue_repo=queue_repo,
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    service._drain_enrich_queue(max_wait_sec=0.2)

    assert collection.l5_calls >= 1
    assert service._last_drain_diagnostics["drain_timeout_hit"] is False


def test_pipeline_perf_service_drain_treats_l5_heavy_deferred_pending_as_terminal(tmp_path: Path) -> None:
    """L5 heavy defer pending만 남으면 perf drain은 timeout 대신 정상 종료해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _HeavyDeferredOnlyQueueRepository()
    service = PipelinePerfService(
        file_collection_service=_IdleCollectionService(),
        queue_repo=queue_repo,
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    service._drain_enrich_queue(max_wait_sec=0.2)

    diag = service._last_drain_diagnostics
    assert diag["drain_timeout_hit"] is False
    assert diag.get("ignored_perf_deferred_pending_count") == 8


def test_pipeline_perf_service_drain_force_finalizes_l5_heavy_deferred_pending(tmp_path: Path) -> None:
    """heavy deferred pending만 남으면 마지막에 promote_to_l3_many로 강제 승격을 시도해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _HeavyDeferredPromotableQueueRepository()
    service = PipelinePerfService(
        file_collection_service=_IdleCollectionService(),
        queue_repo=queue_repo,
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    service._drain_enrich_queue(max_wait_sec=0.5)

    diag = service._last_drain_diagnostics
    assert queue_repo.promote_calls == 1
    assert len(queue_repo.promoted_job_ids) == 4
    assert diag["drain_timeout_hit"] is False
    assert diag.get("forced_heavy_finalize_count") == 4


def test_pipeline_perf_integrity_snapshot_includes_pending_split_age_and_eligible_counts(tmp_path: Path) -> None:
    """workspace integrity 스냅샷에 pending split/age와 strict eligible 집계가 포함되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _FakeQueueRepositoryWithPendingDetails()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithRepos(),
        queue_repo=queue_repo,
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()

    queue_detailed = snap["queue_counts_detailed"]
    assert isinstance(queue_detailed, dict)
    assert queue_detailed["PENDING_AVAILABLE"] == 4
    assert queue_detailed["PENDING_DEFERRED"] == 7
    assert queue_detailed["PENDING_DEFERRED_FAST"] == 3
    assert queue_detailed["PENDING_DEFERRED_HEAVY"] == 2
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


def test_pipeline_perf_integrity_snapshot_includes_quality_shadow_summary_without_affecting_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _FakeQueueRepositoryWithPendingDetails()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithQualityShadowSummary(),
        queue_repo=queue_repo,
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()
    quality = snap.get("quality_shadow_summary")
    assert isinstance(quality, dict)
    assert quality["enabled"] is True
    assert quality["sampled_files"] == 2
    assert quality["avg_recall_proxy_by_language"]["java"] == pytest.approx(0.75)
    assert "integrity_checks" in snap


def test_pipeline_perf_integrity_snapshot_adds_strict_eligible_match_check(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    queue_repo = _FakeQueueRepositoryWithPendingDetails()
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceWithRepos(),
        queue_repo=queue_repo,
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
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()
    checks = snap["integrity_checks"]
    # tool_ready=true 이지만 심볼 0건 파일(예: java/js)이 있을 수 있으므로 strict equality는 강제하지 않는다.
    assert checks["tool_ready_vs_symbol_files_match"] is True
    assert checks["eligible_done_vs_tool_ready_and_symbol_files_match"] is True
    assert snap["tool_readiness_symbol_gap_count"] == 3


def test_pipeline_perf_integrity_snapshot_includes_stage_exit_criteria_report(tmp_path: Path) -> None:
    """integrity snapshot은 Stage A/B/C exit criteria 자동평가를 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceForStageGate(),
        queue_repo=_FakeQueueRepositoryForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()

    stage_exit = snap.get("stage_exit")
    assert isinstance(stage_exit, dict)
    stage_a = stage_exit.get("stage_a_to_b")
    stage_b = stage_exit.get("stage_b_to_c")
    assert isinstance(stage_a, dict)
    assert isinstance(stage_b, dict)
    assert stage_a["passed"] is True
    assert stage_b["passed"] is True
    assert stage_a["checks"]["l3_parse_success_rate_tier1"] is True
    assert stage_a["checks"]["l3_degraded_rate_tier1"] is True
    assert stage_b["checks"]["search_quality_regression"] is True
    assert stage_b["checks"]["pending_age_p95"] is True
    assert stage_b["checks"]["l5_budget_rate_total"] is True
    assert snap["deferred_drop_stats"]["dropped_total"] == 2


def test_stage_exit_search_quality_requires_explicit_runtime_metric(tmp_path: Path) -> None:
    """search_quality_regression은 런타임 메트릭이 없으면 통과로 간주하면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    class _NoSearchMetricCollectionService(_FakeCollectionServiceForStageGate):
        def _lsp_runtime_metrics_snapshot(self) -> dict[str, int]:
            return {
                "l5_total_decisions": 100,
                "l5_total_admitted": 4,
                "l5_batch_decisions": 100,
                "l5_batch_admitted": 1,
            }

    service = PipelinePerfService(
        file_collection_service=_NoSearchMetricCollectionService(),
        queue_repo=_FakeQueueRepositoryForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()
    stage_b = snap["stage_exit"]["stage_b_to_c"]

    assert stage_b["checks"]["search_quality_regression"] is False
    assert stage_b["values"]["search_quality_regression_pct"] is None
    assert stage_b["values"]["search_quality_regression_metric_present"] is False


def test_stage_exit_treats_no_pending_as_non_degraded_and_pending_age_pass(tmp_path: Path) -> None:
    """pending이 0건이면 degraded/pending_age 체크는 통과로 간주해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceForStageGate(),
        queue_repo=_FakeQueueRepositoryNoPendingForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()
    stage_a = snap["stage_exit"]["stage_a_to_b"]
    stage_b = snap["stage_exit"]["stage_b_to_c"]

    assert stage_a["checks"]["l3_degraded_rate_tier1"] is True
    assert stage_b["checks"]["pending_age_p95"] is True


def test_pipeline_perf_integrity_snapshot_allows_eligible_subset_done_match(tmp_path: Path) -> None:
    """eligible subset이 전부 완료되면 tool_ready 전체 개수와 동일하지 않아도 정합성 통과해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceForStageGate(),
        queue_repo=_FakeQueueRepositoryEligibleSubset(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()
    checks = snap["integrity_checks"]
    assert checks["eligible_done_vs_tool_ready_and_symbol_files_match"] is True


def test_stage_exit_uses_quality_shadow_summary_when_runtime_metric_missing(tmp_path: Path) -> None:
    """search_quality_regression 런타임 메트릭이 없으면 quality shadow 요약값으로 보완해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    class _NoSearchMetricWithQualitySummary(_FakeCollectionServiceForStageGate):
        def _lsp_runtime_metrics_snapshot(self) -> dict[str, int]:
            return {
                "l5_total_decisions": 100,
                "l5_total_admitted": 4,
                "l5_batch_decisions": 100,
                "l5_batch_admitted": 1,
            }

        def _l3_quality_shadow_summary_snapshot(self) -> dict[str, object]:
            return {
                "enabled": True,
                "sampled_files": 2,
                "sampled_files_by_language": {"java": 2},
                "avg_recall_proxy_by_language": {"java": 0.995},
            }

    service = PipelinePerfService(
        file_collection_service=_NoSearchMetricWithQualitySummary(),
        queue_repo=_FakeQueueRepositoryForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    snap = service._collect_workspace_integrity_snapshot()
    stage_b = snap["stage_exit"]["stage_b_to_c"]

    assert stage_b["checks"]["search_quality_regression"] is True
    assert stage_b["values"]["search_quality_regression_metric_present"] is True
    assert stage_b["values"]["search_quality_regression_pct"] == pytest.approx(0.5)


def test_pipeline_perf_service_enables_l5_shadow_for_workspace_measurement(tmp_path: Path) -> None:
    """workspace_real 측정 중에는 L5 shadow metrics 수집을 강제해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    collection_service = _FakeCollectionServiceWithL5ModeSwitch()
    service = PipelinePerfService(
        file_collection_service=collection_service,
        queue_repo=_FakeQueueRepositoryForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )

    summary = service.run(repo_root=str(repo_dir), target_files=2000, profile="real_lsp_phase1_v1")
    workspace = next(item for item in summary["datasets"] if item["dataset_type"] == "workspace_real")
    stage_a = workspace["integrity"]["stage_exit"]["stage_a_to_b"]

    assert stage_a["values"]["l4_admission_rate_pct"] == pytest.approx(5.0)
    assert stage_a["checks"]["l4_admission_rate"] is True
    assert collection_service.mode_calls[0] == (True, False)
    assert collection_service.mode_calls[-1] == (False, False)


def test_pipeline_perf_service_enables_l3_quality_shadow_for_workspace_measurement(tmp_path: Path) -> None:
    """workspace_real 측정 중에는 L3 quality shadow를 켜고 종료 시 원복해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    collection_service = _FakeCollectionServiceWithL5AndL3ModeSwitch()
    service = PipelinePerfService(
        file_collection_service=collection_service,
        queue_repo=_FakeQueueRepositoryForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    collection_service.set_l3_quality_shadow_mode(
        enabled=True,
        sample_rate=0.25,
        max_files=123,
        lang_allowlist=("java", "kotlin"),
    )

    service.run(repo_root=str(repo_dir), target_files=2000, profile="real_lsp_phase1_v1")

    assert len(collection_service.l3_quality_shadow_mode_calls) >= 2
    assert collection_service.l3_quality_shadow_mode_calls[1] == (True, 1.0, 1000, ("java",))
    assert collection_service.l3_quality_shadow_mode_calls[-1] == (True, 0.25, 123, ("java", "kotlin"))


def test_stage_exit_uses_persisted_l4_admission_baseline_p50(tmp_path: Path) -> None:
    """baseline_p50 저장 후에는 Stage A L4 admission threshold가 baseline*1.3을 사용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    baseline_repo = PipelineStageBaselineRepository(db_path)

    service_first = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceForStageGate(),
        queue_repo=_FakeQueueRepositoryForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
        stage_baseline_repo=baseline_repo,
    )
    first_snap = service_first._collect_workspace_integrity_snapshot()
    first_stage_a = first_snap["stage_exit"]["stage_a_to_b"]
    assert first_stage_a["values"]["l4_admission_rate_baseline_p50"] == pytest.approx(4.0)
    assert first_stage_a["thresholds"]["l4_admission_rate_max_pct"] == pytest.approx(5.2)
    assert first_stage_a["checks"]["l4_admission_rate"] is True

    service_second = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceForStageGateHigherL5(),
        queue_repo=_FakeQueueRepositoryForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
        stage_baseline_repo=PipelineStageBaselineRepository(db_path),
    )
    second_snap = service_second._collect_workspace_integrity_snapshot()
    second_stage_a = second_snap["stage_exit"]["stage_a_to_b"]
    assert second_stage_a["values"]["l4_admission_rate_baseline_p50"] == pytest.approx(4.0)
    assert second_stage_a["values"]["l4_admission_rate_pct"] == pytest.approx(8.0)
    assert second_stage_a["thresholds"]["l4_admission_rate_max_pct"] == pytest.approx(5.2)
    assert second_stage_a["checks"]["l4_admission_rate"] is False


def test_stage_exit_uses_persisted_pending_age_baseline_for_regression_detection(tmp_path: Path) -> None:
    """Stage B pending_age 판정은 baseline 대비 악화(1.2x 초과) 시 fail 해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    baseline_repo = PipelineStageBaselineRepository(db_path)

    first = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceForStageGate(),
        queue_repo=_FakeQueueRepositoryForStageGate(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
        stage_baseline_repo=baseline_repo,
    )
    first_snap = first._collect_workspace_integrity_snapshot()
    first_stage_b = first_snap["stage_exit"]["stage_b_to_c"]
    assert first_stage_b["values"]["p95_pending_available_age_baseline_sec"] == pytest.approx(8.0)
    assert first_stage_b["thresholds"]["p95_pending_available_age_sec_max"] == pytest.approx(9.6)
    assert first_stage_b["checks"]["pending_age_p95"] is True

    second = PipelinePerfService(
        file_collection_service=_FakeCollectionServiceForStageGate(),
        queue_repo=_FakeQueueRepositoryForStageGateHigherPendingAge(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
        stage_baseline_repo=PipelineStageBaselineRepository(db_path),
    )
    second_snap = second._collect_workspace_integrity_snapshot()
    second_stage_b = second_snap["stage_exit"]["stage_b_to_c"]
    assert second_stage_b["values"]["p95_pending_available_age_sec"] == pytest.approx(12.0)
    assert second_stage_b["values"]["p95_pending_available_age_baseline_sec"] == pytest.approx(8.0)
    assert second_stage_b["thresholds"]["p95_pending_available_age_sec_max"] == pytest.approx(9.6)
    assert second_stage_b["checks"]["pending_age_p95"] is False


def test_build_dataset_result_supports_real_lsp_phase1_profile_for_workspace_real(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
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


def test_build_dataset_result_treats_realistic_v1_as_real_lsp_profile_for_workspace_real(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
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
            "config_snapshot": {"profile": "realistic_v1"},
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
    assert result["threshold_profile_applied"] == "realistic_v1"
    assert result["threshold_gate_passed"] is True
    assert result["integrity_gate_passed"] is True
    assert result["gate_passed"] is True
