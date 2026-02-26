"""L2/L3 보강 파이프라인 전용 엔진."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import queue
import time
import logging
from typing import Callable

from solidlsp.ls_config import Language

from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode, L5RejectReason
from sari.core.language_registry import get_enabled_language_names, resolve_language_from_path
from sari.core.models import (
    CollectedFileBodyDTO,
    FileEnrichJobDTO,
    ToolReadinessStateDTO,
    now_iso8601_utc,
)
from sari.services.collection.l5_admission_policy import LanguageL5Policy, TokenBucket
from sari.services.collection.l5_admission_runtime_service import (
    L5AdmissionRuntimeService,
    L5AdmissionRuntimeState,
)
from sari.services.collection.l5_default_policy_builder import build_default_language_policy_map
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.services.collection.error_policy import CollectionErrorPolicy
from sari.services.collection.l3_asset_loader import L3AssetLoader
from sari.services.collection.l3_orchestrator import L3Orchestrator
from sari.services.collection.l3_skip_runtime_service import L3SkipRuntimeService
from sari.services.collection.l3_runtime_coordination_service import L3RuntimeCoordinationService
from sari.services.collection.l3_scheduling_service import L3SchedulingService
from sari.services.collection.l3_error_handling_service import L3ErrorHandlingService
from sari.services.collection.l3_language_config_parser import (
    parse_l3_supported_languages as _parse_l3_supported_languages_shared,
    parse_lsp_probe_l1_languages as _parse_lsp_probe_l1_languages_shared,
)
from sari.services.collection.l3_failure_classifier import (
    classify_l3_extract_failure_kind,
    extract_error_code_from_lsp_error_message,
    is_scope_escalation_trigger_error_for_l3,
    next_scope_level_for_l3_escalation,
)
from sari.services.collection.enrich_engine_wiring import wire_engine_services, wire_runtime_processors
from sari.services.collection.enrich_result_dto import (
    _L3JobResultDTO,
    _L3ResultBuffersDTO,
)
from sari.services.collection.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)
from sari.services.collection.perf_trace import PerfTracer

log = logging.getLogger(__name__)


class EnrichEngine:
    """파일 보강(L2/L3) 처리와 bootstrap 모드를 관리한다."""

    def __init__(
        self,
        *,
        file_repo: object,
        enrich_queue_repo: object,
        body_repo: object,
        lsp_repo: object,
        readiness_repo: object,
        policy: object,
        lsp_backend: object,
        policy_repo: object | None,
        event_repo: object | None,
        vector_index_sink: object | None,
        tool_layer_repo: ToolDataLayerRepository | None,
        run_mode: str,
        persist_body_for_read: bool,
        l3_ready_queue: queue.Queue[FileEnrichJobDTO],
        error_policy: CollectionErrorPolicy,
        record_enrich_latency: Callable[[float], None],
        assert_parent_alive: Callable[[str], None],
        flush_batch_size: int,
        flush_interval_sec: float,
        flush_max_body_bytes: int,
        l3_parallel_enabled: bool,
        l3_executor_max_workers: int,
        l3_recent_success_ttl_sec: int,
        l3_backpressure_on_interactive: bool,
        l3_backpressure_cooldown_ms: int,
        l3_supported_languages: tuple[str, ...],
        lsp_probe_l1_languages: tuple[str, ...],
        l5_admission_shadow_enabled: bool = False,
        l5_admission_enforced: bool = False,
        l5_call_rate_total_max: float = 0.05,
        l5_call_rate_batch_max: float = 0.01,
        l5_calls_per_min_per_lang_max: int = 30,
        l5_tokens_per_10sec_global_max: int = 120,
        l5_tokens_per_10sec_per_lang_max: int = 30,
        l5_tokens_per_10sec_per_workspace_max: int = 20,
        l3_query_compile_cache_enabled: bool = True,
        l3_query_compile_ms_budget: float = 10.0,
        l3_query_budget_ms: float = 30.0,
        l3_asset_mode: str = "shadow",
        l3_asset_lang_allowlist: tuple[str, ...] = (),
    ) -> None:
        """엔진 실행에 필요한 의존성을 주입받는다."""
        self._file_repo = file_repo
        self._enrich_queue_repo = enrich_queue_repo
        self._body_repo = body_repo
        self._lsp_repo = lsp_repo
        self._readiness_repo = readiness_repo
        self._policy = policy
        self._lsp_backend = lsp_backend
        self._policy_repo = policy_repo
        self._event_repo = event_repo
        self._vector_index_sink = vector_index_sink
        self._tool_layer_repo = tool_layer_repo
        self._run_mode = "prod" if run_mode == "prod" else "dev"
        self._persist_body_for_read = persist_body_for_read
        self._l3_ready_queue = l3_ready_queue
        self._error_policy = error_policy
        self._record_enrich_latency = record_enrich_latency
        self._assert_parent_alive = assert_parent_alive
        self._flush_batch_size = flush_batch_size
        self._flush_interval_sec = flush_interval_sec
        self._flush_max_body_bytes = flush_max_body_bytes
        self._l3_parallel_enabled = bool(l3_parallel_enabled)
        self._l3_executor_max_workers = max(1, int(l3_executor_max_workers)) if int(l3_executor_max_workers) > 0 else 32
        self._l3_group_wait_timeout_sec = 90.0
        self._l3_recent_success_ttl_sec = max(0, int(l3_recent_success_ttl_sec))
        self._l3_backpressure_on_interactive = bool(l3_backpressure_on_interactive)
        self._l3_backpressure_cooldown_sec = max(0.01, float(max(10, int(l3_backpressure_cooldown_ms))) / 1000.0)
        self._l3_executor = ThreadPoolExecutor(max_workers=self._l3_executor_max_workers, thread_name_prefix="enrich-l3")
        self._l3_executor_closed = False
        self._indexing_mode = "steady"
        self._bootstrap_started_at = time.monotonic()
        self._l3_supported_languages = self._parse_l3_supported_languages(l3_supported_languages)
        self._lsp_probe_l1_languages = self._parse_lsp_probe_l1_languages(lsp_probe_l1_languages)
        self._perf_tracer = PerfTracer(component="enrich_engine")
        self._l5_admission_shadow_enabled = bool(l5_admission_shadow_enabled)
        self._l5_admission_enforced = bool(l5_admission_enforced)
        self._l5_total_decisions = 0
        self._l5_total_admitted = 0
        self._l5_batch_decisions = 0
        self._l5_batch_admitted = 0
        self._l5_calls_per_min_per_lang_max = max(1, int(l5_calls_per_min_per_lang_max))
        self._l5_call_rate_total_max = max(0.0, min(1.0, float(l5_call_rate_total_max)))
        self._l5_call_rate_batch_max = max(0.0, min(1.0, float(l5_call_rate_batch_max)))
        self._l5_tokens_per_10sec_global_max = max(1, int(l5_tokens_per_10sec_global_max))
        self._l5_tokens_per_10sec_per_lang_max = max(1, int(l5_tokens_per_10sec_per_lang_max))
        self._l5_tokens_per_10sec_per_workspace_max = max(1, int(l5_tokens_per_10sec_per_workspace_max))
        self._l3_asset_loader = L3AssetLoader()
        self._extract_error_code_fn = extract_error_code_from_lsp_error_message
        self._is_scope_escalation_trigger_fn = (
            lambda code, message: is_scope_escalation_trigger_error_for_l3(code=code, message=message)
        )
        self._next_scope_level_for_escalation_fn = next_scope_level_for_l3_escalation
        self._classify_failure_kind_fn = classify_l3_extract_failure_kind
        wire_engine_services(
            self,
            l3_query_compile_cache_enabled=l3_query_compile_cache_enabled,
            l3_query_compile_ms_budget=l3_query_compile_ms_budget,
            l3_query_budget_ms=l3_query_budget_ms,
            l3_asset_mode=l3_asset_mode,
            l3_asset_lang_allowlist=l3_asset_lang_allowlist,
        )
        self._initialize_runtime_processors()

    def _initialize_runtime_processors(self) -> None:
        """런타임 processor/coordinator wiring을 구성한다."""
        wire_runtime_processors(self)

    def _build_default_language_policy_map(self) -> dict[str, LanguageL5Policy]:
        """전 언어를 열어두고 예산으로 조이는 기본 L5 정책을 생성한다."""
        return build_default_language_policy_map(get_enabled_language_names())

    def shutdown(self) -> None:
        """L3 전역 executor를 종료한다."""
        if self._l3_executor_closed:
            return
        self._l3_executor.shutdown(wait=True)
        self._l3_executor_closed = True

    def reset_runtime_state(self) -> None:
        """백그라운드 시작 시 엔진 상태를 초기화한다."""
        self._bootstrap_started_at = time.monotonic()
        self._indexing_mode = "steady"
        self._l5_total_decisions = 0
        self._l5_total_admitted = 0
        self._l5_batch_decisions = 0
        self._l5_batch_admitted = 0
        for reason in self._l5_reject_counts_by_reason:
            self._l5_reject_counts_by_reason[reason] = 0
        self._l5_cost_units_by_reason.clear()
        self._l5_cost_units_by_language.clear()
        self._l5_cost_units_by_workspace.clear()
        self._l5_admitted_timestamps_by_lang.clear()
        self._l5_cooldown_until_by_scope_file.clear()

    def get_runtime_metrics(self) -> dict[str, float]:
        """L4/L5 admission 관련 런타임 메트릭을 반환한다."""
        reject_counts = self._get_or_init_l5_reject_counts()
        cost_by_reason = self._get_or_init_l5_cost_units_by_reason()
        cost_by_language = self._get_or_init_l5_cost_units_by_language()
        cost_by_workspace = self._get_or_init_l5_cost_units_by_workspace()
        total_rate = (
            0.0
            if self._l5_total_decisions <= 0
            else float(self._l5_total_admitted) / float(self._l5_total_decisions)
        )
        batch_rate = (
            0.0
            if self._l5_batch_decisions <= 0
            else float(self._l5_batch_admitted) / float(self._l5_batch_decisions)
        )
        metrics = {
            "l5_total_decisions": float(self._l5_total_decisions),
            "l5_total_admitted": float(self._l5_total_admitted),
            "l5_batch_decisions": float(self._l5_batch_decisions),
            "l5_batch_admitted": float(self._l5_batch_admitted),
            "l5_call_rate_total_pct": total_rate * 100.0,
            "l5_call_rate_batch_pct": batch_rate * 100.0,
        }
        for reason, count in reject_counts.items():
            metrics[f"l5_reject_count_by_reject_reason_{reason.value}"] = float(count)
        for reason_key, cost_units in cost_by_reason.items():
            metrics[f"l5_cost_units_total_by_reason_{reason_key}"] = float(cost_units)
        for language_key, cost_units in cost_by_language.items():
            metrics[f"l5_cost_units_total_by_language_{language_key}"] = float(cost_units)
        for workspace_key, cost_units in cost_by_workspace.items():
            metrics[f"l5_cost_units_total_by_workspace_{workspace_key}"] = float(cost_units)
        return metrics

    def get_l3_quality_shadow_summary(self) -> dict[str, object]:
        """L3 AST 품질 shadow 비교 요약을 반환한다 (Phase A, metrics-only)."""
        orchestrator = getattr(self, "_l3_orchestrator", None)
        if orchestrator is None:
            return {"enabled": False, "sampled_files": 0, "shadow_eval_errors": 0}
        getter = getattr(orchestrator, "get_quality_shadow_summary", None)
        if not callable(getter):
            return {"enabled": False, "sampled_files": 0, "shadow_eval_errors": 0}
        try:
            summary = getter()
        except (RuntimeError, OSError, ValueError, TypeError):
            return {"enabled": False, "sampled_files": 0, "shadow_eval_errors": 0}
        if not isinstance(summary, dict):
            return {"enabled": False, "sampled_files": 0, "shadow_eval_errors": 0}
        return dict(summary)

    def set_l5_admission_mode(self, *, shadow_enabled: bool, enforced: bool) -> None:
        """L5 admission 모드를 런타임에서 동적으로 갱신한다."""
        self._l5_admission_shadow_enabled = bool(shadow_enabled)
        self._l5_admission_enforced = bool(enforced)
        self._l3_orchestrator.set_l5_admission_mode(
            evaluate_l5_admission=(self._evaluate_l5_admission_for_job if self._l5_admission_shadow_enabled else None),
            enforced=self._l5_admission_enforced,
        )

    def indexing_mode(self) -> str:
        """현재 인덱싱 모드를 반환한다."""
        return self._indexing_mode

    def process_enrich_jobs(self, limit: int) -> int:
        """L2/L3 통합 보강 작업을 수행한다."""
        return self._enrich_jobs_processor.process_jobs(limit=limit)

    def process_enrich_jobs_l2(self, limit: int) -> int:
        """L2 전용 보강 처리."""
        return self._l2_job_processor.process_jobs(limit=limit)

    def process_enrich_jobs_l3(self, limit: int) -> int:
        """L3 전용 보강 처리."""
        batch_started_at = time.perf_counter()
        self._assert_parent_alive("enrich_worker_l3")
        acquire_started_at = time.perf_counter()
        jobs = self._acquire_l3_jobs(limit=limit)
        acquire_elapsed_ms = (time.perf_counter() - acquire_started_at) * 1000.0
        rebalance_started_at = time.perf_counter()
        jobs = self._rebalance_jobs_by_language(jobs=jobs)
        rebalance_elapsed_ms = (time.perf_counter() - rebalance_started_at) * 1000.0
        processed = 0
        l3_buffers = _L3ResultBuffersDTO.empty()
        body_upserts: list[CollectedFileBodyDTO] = []
        last_flush_at = time.perf_counter()
        flush_count = 0
        group_count = 0
        grouped_jobs = self._group_jobs_by_repo_and_language(jobs=jobs)
        grouped_jobs = self._order_l3_groups_for_scheduling(groups=grouped_jobs)
        for group in grouped_jobs:
            group_count += 1
            processed += self._process_l3_group(group=group, buffers=l3_buffers, body_upserts=body_upserts)
            should_flush_by_size = len(l3_buffers.done_ids) + len(l3_buffers.failed_updates) >= self._flush_batch_size
            should_flush_by_time = time.perf_counter() - last_flush_at >= self._flush_interval_sec
            if should_flush_by_size or should_flush_by_time:
                with self._perf_tracer.span("process_enrich_jobs_l3.flush_buffers", phase="l3_flush"):
                    self._l3_flush_coordinator.flush(buffers=l3_buffers, body_upserts=body_upserts)
                flush_count += 1
                last_flush_at = time.perf_counter()
        with self._perf_tracer.span("process_enrich_jobs_l3.flush_buffers_final", phase="l3_flush"):
            self._l3_flush_coordinator.flush(buffers=l3_buffers, body_upserts=body_upserts)
        flush_count += 1
        return processed

    def _process_l3_group(
        self,
        *,
        group: list[FileEnrichJobDTO],
        buffers: _L3ResultBuffersDTO,
        body_upserts: list[CollectedFileBodyDTO],
    ) -> int:
        """L3 그룹 하나를 처리하고 처리 건수를 반환한다."""
        return self._l3_group_processor.process_group(
            group=group,
            buffers=buffers,
            body_upserts=body_upserts,
        )

    def process_enrich_jobs_bootstrap(self, limit: int) -> int:
        """bootstrap 모드 정책에 따라 L2/L3 비율을 조정한다."""
        self.refresh_indexing_mode()
        if self._indexing_mode == "steady":
            return self.process_enrich_jobs(limit=limit)
        _, l3_worker_count, _, _ = self._resolve_bootstrap_policy()
        processed_l2 = self.process_enrich_jobs_l2(limit=limit)
        if self._indexing_mode == "bootstrap_l2_priority":
            l3_limit = max(1, min(limit // 4, l3_worker_count * 32))
            if self._l3_ready_queue.qsize() <= l3_limit:
                return processed_l2
            processed_l3 = self.process_enrich_jobs_l3(limit=l3_limit)
            return processed_l2 + processed_l3
        processed_l3 = self.process_enrich_jobs_l3(limit=max(1, min(limit, l3_worker_count * 64)))
        return processed_l2 + processed_l3

    def compute_coverage_bps(self) -> tuple[int, int]:
        """L2/L3 커버리지를 bps 단위로 계산한다."""
        state_counts = self._file_repo.get_enrich_state_counts()
        total = int(sum(state_counts.values()))
        if total <= 0:
            return (0, 0)
        l3_skipped = int(state_counts.get("L3_SKIPPED", 0))
        l2_ready = int(state_counts.get("BODY_READY", 0)) + int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0)) + l3_skipped
        l3_ready = int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0))
        l3_total = max(0, total - l3_skipped)
        l2_bps = int(l2_ready * 10000 / total)
        l3_bps = 10000 if l3_total <= 0 else int(l3_ready * 10000 / l3_total)
        return (l2_bps, l3_bps)

    def refresh_indexing_mode(self) -> None:
        """bootstrap 전환 정책을 갱신한다."""
        bootstrap_enabled, _, bootstrap_exit_l2_bps, bootstrap_exit_max_sec = self._resolve_bootstrap_policy()
        if not bootstrap_enabled:
            self._indexing_mode = "steady"
            return
        elapsed_sec = time.monotonic() - self._bootstrap_started_at
        l2_bps, l3_bps = self.compute_coverage_bps()
        reenter_l2_bps = max(1, bootstrap_exit_l2_bps - 700)
        if elapsed_sec >= float(bootstrap_exit_max_sec):
            self._indexing_mode = "steady"
            return
        if self._indexing_mode == "steady" and l2_bps < bootstrap_exit_l2_bps:
            self._indexing_mode = "bootstrap_l2_priority"
            return
        if self._indexing_mode == "steady":
            return
        if self._indexing_mode == "bootstrap_balanced" and l2_bps < reenter_l2_bps:
            self._indexing_mode = "bootstrap_l2_priority"
            return
        if self._indexing_mode == "bootstrap_l2_priority" and l2_bps >= bootstrap_exit_l2_bps:
            self._indexing_mode = "bootstrap_balanced"
            return
        if self._indexing_mode == "bootstrap_balanced" and l3_bps >= 9990:
            self._indexing_mode = "steady"

    def _resolve_bootstrap_policy(self) -> tuple[bool, int, int, int]:
        if self._policy_repo is None:
            return (False, 1, 9500, 1800)
        policy = self._policy_repo.get_policy()
        return (
            bool(policy.bootstrap_mode_enabled),
            max(1, int(policy.bootstrap_l3_worker_count)),
            max(1, min(10000, int(policy.bootstrap_exit_min_l2_coverage_bps))),
            max(60, int(policy.bootstrap_exit_max_sec)),
        )

    def _resolve_lsp_language(self, relative_path: str) -> Language | None:
        return resolve_language_from_path(file_path=relative_path)

    def _rebalance_jobs_by_language(self, jobs: list[FileEnrichJobDTO]) -> list[FileEnrichJobDTO]:
        return self._get_or_init_l3_scheduling_service().rebalance_jobs_by_language(jobs)

    def _group_jobs_by_repo_and_language(self, jobs: list[FileEnrichJobDTO]) -> list[list[FileEnrichJobDTO]]:
        return self._get_or_init_l3_scheduling_service().group_jobs_by_repo_and_language(jobs)

    def _order_l3_groups_for_scheduling(self, groups: list[list[FileEnrichJobDTO]]) -> list[list[FileEnrichJobDTO]]:
        """PR3 baseline: backend가 제공하는 lane-aware 정렬 힌트로 L3 그룹 순서를 조정한다."""
        return self._get_or_init_l3_scheduling_service().order_l3_groups_for_scheduling(groups)

    def _resolve_l3_parallelism(self, jobs: list[FileEnrichJobDTO]) -> int:
        return self._get_or_init_l3_scheduling_service().resolve_l3_parallelism(jobs)

    def _get_or_init_l3_scheduling_service(self) -> L3SchedulingService:
        service = getattr(self, "_l3_scheduling_service", None)
        if service is not None:
            return service
        service = L3SchedulingService(
            resolve_lsp_language=lambda relative_path: self._resolve_lsp_language(relative_path),
            lsp_backend=getattr(self, "_lsp_backend", object()),
            l3_parallel_enabled=bool(getattr(self, "_l3_parallel_enabled", True)),
            executor_max_workers=max(1, int(getattr(self, "_l3_executor_max_workers", 32))),
            backpressure_on_interactive=bool(getattr(self, "_l3_backpressure_on_interactive", True)),
            backpressure_cooldown_sec=float(getattr(self, "_l3_backpressure_cooldown_sec", 0.3)),
            monotonic_now=time.monotonic,
        )
        self._l3_scheduling_service = service
        return service

    def _set_group_bulk_mode(self, group: list[FileEnrichJobDTO], enabled: bool) -> None:
        """LSP 백엔드에 그룹 단위 bulk 모드를 전달한다."""
        if len(group) == 0:
            return
        language = self._resolve_lsp_language(group[0].relative_path)
        if language is None:
            return
        setter = getattr(self._lsp_backend, "set_bulk_mode", None)
        if callable(setter):
            try:
                setter(group[0].repo_root, language, enabled)
            except (RuntimeError, OSError, ValueError, TypeError):
                return

    def _process_single_l3_job(self, job: FileEnrichJobDTO) -> _L3JobResultDTO:
        orchestrator = getattr(self, "_l3_orchestrator", None)
        if orchestrator is None:
            raise RuntimeError("L3Orchestrator is not initialized")
        result = orchestrator.process_job(job)
        if not isinstance(result, _L3JobResultDTO):
            raise TypeError(
                f"L3Orchestrator returned unexpected result type: {type(result)!r}"
            )
        return result


    def _run_l3_preprocess(self, *, job: FileEnrichJobDTO, file_row: object) -> L3PreprocessResultDTO | None:
        preprocess_service = getattr(self, "_l3_preprocess_service", None)
        if preprocess_service is None:
            return None
        absolute_path = getattr(file_row, "absolute_path", None)
        try:
            if isinstance(absolute_path, str) and absolute_path.strip() != "":
                with open(absolute_path, "r", encoding="utf-8", errors="ignore") as handle:
                    content_text = handle.read()
            else:
                content_text = ""
            result = preprocess_service.preprocess(
                relative_path=job.relative_path,
                content_text=content_text,
                max_bytes=int(getattr(self, "_l3_preprocess_max_bytes", 262_144)),
            )
            if len(result.symbols) == 0 and result.decision is not L3PreprocessDecision.DEFERRED_HEAVY:
                fallback = getattr(self, "_l3_degraded_fallback_service", None)
                if fallback is not None:
                    return fallback.fallback(relative_path=job.relative_path, content_text=content_text)
            return result
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            log.warning(
                "EnrichEngine L3 preprocess failed, returning explicit degraded NEEDS_L5 result (repo=%s, path=%s)",
                job.repo_root,
                job.relative_path,
                exc_info=True,
            )
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=True,
                decision=L3PreprocessDecision.NEEDS_L5,
                source="none",
                reason=f"l3_preprocess_exception:{type(exc).__name__}",
            )

    def _schedule_l1_probe_after_l3_fallback(self, job: FileEnrichJobDTO) -> None:
        """L3 fail-open 시 백그라운드 L1 probe를 조건부로 예약한다."""
        self._get_or_init_l3_runtime_coordination_service().schedule_l1_probe_after_l3_fallback(job)

    def _try_escalate_scope_after_l3_extract_error(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        """L3 extract 실패가 scope 문제라면 same-row scope escalation을 시도한다."""
        return self._get_or_init_l3_error_handling_service().try_escalate_scope_after_l3_extract_error(
            job=job,
            error_message=error_message,
        )

    def _try_defer_after_broker_lease_denial(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        """broker lease 거부 오류는 실패가 아니라 queue defer로 되돌린다."""
        return self._get_or_init_l3_error_handling_service().try_defer_after_broker_lease_denial(
            job=job,
            error_message=error_message,
        )

    def _resolve_next_scope_root_for_escalation(self, *, job: FileEnrichJobDTO, next_scope_level: str) -> str:
        """PR-B baseline scope root fallback 계산 (실제 planner 연계는 PR1에서 강화)."""
        return self._get_or_init_l3_error_handling_service().resolve_next_scope_root_for_escalation(
            job=job,
            next_scope_level=next_scope_level,
        )

    def _get_or_init_l3_error_handling_service(self) -> L3ErrorHandlingService:
        service = getattr(self, "_l3_error_handling_service", None)
        if service is not None:
            return service
        service = L3ErrorHandlingService(
            queue_repo=getattr(self, "_enrich_queue_repo", object()),
            error_policy=getattr(self, "_error_policy", object()),
            now_iso_supplier=now_iso8601_utc,
        )
        self._l3_error_handling_service = service
        return service

    def _get_or_init_l3_skip_runtime_service(self) -> L3SkipRuntimeService:
        service = getattr(self, "_l3_skip_runtime_service", None)
        if service is not None:
            return service
        service = L3SkipRuntimeService(
            l3_supported_languages=getattr(self, "_l3_supported_languages", set()),
            l3_recent_success_ttl_sec=int(getattr(self, "_l3_recent_success_ttl_sec", 0)),
            readiness_repo=getattr(self, "_readiness_repo", object()),
            lsp_backend=getattr(self, "_lsp_backend", object()),
            resolve_language_from_path_fn=lambda relative_path: resolve_language_from_path(file_path=relative_path),
        )
        self._l3_skip_runtime_service = service
        return service

    def _get_or_init_l3_runtime_coordination_service(self) -> L3RuntimeCoordinationService:
        service = getattr(self, "_l3_runtime_coordination_service", None)
        if service is not None:
            return service
        service = L3RuntimeCoordinationService(
            lsp_backend=getattr(self, "_lsp_backend", object()),
            lsp_probe_l1_languages=getattr(self, "_lsp_probe_l1_languages", set()),
            resolve_language_from_path_fn=lambda relative_path: resolve_language_from_path(file_path=relative_path),
            l3_ready_queue=getattr(self, "_l3_ready_queue", queue.Queue()),
            enrich_queue_repo=getattr(self, "_enrich_queue_repo", object()),
            now_iso_supplier=now_iso8601_utc,
            policy_repo=getattr(self, "_policy_repo", None),
        )
        self._l3_runtime_coordination_service = service
        return service

    def _record_scope_learning_after_l3_success(self, *, job: FileEnrichJobDTO) -> None:
        """성공한 scope 시도를 backend 학습 캐시에 기록한다 (Phase1 baseline)."""
        self._get_or_init_l3_runtime_coordination_service().record_scope_learning_after_l3_success(job=job)

    def _should_perf_trace_tick(self) -> bool:
        """테스트용 래퍼: 트레이스 샘플링 틱을 반환한다."""
        return self._perf_tracer.should_sample()

    def _perf_trace(self, event: str, **fields: object) -> None:
        """테스트용 래퍼: 성능 트레이스 로그를 남긴다."""
        self._perf_tracer.emit(event, **fields)

    def _parse_lsp_probe_l1_languages(self, items: tuple[str, ...]) -> set[Language]:
        """lsp_probe_l1_languages 설정을 Language 집합으로 변환한다."""
        return _parse_lsp_probe_l1_languages_shared(items)

    def _parse_l3_supported_languages(self, items: tuple[str, ...]) -> set[Language]:
        """l3_supported_languages 설정을 Language 집합으로 변환한다."""
        return _parse_l3_supported_languages_shared(items)

    def _evaluate_l5_admission_for_job(self, job: FileEnrichJobDTO, language: str) -> L4AdmissionDecisionDTO | None:
        runtime_service = getattr(self, "_l5_admission_runtime_service", None)
        if runtime_service is None:
            runtime_service = L5AdmissionRuntimeService(
                l4_admission_service=self._l4_admission_service,
                lsp_backend=getattr(self, "_lsp_backend", object()),
                monotonic_now=time.monotonic,
            )
            self._l5_admission_runtime_service = runtime_service
        state = L5AdmissionRuntimeState(
            total_decisions=int(getattr(self, "_l5_total_decisions", 0)),
            total_admitted=int(getattr(self, "_l5_total_admitted", 0)),
            batch_decisions=int(getattr(self, "_l5_batch_decisions", 0)),
            batch_admitted=int(getattr(self, "_l5_batch_admitted", 0)),
            calls_per_min_per_lang_max=int(getattr(self, "_l5_calls_per_min_per_lang_max", 1)),
            admitted_timestamps_by_lang=getattr(self, "_l5_admitted_timestamps_by_lang", {}),
            cooldown_until_by_scope_file=getattr(self, "_l5_cooldown_until_by_scope_file", {}),
            reject_counts_by_reason=self._get_or_init_l5_reject_counts(),
            cost_units_by_reason=self._get_or_init_l5_cost_units_by_reason(),
            cost_units_by_language=self._get_or_init_l5_cost_units_by_language(),
            cost_units_by_workspace=self._get_or_init_l5_cost_units_by_workspace(),
        )
        decision = runtime_service.evaluate_batch_for_job(
            state=state,
            job=job,
            language=language,
        )
        self._l5_total_decisions = state.total_decisions
        self._l5_total_admitted = state.total_admitted
        self._l5_batch_decisions = state.batch_decisions
        self._l5_batch_admitted = state.batch_admitted
        return decision

    def _get_or_init_l5_reject_counts(self) -> dict[L5RejectReason, int]:
        existing = getattr(self, "_l5_reject_counts_by_reason", None)
        if isinstance(existing, dict) and len(existing) > 0:
            return existing
        initialized: dict[L5RejectReason, int] = {reason: 0 for reason in L5RejectReason}
        setattr(self, "_l5_reject_counts_by_reason", initialized)
        return initialized

    def _get_or_init_l5_cost_units_by_reason(self) -> dict[str, float]:
        existing = getattr(self, "_l5_cost_units_by_reason", None)
        if isinstance(existing, dict):
            return existing
        initialized: dict[str, float] = {}
        setattr(self, "_l5_cost_units_by_reason", initialized)
        return initialized

    def _get_or_init_l5_cost_units_by_language(self) -> dict[str, float]:
        existing = getattr(self, "_l5_cost_units_by_language", None)
        if isinstance(existing, dict):
            return existing
        initialized: dict[str, float] = {}
        setattr(self, "_l5_cost_units_by_language", initialized)
        return initialized

    def _get_or_init_l5_cost_units_by_workspace(self) -> dict[str, float]:
        existing = getattr(self, "_l5_cost_units_by_workspace", None)
        if isinstance(existing, dict):
            return existing
        initialized: dict[str, float] = {}
        setattr(self, "_l5_cost_units_by_workspace", initialized)
        return initialized

    def _get_l5_lang_bucket(self, language_key: str) -> TokenBucket:
        existing = self._l5_lang_buckets.get(language_key)
        if existing is not None:
            return existing
        capacity = float(self._l5_tokens_per_10sec_per_lang_max)
        created = TokenBucket(
            capacity=capacity,
            refill_per_sec=capacity / 10.0,
            tokens=capacity,
            last_ts=time.monotonic(),
        )
        self._l5_lang_buckets[language_key] = created
        return created

    def _get_l5_workspace_bucket(self, workspace_uid: str) -> TokenBucket:
        existing = self._l5_workspace_buckets.get(workspace_uid)
        if existing is not None:
            return existing
        capacity = float(self._l5_tokens_per_10sec_per_workspace_max)
        created = TokenBucket(
            capacity=capacity,
            refill_per_sec=capacity / 10.0,
            tokens=capacity,
            last_ts=time.monotonic(),
        )
        self._l5_workspace_buckets[workspace_uid] = created
        return created

    def _build_l3_layer_upsert(
        self,
        *,
        job: FileEnrichJobDTO,
        preprocess_result: L3PreprocessResultDTO | None,
        now_iso: str,
    ) -> dict[str, object]:
        return self._layer_upsert_builder.build_l3(
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            content_hash=job.content_hash,
            preprocess_result=preprocess_result,
            now_iso=now_iso,
        )

    def _build_l4_layer_upsert(
        self,
        *,
        job: FileEnrichJobDTO,
        preprocess_result: L3PreprocessResultDTO | None,
        admission_decision: L4AdmissionDecisionDTO | None,
        now_iso: str,
    ) -> dict[str, object]:
        return self._layer_upsert_builder.build_l4(
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            content_hash=job.content_hash,
            preprocess_result=preprocess_result,
            admission_decision=admission_decision,
            now_iso=now_iso,
        )

    def _build_l5_layer_upsert(
        self,
        *,
        job: FileEnrichJobDTO,
        reason_code: L5ReasonCode,
        symbols: list[dict[str, object]],
        relations: list[dict[str, object]],
        now_iso: str,
    ) -> dict[str, object]:
        return self._layer_upsert_builder.build_l5(
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            content_hash=job.content_hash,
            reason_code=reason_code,
            symbols=symbols,
            relations=relations,
            now_iso=now_iso,
        )

    def _resolve_l3_skip_reason(self, job: FileEnrichJobDTO) -> str | None:
        """job이 L3 추출을 건너뛰어야 하는 사유를 반환한다."""
        return self._get_or_init_l3_skip_runtime_service().resolve_skip_reason(job)

    def _build_l3_skipped_readiness(
        self,
        *,
        job: FileEnrichJobDTO,
        reason: str,
        now_iso: str,
    ) -> ToolReadinessStateDTO:
        """L3 스킵 상태의 readiness 레코드를 생성한다."""
        return self._get_or_init_l3_skip_runtime_service().build_l3_skipped_readiness(
            job=job,
            reason=reason,
            now_iso=now_iso,
        )

    def _is_recent_tool_ready(self, job: FileEnrichJobDTO) -> bool:
        """최근 성공 상태면 L3 재추출을 건너뛸지 판단한다."""
        return self._get_or_init_l3_skip_runtime_service().is_recent_tool_ready(job)

    def _acquire_l3_jobs(self, limit: int) -> list[FileEnrichJobDTO]:
        return self._get_or_init_l3_runtime_coordination_service().acquire_l3_jobs(limit)

    def _is_deletion_hold_enabled(self) -> bool:
        return self._get_or_init_l3_runtime_coordination_service().is_deletion_hold_enabled()
