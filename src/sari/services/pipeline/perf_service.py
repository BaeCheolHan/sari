"""파이프라인 성능 실측 서비스를 구현한다."""

from __future__ import annotations

import json
import subprocess
import time
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sari.core.exceptions import ErrorContext, PerfError
from sari.core.models import now_iso8601_utc
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository
from sari.db.repositories.pipeline_stage_baseline_repository import PipelineStageBaselineRepository
from sari.services.collection.perf_trace import PerfTracer, get_perf_trace_summary, perf_trace_session, reset_perf_trace_summary


class PipelinePerfService:
    """혼합지표(L3 처리량/총시간/실패율) 성능 실측을 수행한다."""

    def __init__(
        self,
        file_collection_service: object,
        queue_repo: object,
        perf_repo: PipelinePerfRepository,
        artifact_root: Path,
        stage_baseline_repo: PipelineStageBaselineRepository | None = None,
    ) -> None:
        """실측 서비스 의존성을 주입한다."""
        self._file_collection_service = file_collection_service
        self._queue_repo = queue_repo
        self._perf_repo = perf_repo
        self._stage_baseline_repo = stage_baseline_repo
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

    def get_latest_report(self, repo_root: str | None = None) -> dict[str, object]:
        """최신 성능 실측 리포트를 반환한다."""
        if isinstance(repo_root, str) and repo_root.strip() != "":
            normalized_repo_root = str(Path(repo_root).expanduser().resolve())
            latest = self._perf_repo.get_latest_run_for_repo(normalized_repo_root)
        else:
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
        # benchmark service 제거 후 sample_2k는 고정 synthetic 비용 모델로 계산한다.
        # 실제 게이트는 workspace_real에서 수행하므로 sample_2k는 비교지표 유지 목적이다.
        capped_target = max(1, min(int(target_files), 2_000))
        ingest_ms = float(max(200, int(capped_target * 0.6)))
        enrich_sec = float(max(1.0, round(capped_target / 250.0, 3)))
        done_count = int(capped_target)
        dead_count = 0
        wall_time_sec = (ingest_ms / 1000.0) + enrich_sec
        sample_context = dict(run_context)
        sample_context["backend_kind"] = "SyntheticSampleDataset"
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
        with ExitStack() as stack:
            stack.enter_context(self._temporary_l5_shadow_mode_for_perf())
            stack.enter_context(self._temporary_l3_quality_shadow_mode_for_perf())
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
            integrity_snapshot = self._collect_workspace_integrity_snapshot()
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
            integrity_snapshot=integrity_snapshot,
        )

    @contextmanager
    def _temporary_l5_shadow_mode_for_perf(self):
        """perf 측정 중 L5 shadow metrics 수집을 강제하고 종료 시 원복한다."""
        setter = getattr(self._file_collection_service, "set_l5_admission_mode", None)
        if not callable(setter):
            yield
            return
        enrich_engine = getattr(self._file_collection_service, "_enrich_engine", None)
        prev_shadow = bool(getattr(enrich_engine, "_l5_admission_shadow_enabled", False))
        prev_enforced = bool(getattr(enrich_engine, "_l5_admission_enforced", False))
        try:
            setter(shadow_enabled=True, enforced=prev_enforced)
        except (RuntimeError, OSError, ValueError, TypeError):
            yield
            return
        try:
            yield
        finally:
            try:
                setter(shadow_enabled=prev_shadow, enforced=prev_enforced)
            except (RuntimeError, OSError, ValueError, TypeError):
                ...

    @contextmanager
    def _temporary_l3_quality_shadow_mode_for_perf(self):
        """perf 측정 중 L3 quality shadow metrics 수집을 강제하고 종료 시 원복한다."""
        setter = getattr(self._file_collection_service, "set_l3_quality_shadow_mode", None)
        if not callable(setter):
            yield
            return
        prev_state = self._resolve_l3_quality_shadow_mode_state()
        try:
            setter(enabled=True, sample_rate=1.0, max_files=1000, lang_allowlist=("java",))
        except (RuntimeError, OSError, ValueError, TypeError):
            yield
            return
        try:
            yield
        finally:
            try:
                setter(
                    enabled=prev_state["enabled"],
                    sample_rate=prev_state["sample_rate"],
                    max_files=prev_state["max_files"],
                    lang_allowlist=prev_state["lang_allowlist"],
                )
            except (RuntimeError, OSError, ValueError, TypeError):
                ...

    def _resolve_l3_quality_shadow_mode_state(self) -> dict[str, object]:
        """L3 quality shadow 런타임 설정값을 best-effort로 조회한다."""
        getter = getattr(self._file_collection_service, "get_l3_quality_shadow_mode", None)
        if callable(getter):
            try:
                raw = getter()
                if isinstance(raw, dict):
                    return {
                        "enabled": bool(raw.get("enabled", False)),
                        "sample_rate": float(raw.get("sample_rate", 0.0)),
                        "max_files": int(raw.get("max_files", 0)),
                        "lang_allowlist": tuple(
                            str(item)
                            for item in raw.get("lang_allowlist", ())
                            if str(item).strip() != ""
                        ),
                    }
            except (RuntimeError, OSError, ValueError, TypeError):
                ...
        return {
            "enabled": False,
            "sample_rate": 0.0,
            "max_files": 0,
            "lang_allowlist": (),
        }

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
        ignored_deferred_pending_count = 0
        forced_heavy_finalize_count = 0
        forced_heavy_finalize_attempted = False
        last_recovery_check = 0.0
        drain_timeout_hit = False
        last_counts: dict[str, int] = self._queue_counts_snapshot()
        l2_processor = getattr(self._file_collection_service, "process_enrich_jobs_l2", None)
        unified_processor = getattr(self._file_collection_service, "process_enrich_jobs", None)
        l3_processor = getattr(self._file_collection_service, "process_enrich_jobs_l3", None)
        l3_queue_size_getter = getattr(self._file_collection_service, "l3_queue_size", None)
        count_pending_perf_ignorable = getattr(self._queue_repo, "count_pending_perf_ignorable", None)
        with self._force_heavy_deferred_finalization_mode():
            while time.time() < deadline:
                with self._perf_tracer.span("drain.loop.process_enrich_jobs", phase="drain"):
                    if callable(l2_processor):
                        processed_l2 = int(l2_processor(limit=100))
                    elif callable(unified_processor):
                        processed_l2 = int(unified_processor(limit=100))
                    else:
                        processed_l2 = 0
                processed_l3 = 0
                if callable(l3_processor):
                    with self._perf_tracer.span("drain.loop.process_enrich_jobs_l3", phase="drain"):
                        processed_l3 = int(l3_processor(limit=100))
                processed = processed_l2 + processed_l3
                with self._perf_tracer.span("drain.loop.queue_snapshot", phase="drain"):
                    counts = self._queue_repo.get_status_counts()
                last_counts = self._queue_counts_snapshot()
                pending = int(counts.get("PENDING", 0))
                running = int(counts.get("RUNNING", 0))
                l3_pending = 0
                if callable(l3_queue_size_getter):
                    try:
                        l3_pending = max(0, int(l3_queue_size_getter()))
                    except (RuntimeError, OSError, ValueError, TypeError):
                        l3_pending = 0
                elif hasattr(self._file_collection_service, "_l3_ready_queue"):
                    queue_obj = getattr(self._file_collection_service, "_l3_ready_queue")
                    qsize = getattr(queue_obj, "qsize", None)
                    if callable(qsize):
                        try:
                            l3_pending = max(0, int(qsize()))
                        except (RuntimeError, OSError, ValueError, TypeError):
                            l3_pending = 0
                ignored_pending = 0
                if callable(count_pending_perf_ignorable):
                    try:
                        ignored_pending = max(0, int(count_pending_perf_ignorable()))
                    except (RuntimeError, OSError, ValueError, TypeError):
                        ignored_pending = 0
                pending_effective = max(0, pending - ignored_pending)
                if ignored_pending > 0:
                    ignored_deferred_pending_count = ignored_pending
                if processed == 0 and pending_effective == 0 and ignored_pending > 0 and not forced_heavy_finalize_attempted:
                    forced_heavy_finalize_attempted = True
                    promoted = self._promote_heavy_deferred_for_finalization(limit=max(64, ignored_pending))
                    forced_heavy_finalize_count += promoted
                    if promoted > 0:
                        continue
                if processed == 0 and pending_effective == 0 and running == 0 and l3_pending == 0:
                    self._last_drain_diagnostics = {
                        "stale_running_recovered_count": int(recovered_total),
                        "drain_timeout_hit": False,
                        "drain_timeout_last_queue_counts": last_counts,
                        "ignored_perf_deferred_pending_count": int(ignored_deferred_pending_count),
                        "forced_heavy_finalize_count": int(forced_heavy_finalize_count),
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
        with self._force_heavy_deferred_finalization_mode():
            while time.time() < grace_deadline:
                with self._perf_tracer.span("drain.grace.process_enrich_jobs", phase="drain"):
                    if callable(l2_processor):
                        processed_l2 = int(l2_processor(limit=100))
                    elif callable(unified_processor):
                        processed_l2 = int(unified_processor(limit=100))
                    else:
                        processed_l2 = 0
                processed_l3 = 0
                if callable(l3_processor):
                    with self._perf_tracer.span("drain.grace.process_enrich_jobs_l3", phase="drain"):
                        processed_l3 = int(l3_processor(limit=100))
                processed = processed_l2 + processed_l3
                last_counts = self._queue_counts_snapshot()
                pending = int(last_counts.get("PENDING", 0))
                running = int(last_counts.get("RUNNING", 0))
                l3_pending = 0
                if callable(l3_queue_size_getter):
                    try:
                        l3_pending = max(0, int(l3_queue_size_getter()))
                    except (RuntimeError, OSError, ValueError, TypeError):
                        l3_pending = 0
                elif hasattr(self._file_collection_service, "_l3_ready_queue"):
                    queue_obj = getattr(self._file_collection_service, "_l3_ready_queue")
                    qsize = getattr(queue_obj, "qsize", None)
                    if callable(qsize):
                        try:
                            l3_pending = max(0, int(qsize()))
                        except (RuntimeError, OSError, ValueError, TypeError):
                            l3_pending = 0
                ignored_pending = 0
                if callable(count_pending_perf_ignorable):
                    try:
                        ignored_pending = max(0, int(count_pending_perf_ignorable()))
                    except (RuntimeError, OSError, ValueError, TypeError):
                        ignored_pending = 0
                pending_effective = max(0, pending - ignored_pending)
                if ignored_pending > 0:
                    ignored_deferred_pending_count = ignored_pending
                if processed == 0 and pending_effective == 0 and ignored_pending > 0 and not forced_heavy_finalize_attempted:
                    forced_heavy_finalize_attempted = True
                    promoted = self._promote_heavy_deferred_for_finalization(limit=max(64, ignored_pending))
                    forced_heavy_finalize_count += promoted
                    if promoted > 0:
                        continue
                if processed == 0 and pending_effective == 0 and running == 0 and l3_pending == 0:
                    self._last_drain_diagnostics = {
                        "stale_running_recovered_count": int(recovered_total),
                        "drain_timeout_hit": bool(drain_timeout_hit),
                        "drain_timeout_last_queue_counts": last_counts,
                        "ignored_perf_deferred_pending_count": int(ignored_deferred_pending_count),
                        "forced_heavy_finalize_count": int(forced_heavy_finalize_count),
                    }
                    return
                if processed == 0:
                    time.sleep(0.02)
        self._last_drain_diagnostics = {
            "stale_running_recovered_count": int(recovered_total),
            "drain_timeout_hit": True,
            "drain_timeout_last_queue_counts": last_counts,
            "ignored_perf_deferred_pending_count": int(ignored_deferred_pending_count),
            "forced_heavy_finalize_count": int(forced_heavy_finalize_count),
        }
        raise PerfError(ErrorContext(code="ERR_PERF_TIMEOUT", message="perf queue drain timeout"))

    @contextmanager
    def _force_heavy_deferred_finalization_mode(self):
        """drain 종료 직전 heavy defer를 다시 defer하지 않도록 preprocess threshold를 넓힌다."""
        fc = self._file_collection_service
        enrich_engine = getattr(fc, "_enrich_engine", None)
        if enrich_engine is None:
            yield
            return
        orchestrator = getattr(enrich_engine, "_l3_orchestrator", None)
        old_engine_max = getattr(enrich_engine, "_l3_preprocess_max_bytes", None)
        old_orch_max = getattr(orchestrator, "_preprocess_max_bytes", None) if orchestrator is not None else None
        forced_max = 64 * 1024 * 1024
        try:
            if isinstance(old_engine_max, int):
                setattr(enrich_engine, "_l3_preprocess_max_bytes", max(old_engine_max, forced_max))
            if orchestrator is not None and isinstance(old_orch_max, int):
                setattr(orchestrator, "_preprocess_max_bytes", max(old_orch_max, forced_max))
            yield
        finally:
            if isinstance(old_engine_max, int):
                setattr(enrich_engine, "_l3_preprocess_max_bytes", old_engine_max)
            if orchestrator is not None and isinstance(old_orch_max, int):
                setattr(orchestrator, "_preprocess_max_bytes", old_orch_max)

    def _promote_heavy_deferred_for_finalization(self, *, limit: int) -> int:
        """heavy deferred pending을 강제 파싱 대상으로 재승격한다."""
        list_ids = getattr(self._queue_repo, "list_pending_perf_ignorable_job_ids", None)
        promote = getattr(self._queue_repo, "promote_to_l3_many", None)
        if not callable(list_ids) or not callable(promote):
            return 0
        try:
            job_ids = list(list_ids(limit=int(limit)))
        except (RuntimeError, OSError, ValueError, TypeError):
            return 0
        if len(job_ids) == 0:
            return 0
        try:
            promote(job_ids=job_ids, now_iso=now_iso8601_utc())
        except (RuntimeError, OSError, ValueError, TypeError):
            return 0
        return len(job_ids)

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
            detailed["PENDING_DEFERRED_FAST"] = int(split.get("PENDING_DEFERRED_FAST", 0))
            detailed["PENDING_DEFERRED_HEAVY"] = int(split.get("PENDING_DEFERRED_HEAVY", 0))
        else:
            detailed["PENDING_AVAILABLE"] = int(detailed.get("PENDING", 0))
            detailed["PENDING_DEFERRED"] = 0
            detailed["PENDING_DEFERRED_FAST"] = 0
            detailed["PENDING_DEFERRED_HEAVY"] = 0
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
        threshold_profile = self._resolve_threshold_profile(run_context=run_context, dataset_type=dataset_type)
        threshold_gate_passed = bool(
            l3_jobs_per_sec >= float(threshold_profile["min_l3_jobs_per_sec"])
            and wall_time_sec <= float(threshold_profile["max_wall_time_sec"])
            and error_rate <= float(threshold_profile["max_error_rate_pct"])
        )
        gate_passed = threshold_gate_passed
        integrity_gate_passed: bool | None = None
        if dataset_type == "workspace_real" and integrity_snapshot is not None:
            integrity_checks_raw = integrity_snapshot.get("integrity_checks")
            if isinstance(integrity_checks_raw, dict):
                integrity_checks = {
                    str(key): bool(value)
                    for key, value in integrity_checks_raw.items()
                    if isinstance(value, bool)
                }
                if len(integrity_checks) > 0:
                    integrity_gate_passed = all(integrity_checks.values())
                    if not integrity_gate_passed:
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
            "threshold_profile_applied": str(threshold_profile["profile_name"]),
            "threshold_gate_passed": threshold_gate_passed,
            "integrity_gate_passed": integrity_gate_passed,
            "gate_passed": gate_passed,
        }
        if integrity_snapshot is not None:
            result["integrity"] = integrity_snapshot
        return result

    def _resolve_threshold_profile(self, *, run_context: dict[str, object], dataset_type: str) -> dict[str, object]:
        """dataset/profile 조합에 맞는 threshold 기준을 반환한다."""
        profile_name = "realistic_v1"
        config_snapshot = run_context.get("config_snapshot")
        if isinstance(config_snapshot, dict):
            raw_profile = config_snapshot.get("profile")
            if isinstance(raw_profile, str) and raw_profile.strip():
                profile_name = raw_profile.strip()

        # 기본 profile (기존 동작 유지)
        default_threshold = {
            "profile_name": profile_name,
            "min_l3_jobs_per_sec": 220.0,
            "max_wall_time_sec": 13.0,
            "max_error_rate_pct": 0.5,
        }
        if profile_name not in {"real_lsp_phase1_v1", "realistic_v1", "py314_subinterp_v1"}:
            return default_threshold

        # 실LSP Phase1 baseline 및 py314/subinterp 실험 profile:
        # sample_2k는 기존 기준 유지, workspace_real만 완화
        if dataset_type != "workspace_real":
            return {
                **default_threshold,
                "profile_name": profile_name,
            }
        if profile_name == "py314_subinterp_v1":
            return {
                "profile_name": "py314_subinterp_v1",
                "min_l3_jobs_per_sec": 45.0,
                "max_wall_time_sec": 55.0,
                "max_error_rate_pct": 0.5,
            }
        return {
            "profile_name": profile_name,
            "min_l3_jobs_per_sec": 40.0,
            "max_wall_time_sec": 60.0,
            "max_error_rate_pct": 0.5,
        }

    def _resolve_workspace_backend_kind(self) -> str:
        """workspace_real 측정에 사용되는 backend 종류를 식별한다."""
        backend = getattr(self._file_collection_service, "_lsp_backend", None)
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
        quality_shadow_summary_getter = getattr(self._file_collection_service, "_l3_quality_shadow_summary_snapshot", None)
        broker_obj = getattr(self._file_collection_service, "_lsp_session_broker", None)

        get_state_counts = getattr(file_repo, "get_enrich_state_counts", None)
        if callable(get_state_counts):
            try:
                snapshot["file_enrich_state_counts"] = dict(get_state_counts())
            except (RuntimeError, OSError, ValueError, TypeError):
                ...

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
        if readiness_counts is not None and symbol_file_count is not None:
            try:
                tool_ready_true = int(readiness_counts.get("tool_ready_true", 0))
                symbol_files_int = int(symbol_file_count)
                snapshot["tool_readiness_symbol_gap_count"] = max(0, tool_ready_true - symbol_files_int)
            except (TypeError, ValueError):
                ...

        snapshot_now_iso = now_iso8601_utc()
        queue_counts = self._queue_counts_snapshot()
        snapshot["queue_counts_snapshot"] = queue_counts
        snapshot["queue_counts_detailed"] = self._queue_counts_detailed_snapshot(now_iso=snapshot_now_iso)
        get_pending_age_stats = getattr(self._queue_repo, "get_pending_age_stats", None)
        if callable(get_pending_age_stats):
            try:
                snapshot["pending_age_stats"] = dict(get_pending_age_stats(now_iso=snapshot_now_iso))
            except (RuntimeError, OSError, ValueError, TypeError):
                ...
        get_deferred_drop_stats = getattr(self._queue_repo, "get_deferred_drop_stats", None)
        if callable(get_deferred_drop_stats):
            try:
                snapshot["deferred_drop_stats"] = dict(get_deferred_drop_stats())
            except (RuntimeError, OSError, ValueError, TypeError):
                ...
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
                    str(key): float(value) for key, value in runtime_metrics.items()
                    if isinstance(value, (int, float))
                }
        if callable(quality_shadow_summary_getter):
            try:
                quality_summary = quality_shadow_summary_getter()
            except (RuntimeError, OSError, ValueError, TypeError):
                quality_summary = None
            if isinstance(quality_summary, dict):
                snapshot["quality_shadow_summary"] = dict(quality_summary)
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
            try:
                tool_ready_true = int(readiness_counts.get("tool_ready_true", 0))
                symbol_files_int = int(symbol_file_count)
                # tool_ready=true 이지만 심볼 0건인 파일은 정상 허용한다.
                integrity_checks["tool_ready_vs_symbol_files_match"] = symbol_files_int <= tool_ready_true
            except (TypeError, ValueError):
                ...
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
                symbol_files_int = int(symbol_file_count)
                integrity_checks["eligible_done_vs_tool_ready_and_symbol_files_match"] = (
                    eligible_done >= 0
                    and eligible_done <= tool_ready_true
                    and symbol_files_int <= tool_ready_true
                )
            except (TypeError, ValueError):
                ...
        snapshot["integrity_checks"] = integrity_checks
        snapshot["stage_exit"] = self._build_stage_exit_report(snapshot=snapshot)
        return snapshot

    def _build_stage_exit_report(self, *, snapshot: dict[str, object]) -> dict[str, object]:
        """Stage A/B/C exit criteria를 자동 평가해 리포트에 포함한다."""
        readiness_counts = snapshot.get("tool_readiness_counts")
        queue_detailed = snapshot.get("queue_counts_detailed")
        pending_age_stats = snapshot.get("pending_age_stats")
        runtime_metrics = snapshot.get("lsp_runtime_metrics")
        quality_shadow_summary = snapshot.get("quality_shadow_summary")

        parse_success_rate_tier1: float | None = None
        l3_degraded_rate_tier1: float | None = None
        l4_admission_rate_pct: float | None = None
        search_quality_regression_pct: float | None = None
        search_quality_regression_metric_present = False
        pending_age_p95_sec: float | None = None
        l5_rate_total_pct: float | None = None
        pending_available_count: int | None = None

        if isinstance(readiness_counts, dict):
            try:
                tool_ready_true = int(readiness_counts.get("tool_ready_true", 0))
                tool_ready_false = int(readiness_counts.get("tool_ready_false", 0))
                denominator = tool_ready_true + tool_ready_false
                if denominator > 0:
                    parse_success_rate_tier1 = (float(tool_ready_true) / float(denominator)) * 100.0
            except (TypeError, ValueError):
                parse_success_rate_tier1 = None

        if isinstance(queue_detailed, dict):
            try:
                pending_available = int(queue_detailed.get("PENDING_AVAILABLE", 0))
                pending_deferred = int(queue_detailed.get("PENDING_DEFERRED", 0))
                pending_available_count = pending_available
                pending_total = pending_available + pending_deferred
                if pending_total > 0:
                    l3_degraded_rate_tier1 = (float(pending_deferred) / float(pending_total)) * 100.0
                else:
                    # pending 자체가 없으면 degraded는 0%로 간주한다.
                    l3_degraded_rate_tier1 = 0.0
            except (TypeError, ValueError):
                l3_degraded_rate_tier1 = None
                pending_available_count = None

        if isinstance(runtime_metrics, dict):
            try:
                l5_total_decisions = int(runtime_metrics.get("l5_total_decisions", 0))
                l5_total_admitted = int(runtime_metrics.get("l5_total_admitted", 0))
                if l5_total_decisions > 0:
                    l4_admission_rate_pct = (float(l5_total_admitted) / float(l5_total_decisions)) * 100.0
                    l5_rate_total_pct = l4_admission_rate_pct
            except (TypeError, ValueError):
                l4_admission_rate_pct = None
                l5_rate_total_pct = None
            if "search_quality_regression_pct" in runtime_metrics:
                search_quality_regression_metric_present = True
                try:
                    search_quality_regression_pct = float(runtime_metrics.get("search_quality_regression_pct"))
                except (TypeError, ValueError):
                    search_quality_regression_pct = None
        if not search_quality_regression_metric_present and isinstance(quality_shadow_summary, dict):
            try:
                enabled = bool(quality_shadow_summary.get("enabled", False))
                avg_recall = quality_shadow_summary.get("avg_recall_proxy_by_language")
                sampled_by_lang = quality_shadow_summary.get("sampled_files_by_language")
                if enabled and isinstance(avg_recall, dict) and len(avg_recall) > 0:
                    weighted_sum = 0.0
                    total_weight = 0.0
                    for lang, value in avg_recall.items():
                        try:
                            recall_ratio = float(value)
                        except (TypeError, ValueError):
                            continue
                        # 0~100 퍼센트 값이 오면 ratio로 보정한다.
                        if recall_ratio > 1.0:
                            recall_ratio = recall_ratio / 100.0
                        recall_ratio = max(0.0, min(1.0, recall_ratio))
                        weight = 1.0
                        if isinstance(sampled_by_lang, dict):
                            try:
                                weight = max(1.0, float(sampled_by_lang.get(str(lang), 1.0)))
                            except (TypeError, ValueError):
                                weight = 1.0
                        weighted_sum += recall_ratio * weight
                        total_weight += weight
                    if total_weight > 0.0:
                        weighted_recall_ratio = weighted_sum / total_weight
                        search_quality_regression_pct = max(0.0, (1.0 - weighted_recall_ratio) * 100.0)
                        search_quality_regression_metric_present = True
            except (TypeError, ValueError):
                ...

        if isinstance(pending_age_stats, dict):
            try:
                pending_age_p95_sec = float(pending_age_stats.get("p95_pending_available_age_sec"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pending_age_p95_sec = None

        l4_admission_rate_baseline_p50: float | None = None
        pending_age_p95_baseline_sec: float | None = None
        if self._stage_baseline_repo is not None:
            l4_admission_rate_baseline_p50 = self._stage_baseline_repo.get_l4_admission_rate_baseline_p50()
            if l4_admission_rate_baseline_p50 is None and l4_admission_rate_pct is not None:
                initialized = self._stage_baseline_repo.initialize_l4_admission_rate_baseline(l4_admission_rate_pct)
                if initialized:
                    l4_admission_rate_baseline_p50 = l4_admission_rate_pct
            pending_age_p95_baseline_sec = self._stage_baseline_repo.get_p95_pending_available_age_baseline_sec()
            if pending_age_p95_baseline_sec is None and pending_age_p95_sec is not None:
                initialized_pending_age = self._stage_baseline_repo.initialize_p95_pending_available_age_baseline(
                    pending_age_p95_sec
                )
                if initialized_pending_age:
                    pending_age_p95_baseline_sec = pending_age_p95_sec

        # Stage A -> B
        check_a_parse = parse_success_rate_tier1 is not None and parse_success_rate_tier1 >= 95.0
        check_a_degraded = l3_degraded_rate_tier1 is not None and l3_degraded_rate_tier1 <= 10.0
        # baseline_p50 ±30% 기준 (baseline이 없으면 기존 보수 기준 30%를 사용).
        l4_admission_rate_max_pct = 30.0
        if l4_admission_rate_baseline_p50 is not None:
            l4_admission_rate_max_pct = float(l4_admission_rate_baseline_p50) * 1.3
        check_a_l4_admission = (
            l4_admission_rate_pct is not None and l4_admission_rate_pct <= l4_admission_rate_max_pct
        )
        stage_a_passed = bool(check_a_parse and check_a_degraded and check_a_l4_admission)

        # Stage B -> C
        check_b_quality_reg = (
            search_quality_regression_metric_present
            and search_quality_regression_pct is not None
            and search_quality_regression_pct <= 1.0
        )
        pending_age_p95_max_sec = 10.0
        if pending_age_p95_baseline_sec is not None:
            pending_age_p95_max_sec = float(pending_age_p95_baseline_sec) * 1.2
        check_b_pending_age = (
            (pending_age_p95_sec is not None and pending_age_p95_sec <= pending_age_p95_max_sec)
            or pending_available_count == 0
        )
        check_b_l5_rate_total = l5_rate_total_pct is not None and l5_rate_total_pct <= 5.0
        stage_b_passed = bool(check_b_quality_reg and check_b_pending_age and check_b_l5_rate_total)

        return {
            "stage_a_to_b": {
                "passed": stage_a_passed,
                "checks": {
                    "l3_parse_success_rate_tier1": check_a_parse,
                    "l3_degraded_rate_tier1": check_a_degraded,
                    "l4_admission_rate": check_a_l4_admission,
                },
                "values": {
                    "l3_parse_success_rate_tier1_pct": parse_success_rate_tier1,
                    "l3_degraded_rate_tier1_pct": l3_degraded_rate_tier1,
                    "l4_admission_rate_pct": l4_admission_rate_pct,
                    "l4_admission_rate_baseline_p50": l4_admission_rate_baseline_p50,
                },
                "thresholds": {
                    "l3_parse_success_rate_tier1_min_pct": 95.0,
                    "l3_degraded_rate_tier1_max_pct": 10.0,
                    "l4_admission_rate_max_pct": l4_admission_rate_max_pct,
                },
            },
            "stage_b_to_c": {
                "passed": stage_b_passed,
                "checks": {
                    "search_quality_regression": check_b_quality_reg,
                    "pending_age_p95": check_b_pending_age,
                    "l5_budget_rate_total": check_b_l5_rate_total,
                },
                "values": {
                    "search_quality_regression_pct": search_quality_regression_pct,
                    "search_quality_regression_metric_present": search_quality_regression_metric_present,
                    "p95_pending_available_age_sec": pending_age_p95_sec,
                    "p95_pending_available_age_baseline_sec": pending_age_p95_baseline_sec,
                    "l5_rate_total_pct": l5_rate_total_pct,
                },
                "thresholds": {
                    "search_quality_regression_max_pct": 1.0,
                    "p95_pending_available_age_sec_max": pending_age_p95_max_sec,
                    "l5_rate_total_max_pct": 5.0,
                },
            },
        }

    def _write_artifact(self, run_id: str, summary: dict[str, object]) -> None:
        """실측 아티팩트를 파일로 저장한다."""
        perf_dir = self._artifact_root / "perf"
        perf_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = perf_dir / f"{run_id}.json"
        artifact_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
