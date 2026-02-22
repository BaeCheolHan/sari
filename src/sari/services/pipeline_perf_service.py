"""파이프라인 성능 실측 서비스를 구현한다."""

from __future__ import annotations

import json
import subprocess
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sari.core.exceptions import ErrorContext, PerfError
from sari.core.models import now_iso8601_utc
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository
from sari.services.collection.perf_trace import PerfTracer, get_perf_trace_summary, perf_trace_session, reset_perf_trace_summary


class PipelinePerfService:
    """혼합지표(L3 처리량/총시간/실패율) 성능 실측을 수행한다."""

    def __init__(
        self,
        file_collection_service: object,
        queue_repo: object,
        benchmark_service: object,
        perf_repo: PipelinePerfRepository,
        artifact_root: Path,
    ) -> None:
        """실측 서비스 의존성을 주입한다."""
        self._file_collection_service = file_collection_service
        self._queue_repo = queue_repo
        self._benchmark_service = benchmark_service
        self._perf_repo = perf_repo
        self._artifact_root = artifact_root
        self._last_drain_diagnostics: dict[str, object] = {}
        self._perf_tracer = PerfTracer(component="pipeline_perf_service")

    def run(
        self,
        repo_root: str,
        target_files: int,
        profile: str,
        dataset_mode: str = "isolated",
        *,
        fresh_db: bool = False,
        reset_probe_state: bool = False,
        cold_lsp_reset: bool = False,
        workspace_exclude_globs: tuple[str, ...] = (),
    ) -> dict[str, object]:
        """샘플 2k + 실데이터 2트랙 실측을 실행하고 요약을 반환한다."""
        root = Path(repo_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise PerfError(ErrorContext(code="ERR_REPO_NOT_FOUND", message="repo 경로를 찾을 수 없습니다"))
        if target_files <= 0:
            raise PerfError(ErrorContext(code="ERR_INVALID_TARGET_FILES", message="target_files는 1 이상이어야 합니다"))
        if profile.strip() == "":
            raise PerfError(ErrorContext(code="ERR_INVALID_PROFILE", message="profile은 비어 있을 수 없습니다"))
        normalized_dataset_mode = dataset_mode.strip().lower()
        if normalized_dataset_mode not in ("isolated", "legacy"):
            raise PerfError(ErrorContext(code="ERR_INVALID_DATASET_MODE", message="dataset_mode must be isolated or legacy"))
        cold_requested = bool(cold_lsp_reset)
        pre_state_reset_requested = bool(reset_probe_state or cold_requested)
        run_context = self._prepare_measurement_context(
            repo_root=str(root),
            target_files=target_files,
            profile=profile,
            dataset_mode=normalized_dataset_mode,
            fresh_db=bool(fresh_db),
            pre_state_reset=pre_state_reset_requested,
            cold_lsp_reset=cold_requested,
            workspace_exclude_globs=workspace_exclude_globs,
        )
        self._apply_pre_run_reset(
            fresh_db=bool(fresh_db),
            reset_probe_state=pre_state_reset_requested,
            cold_lsp_reset=cold_requested,
        )

        started_at = now_iso8601_utc()
        run_id = self._perf_repo.create_run(
            repo_root=str(root),
            target_files=target_files,
            profile=profile,
            started_at=started_at,
        )
        try:
            trace_session_id = f"perf_run:{run_id}"
            reset_perf_trace_summary(trace_session_id)
            trace_context = perf_trace_session(trace_session_id)
        except (RuntimeError, OSError, ValueError, TypeError):
            trace_session_id = None
            trace_context = nullcontext()
        try:
            with trace_context:
                if normalized_dataset_mode == "isolated":
                    workspace_dataset = self._measure_workspace_dataset(
                        repo_root=str(root),
                        dataset_mode=normalized_dataset_mode,
                        run_context=run_context,
                        workspace_exclude_globs=workspace_exclude_globs,
                    )
                    sample_dataset = self._measure_sample_dataset(
                        repo_root=str(root),
                        target_files=target_files,
                        dataset_mode=normalized_dataset_mode,
                        run_context=run_context,
                    )
                    datasets = [workspace_dataset, sample_dataset]
                else:
                    sample_dataset = self._measure_sample_dataset(
                        repo_root=str(root),
                        target_files=target_files,
                        dataset_mode=normalized_dataset_mode,
                        run_context=run_context,
                    )
                    workspace_dataset = self._measure_workspace_dataset(
                        repo_root=str(root),
                        dataset_mode=normalized_dataset_mode,
                        run_context=run_context,
                        workspace_exclude_globs=workspace_exclude_globs,
                    )
                    datasets = [sample_dataset, workspace_dataset]
            gate_passed = all(bool(item.get("gate_passed")) for item in datasets)
            if trace_session_id is not None:
                for item in datasets:
                    if item.get("dataset_type") == "workspace_real":
                        integrity = item.setdefault("integrity", {})
                        if isinstance(integrity, dict):
                            integrity["perf_trace_summary"] = get_perf_trace_summary(trace_session_id, top_n=200)
            summary: dict[str, object] = {
                "run_id": run_id,
                "status": "COMPLETED",
                "repo_root": str(root),
                "threshold_profile": profile,
                "dataset_mode": normalized_dataset_mode,
                "target_files": target_files,
                "gate_passed": gate_passed,
                "datasets": datasets,
            }
            self._write_artifact(run_id=run_id, summary=summary)
            self._perf_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="COMPLETED",
                summary=summary,
            )
            return summary
        except PerfError as exc:
            failed = {
                "run_id": run_id,
                "status": "FAILED",
                "repo_root": str(root),
                "target_files": target_files,
                "dataset_mode": normalized_dataset_mode,
                "threshold_profile": profile,
                "error": str(exc),
            }
            self._perf_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="FAILED",
                summary=failed,
            )
            raise
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            failed = {
                "run_id": run_id,
                "status": "FAILED",
                "repo_root": str(root),
                "target_files": target_files,
                "dataset_mode": normalized_dataset_mode,
                "threshold_profile": profile,
                "error": str(exc),
            }
            self._perf_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="FAILED",
                summary=failed,
            )
            raise PerfError(ErrorContext(code="ERR_PERF_RUN_FAILED", message=f"perf run failed: {exc}")) from exc

    def get_latest_report(self) -> dict[str, object]:
        """최신 성능 실측 리포트를 반환한다."""
        latest = self._perf_repo.get_latest_run()
        if latest is None:
            raise PerfError(ErrorContext(code="ERR_PERF_NOT_FOUND", message="no perf run found"))
        summary = latest.get("summary")
        if isinstance(summary, dict):
            return summary
        return latest

    def _measure_sample_dataset(
        self,
        repo_root: str,
        target_files: int,
        dataset_mode: str,
        run_context: dict[str, object],
    ) -> dict[str, object]:
        """샘플 2k 기준 실측 지표를 계산한다."""
        summary = self._benchmark_service.run(
            repo_root=repo_root,
            target_files=target_files,
            profile="default",
            language_filter=None,
            per_language_report=False,
        )
        scan = summary.get("scan", {})
        enrich = summary.get("enrich", {})
        ingest_ms = float(scan.get("ingest_latency_ms_p95", 0.0))
        enrich_sec = float(enrich.get("completion_sec", 0.0))
        done_count = int(enrich.get("done_count", 0))
        dead_count = int(enrich.get("dead_count", 0))
        wall_time_sec = (ingest_ms / 1000.0) + enrich_sec
        sample_context = dict(run_context)
        sample_context["backend_kind"] = self._resolve_benchmark_backend_kind()
        return self._build_dataset_result(
            dataset_type="sample_2k",
            repo_scope=repo_root,
            done_count=done_count,
            dead_count=dead_count,
            l3_elapsed_sec=enrich_sec,
            wall_time_sec=wall_time_sec,
            count_mode="summary",
            start_counts=None,
            end_counts=None,
            measurement_scope=f"sample_2k_{dataset_mode}",
            run_context=sample_context,
        )

    def _measure_workspace_dataset(
        self,
        repo_root: str,
        dataset_mode: str,
        run_context: dict[str, object],
        workspace_exclude_globs: tuple[str, ...] = (),
    ) -> dict[str, object]:
        """실데이터 기준 실측 지표를 계산한다."""
        start_counts = self._queue_counts_snapshot()
        scan_started = time.perf_counter()
        exclude_ctx_factory = getattr(self._file_collection_service, "temporary_scan_exclude_globs", None)
        exclude_ctx = (
            exclude_ctx_factory(workspace_exclude_globs)
            if callable(exclude_ctx_factory)
            else nullcontext()
        )
        with self._perf_tracer.span("measure_workspace.scan_once", phase="scan", repo_root=repo_root):
            with exclude_ctx:
                self._file_collection_service.scan_once(repo_root=repo_root)
        scan_elapsed_sec = float(time.perf_counter() - scan_started)
        enrich_started = time.perf_counter()
        self._last_drain_diagnostics = {}
        with self._perf_tracer.span("measure_workspace.drain_enrich_queue", phase="drain", repo_root=repo_root):
            self._drain_enrich_queue(max_wait_sec=120.0)
        enrich_elapsed_sec = float(time.perf_counter() - enrich_started)
        end_counts = self._queue_counts_snapshot()
        done_count = max(0, int(end_counts.get("DONE", 0)) - int(start_counts.get("DONE", 0)))
        dead_count = max(0, int(end_counts.get("DEAD", 0)) - int(start_counts.get("DEAD", 0)))
        wall_time_sec = scan_elapsed_sec + enrich_elapsed_sec
        workspace_context = dict(run_context)
        workspace_context["backend_kind"] = self._resolve_workspace_backend_kind()
        return self._build_dataset_result(
            dataset_type="workspace_real",
            repo_scope=repo_root,
            done_count=done_count,
            dead_count=dead_count,
            l3_elapsed_sec=enrich_elapsed_sec,
            wall_time_sec=wall_time_sec,
            count_mode="delta",
            start_counts=start_counts,
            end_counts=end_counts,
            measurement_scope=f"workspace_real_{dataset_mode}",
            run_context=workspace_context,
            integrity_snapshot=self._collect_workspace_integrity_snapshot(),
        )

    def _apply_pre_run_reset(self, *, fresh_db: bool, reset_probe_state: bool, cold_lsp_reset: bool) -> None:
        """요청된 cold 측정 reset을 실행한다."""
        if fresh_db:
            # 현재 perf run에서는 DB 파일 재생성 대신 논리 reset만 허용한다.
            reset_runtime = getattr(self._file_collection_service, "reset_runtime_state", None)
            if callable(reset_runtime):
                reset_runtime()
        if reset_probe_state:
            reset_probe = getattr(self._file_collection_service, "reset_probe_state", None)
            if not callable(reset_probe):
                raise PerfError(ErrorContext(code="ERR_PERF_RESET_UNSUPPORTED", message="reset_probe_state capability is required"))
            reset_probe()
        if cold_lsp_reset:
            reset_lsp = getattr(self._file_collection_service, "reset_lsp_runtime", None)
            if not callable(reset_lsp):
                raise PerfError(ErrorContext(code="ERR_PERF_RESET_UNSUPPORTED", message="reset_lsp_runtime capability is required"))
            reset_lsp()

    def _prepare_measurement_context(
        self,
        *,
        repo_root: str,
        target_files: int,
        profile: str,
        dataset_mode: str,
        fresh_db: bool,
        pre_state_reset: bool,
        cold_lsp_reset: bool,
        workspace_exclude_globs: tuple[str, ...],
    ) -> dict[str, object]:
        """측정 범위 메타데이터를 구성한다."""
        return {
            "fresh_db": fresh_db,
            "fresh_db_mode": "logical_reset",
            "pre_state_reset": pre_state_reset,
            "cold_lsp_reset": cold_lsp_reset,
            "os_page_cache_cold": False,
            "git_sha": self._resolve_git_sha(repo_root),
            "config_snapshot": {
                "target_files": target_files,
                "profile": profile,
                "dataset_mode": dataset_mode,
                "workspace_exclude_globs": [item for item in workspace_exclude_globs if item.strip() != ""],
            },
        }

    def _resolve_git_sha(self, repo_root: str) -> str | None:
        """repo의 git sha를 best-effort로 반환한다."""
        try:
            output = subprocess.check_output(
                ["git", "-C", repo_root, "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if output == "":
                return None
            return output
        except (OSError, subprocess.SubprocessError):
            return None

    def _drain_enrich_queue(self, max_wait_sec: float) -> None:
        """큐가 비워질 때까지 보강 작업을 반복 실행한다."""
        deadline = time.time() + max_wait_sec
        recovered_total = 0
        last_recovery_check = 0.0
        drain_timeout_hit = False
        last_counts: dict[str, int] = self._queue_counts_snapshot()
        while time.time() < deadline:
            with self._perf_tracer.span("drain.loop.process_enrich_jobs", phase="drain"):
                processed = int(self._file_collection_service.process_enrich_jobs(limit=100))
            with self._perf_tracer.span("drain.loop.queue_snapshot", phase="drain"):
                counts = self._queue_repo.get_status_counts()
            last_counts = self._queue_counts_snapshot()
            pending = int(counts.get("PENDING", 0))
            running = int(counts.get("RUNNING", 0))
            if processed == 0 and pending == 0 and running == 0:
                self._last_drain_diagnostics = {
                    "stale_running_recovered_count": int(recovered_total),
                    "drain_timeout_hit": False,
                    "drain_timeout_last_queue_counts": last_counts,
                }
                return
            now = time.time()
            if now - last_recovery_check >= 2.0:
                with self._perf_tracer.span("drain.loop.recover_stale_running", phase="drain"):
                    recovered_total += self._recover_stale_running_jobs()
                last_recovery_check = now
            if processed == 0:
                time.sleep(0.02)
        drain_timeout_hit = True
        with self._perf_tracer.span("drain.final_recover_stale_running", phase="drain"):
            recovered_total += self._recover_stale_running_jobs()
        grace_deadline = time.time() + 3.0
        while time.time() < grace_deadline:
            with self._perf_tracer.span("drain.grace.process_enrich_jobs", phase="drain"):
                processed = int(self._file_collection_service.process_enrich_jobs(limit=100))
            last_counts = self._queue_counts_snapshot()
            pending = int(last_counts.get("PENDING", 0))
            running = int(last_counts.get("RUNNING", 0))
            if processed == 0 and pending == 0 and running == 0:
                self._last_drain_diagnostics = {
                    "stale_running_recovered_count": int(recovered_total),
                    "drain_timeout_hit": bool(drain_timeout_hit),
                    "drain_timeout_last_queue_counts": last_counts,
                }
                return
            if processed == 0:
                time.sleep(0.02)
        self._last_drain_diagnostics = {
            "stale_running_recovered_count": int(recovered_total),
            "drain_timeout_hit": True,
            "drain_timeout_last_queue_counts": last_counts,
        }
        raise PerfError(ErrorContext(code="ERR_PERF_TIMEOUT", message="perf queue drain timeout"))

    def _recover_stale_running_jobs(self) -> int:
        """perf 측정 경로에서만 오래된 RUNNING 작업을 FAILED로 회수한다."""
        recover = getattr(self._queue_repo, "recover_stale_running_to_failed", None)
        if not callable(recover):
            return 0
        now_iso = now_iso8601_utc()
        stale_before = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).isoformat()
        try:
            return int(recover(now_iso=now_iso, stale_before_iso=stale_before))
        except (RuntimeError, OSError, ValueError, TypeError):
            return 0

    def _queue_counts_snapshot(self) -> dict[str, int]:
        """큐 상태 카운트를 정규화해 스냅샷으로 반환한다."""
        raw_counts = self._queue_repo.get_status_counts()
        return {
            "PENDING": int(raw_counts.get("PENDING", 0)),
            "RUNNING": int(raw_counts.get("RUNNING", 0)),
            "FAILED": int(raw_counts.get("FAILED", 0)),
            "DONE": int(raw_counts.get("DONE", 0)),
            "DEAD": int(raw_counts.get("DEAD", 0)),
        }

    def _queue_counts_detailed_snapshot(self, now_iso: str) -> dict[str, int]:
        """큐 상태 카운트 + deferred 분리 집계를 함께 반환한다."""
        counts = self._queue_counts_snapshot()
        detailed = dict(counts)
        get_pending_split_counts = getattr(self._queue_repo, "get_pending_split_counts", None)
        if callable(get_pending_split_counts):
            try:
                split = dict(get_pending_split_counts(now_iso=now_iso))
            except (RuntimeError, OSError, ValueError, TypeError):
                split = {}
            detailed["PENDING_AVAILABLE"] = int(split.get("PENDING_AVAILABLE", max(0, detailed.get("PENDING", 0))))
            detailed["PENDING_DEFERRED"] = int(split.get("PENDING_DEFERRED", 0))
        else:
            detailed["PENDING_AVAILABLE"] = int(detailed.get("PENDING", 0))
            detailed["PENDING_DEFERRED"] = 0
        return detailed

    def _build_dataset_result(
        self,
        dataset_type: str,
        repo_scope: str,
        done_count: int,
        dead_count: int,
        l3_elapsed_sec: float,
        wall_time_sec: float,
        count_mode: str,
        start_counts: dict[str, int] | None,
        end_counts: dict[str, int] | None,
        measurement_scope: str,
        run_context: dict[str, object],
        integrity_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """단일 데이터셋 실측 결과와 게이트 판정을 생성한다."""
        denominator = done_count + dead_count
        error_rate = 0.0 if denominator == 0 else (float(dead_count) / float(denominator)) * 100.0
        l3_jobs_per_sec = 0.0 if l3_elapsed_sec <= 0 else float(done_count) / l3_elapsed_sec
        threshold_gate_passed = bool(l3_jobs_per_sec >= 220.0 and wall_time_sec <= 13.0 and error_rate <= 0.5)
        gate_passed = threshold_gate_passed
        if dataset_type == "workspace_real" and integrity_snapshot is not None:
            integrity_checks_raw = integrity_snapshot.get("integrity_checks")
            if isinstance(integrity_checks_raw, dict):
                integrity_checks = {
                    str(key): bool(value)
                    for key, value in integrity_checks_raw.items()
                    if isinstance(value, bool)
                }
                if len(integrity_checks) > 0 and not all(integrity_checks.values()):
                    gate_passed = False
        result = {
            "dataset_type": dataset_type,
            "repo_scope": repo_scope,
            "count_mode": count_mode,
            "measurement_scope": measurement_scope,
            "run_context": run_context,
            "start_counts": start_counts,
            "end_counts": end_counts,
            "done_count": done_count,
            "dead_count": dead_count,
            "l3_jobs_per_sec": round(l3_jobs_per_sec, 4),
            "wall_time_sec": round(wall_time_sec, 4),
            "error_rate": round(error_rate, 4),
            "gate_passed": gate_passed,
        }
        if integrity_snapshot is not None:
            result["integrity"] = integrity_snapshot
        return result

    def _resolve_workspace_backend_kind(self) -> str:
        """workspace_real 측정에 사용되는 backend 종류를 식별한다."""
        backend = getattr(self._file_collection_service, "_lsp_backend", None)
        if backend is None:
            return "unknown"
        return type(backend).__name__

    def _resolve_benchmark_backend_kind(self) -> str:
        """sample_2k 측정에 사용되는 benchmark backend 종류를 식별한다."""
        bench_fc = getattr(self._benchmark_service, "_file_collection_service", None)
        backend = getattr(bench_fc, "_lsp_backend", None)
        if backend is None:
            return "unknown"
        return type(backend).__name__

    def _collect_workspace_integrity_snapshot(self) -> dict[str, object]:
        """workspace_real 측정 직후 정합성 스냅샷을 best-effort로 수집한다."""
        snapshot: dict[str, object] = {}
        file_repo = getattr(self._file_collection_service, "_file_repo", None)
        readiness_repo = getattr(self._file_collection_service, "_readiness_repo", None)
        lsp_repo = getattr(self._file_collection_service, "_lsp_repo", None)
        runtime_metrics_getter = getattr(self._file_collection_service, "_lsp_runtime_metrics_snapshot", None)
        broker_obj = getattr(self._file_collection_service, "_lsp_session_broker", None)

        get_state_counts = getattr(file_repo, "get_enrich_state_counts", None)
        if callable(get_state_counts):
            try:
                snapshot["file_enrich_state_counts"] = dict(get_state_counts())
            except (RuntimeError, OSError, ValueError, TypeError):
                pass

        count_by_tool_ready = getattr(readiness_repo, "count_by_tool_ready", None)
        if callable(count_by_tool_ready):
            try:
                readiness_counts = dict(count_by_tool_ready())
                snapshot["tool_readiness_counts"] = readiness_counts
            except (RuntimeError, OSError, ValueError, TypeError):
                readiness_counts = None
            else:
                readiness_counts = readiness_counts
        else:
            readiness_counts = None

        count_distinct_symbol_files = getattr(lsp_repo, "count_distinct_symbol_files", None)
        if callable(count_distinct_symbol_files):
            try:
                symbol_file_count = int(count_distinct_symbol_files())
                snapshot["lsp_symbol_distinct_files"] = symbol_file_count
            except (RuntimeError, OSError, ValueError, TypeError):
                symbol_file_count = None
            else:
                symbol_file_count = symbol_file_count
        else:
            symbol_file_count = None

        snapshot_now_iso = now_iso8601_utc()
        queue_counts = self._queue_counts_snapshot()
        snapshot["queue_counts_snapshot"] = queue_counts
        snapshot["queue_counts_detailed"] = self._queue_counts_detailed_snapshot(now_iso=snapshot_now_iso)
        get_pending_age_stats = getattr(self._queue_repo, "get_pending_age_stats", None)
        if callable(get_pending_age_stats):
            try:
                snapshot["pending_age_stats"] = dict(get_pending_age_stats(now_iso=snapshot_now_iso))
            except (RuntimeError, OSError, ValueError, TypeError):
                pass
        get_eligible_counts = getattr(self._queue_repo, "get_eligible_counts", None)
        if callable(get_eligible_counts):
            try:
                snapshot["eligible_counts"] = dict(get_eligible_counts(now_iso=snapshot_now_iso))
                snapshot["eligible_counts_mode"] = "strict_queue_phaseB_v1"
            except (RuntimeError, OSError, ValueError, TypeError):
                snapshot["eligible_counts_mode"] = "deferred_split_only_phaseA"
        else:
            snapshot["eligible_counts_mode"] = "deferred_split_only_phaseA"
        if len(self._last_drain_diagnostics) > 0:
            snapshot["drain"] = dict(self._last_drain_diagnostics)
        if callable(runtime_metrics_getter):
            try:
                runtime_metrics = runtime_metrics_getter()
            except (RuntimeError, OSError, ValueError, TypeError):
                runtime_metrics = None
            if isinstance(runtime_metrics, dict):
                snapshot["lsp_runtime_metrics"] = {
                    str(key): int(value) for key, value in runtime_metrics.items()
                    if isinstance(value, (int, float))
                }
        broker_snapshot_getter = getattr(broker_obj, "get_snapshot", None) if broker_obj is not None else None
        if callable(broker_snapshot_getter):
            try:
                broker_snapshot = broker_snapshot_getter()
            except (RuntimeError, OSError, ValueError, TypeError):
                broker_snapshot = None
            if broker_snapshot is not None:
                active_by_lang = getattr(broker_snapshot, "active_sessions_by_language", None)
                active_by_group = getattr(broker_snapshot, "active_sessions_by_budget_group", None)
                if isinstance(active_by_lang, dict) or isinstance(active_by_group, dict):
                    snapshot["broker_snapshot"] = {
                        "active_sessions_by_language": {
                            str(k): int(v) for k, v in (active_by_lang or {}).items()
                        },
                        "active_sessions_by_budget_group": {
                            str(k): int(v) for k, v in (active_by_group or {}).items()
                        },
                    }
        integrity_checks: dict[str, bool] = {
            "queue_running_zero": int(queue_counts.get("RUNNING", 0)) == 0,
            "measurement_backend_real_lsp": self._resolve_workspace_backend_kind() == "SolidLspExtractionBackend",
        }
        if readiness_counts is not None and symbol_file_count is not None:
            integrity_checks["tool_ready_vs_symbol_files_match"] = int(readiness_counts.get("tool_ready_true", 0)) == int(symbol_file_count)
        eligible_counts = snapshot.get("eligible_counts")
        if (
            isinstance(eligible_counts, dict)
            and readiness_counts is not None
            and symbol_file_count is not None
            and snapshot.get("eligible_counts_mode") == "strict_queue_phaseB_v1"
        ):
            try:
                eligible_done = int(eligible_counts.get("eligible_done_count", -1))
                tool_ready_true = int(readiness_counts.get("tool_ready_true", -2))
                integrity_checks["eligible_done_vs_tool_ready_and_symbol_files_match"] = (
                    eligible_done == tool_ready_true == int(symbol_file_count)
                )
            except (TypeError, ValueError):
                pass
        snapshot["integrity_checks"] = integrity_checks
        return snapshot

    def _write_artifact(self, run_id: str, summary: dict[str, object]) -> None:
        """실측 아티팩트를 파일로 저장한다."""
        perf_dir = self._artifact_root / "perf"
        perf_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = perf_dir / f"{run_id}.json"
        artifact_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
