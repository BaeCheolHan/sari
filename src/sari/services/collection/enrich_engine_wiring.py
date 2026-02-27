"""EnrichEngine 조립(Composition Root) 전담 모듈."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sari.core.models import L5RejectReason, L5ReasonCode, L5RequestMode, now_iso8601_utc
from sari.services.collection.enrich_flush_coordinator import EnrichFlushCoordinator as _EnrichFlushCoordinator
from sari.services.collection.enrich_jobs_processor import EnrichJobsProcessor as _EnrichJobsProcessor
from sari.services.collection.enrich_processor_deps import EnrichProcessorDeps
from sari.services.collection.enrich_result_dto import _L3JobResultDTO
from sari.services.collection.l2.l2_job_processor import L2JobProcessor as _L2JobProcessor
from sari.services.collection.l3.l3_broker_admission_service import L3BrokerAdmissionService
from sari.services.collection.l3.l3_degraded_fallback_service import L3DegradedFallbackService
from sari.services.collection.l3.l3_error_handling_service import L3ErrorHandlingService
from sari.services.collection.l3.l3_flush_coordinator import L3FlushCoordinator as _L3FlushCoordinator
from sari.services.collection.l3.l3_group_processor import L3GroupProcessor as _L3GroupProcessor
from sari.services.collection.l3.l3_orchestrator import L3Orchestrator
from sari.services.collection.l3.l3_persist_service import L3PersistService
from sari.services.collection.l3.l3_quality_evaluation_service import L3QualityEvaluationService
from sari.services.collection.l3.l3_queue_transition_service import L3QueueTransitionService
from sari.services.collection.l3.l3_result_merger import L3ResultMerger as _L3ResultMerger
from sari.services.collection.l3.l3_runtime_coordination_service import L3RuntimeCoordinationService
from sari.services.collection.l3.l3_scheduling_service import L3SchedulingService
from sari.services.collection.l3.l3_preprocess_io_service import L3PreprocessIoService
from sari.services.collection.l3.l3_scope_resolution_service import L3ScopeResolutionService
from sari.services.collection.l3.l3_skip_eligibility_service import L3SkipEligibilityService
from sari.services.collection.l3.l3_skip_runtime_service import L3SkipRuntimeService
from sari.services.collection.l3.l3_timeout_failure_builder import L3TimeoutFailureBuilder as _L3TimeoutFailureBuilder
from sari.services.collection.l3.l3_treesitter_preprocess_service import L3TreeSitterPreprocessService
from sari.services.collection.l4.l4_admission_service import L4AdmissionService
from sari.services.collection.l5.l5_admission_policy import L5AdmissionPolicy, L5AdmissionPolicyConfig, TokenBucket
from sari.services.collection.l5.l5_admission_runtime_service import L5AdmissionRuntimeService
from sari.services.collection.l5.l5_cached_extract_service import L5CachedExtractService
from sari.services.collection.l5.l5_queue_defer_service import L5QueueDeferService
from sari.services.collection.l5.l5_runtime_stats_service import L5RuntimeStatsService
from sari.services.collection.layer_upsert_builder import LayerUpsertBuilder

if TYPE_CHECKING:
    from sari.services.collection.enrich_engine import EnrichEngine


def build_enrich_processor_deps(engine: "EnrichEngine") -> EnrichProcessorDeps:
    """L2/Enrich processor 공통 의존성을 DTO로 묶어 반환한다."""
    return EnrichProcessorDeps(
        assert_parent_alive=engine._assert_parent_alive,
        rebalance_jobs_by_language=engine._rebalance_jobs_by_language,
        file_repo_get_file=engine._file_repo.get_file,
        retry_max_attempts=engine._policy.retry_max_attempts,
        retry_backoff_base_sec=engine._policy.retry_backoff_base_sec,
        persist_body_for_read=engine._persist_body_for_read,
        vector_index_sink=engine._vector_index_sink,
        is_deletion_hold_enabled=engine._is_deletion_hold_enabled,
        resolve_l3_skip_reason=lambda job: engine._resolve_l3_skip_reason(job=job),
        build_l3_skipped_readiness=lambda job, reason, now_iso: engine._build_l3_skipped_readiness(
            job=job,
            reason=reason,
            now_iso=now_iso,
        ),
        record_error_event=engine._error_policy.record_error_event,
        record_enrich_latency=engine._record_enrich_latency,
        run_mode=engine._run_mode,
        record_event=(
            None
            if engine._event_repo is None
            else lambda job_id, status, latency_ms, created_at: engine._event_repo.record_event(
                job_id=job_id,
                status=status,
                latency_ms=latency_ms,
                created_at=created_at,
            )
        ),
    )


def wire_engine_services(
    engine: "EnrichEngine",
    *,
    l3_query_compile_cache_enabled: bool,
    l3_query_compile_ms_budget: float,
    l3_query_budget_ms: float,
    l3_tree_sitter_executor_mode: str,
    l3_tree_sitter_subinterp_workers: int,
    l3_tree_sitter_subinterp_min_bytes: int,
    l3_asset_mode: str,
    l3_asset_lang_allowlist: tuple[str, ...],
) -> None:
    engine._l5_reject_counts_by_reason = {reason: 0 for reason in L5RejectReason}
    engine._l5_cost_units_by_reason = {}
    engine._l5_cost_units_by_language = {}
    engine._l5_cost_units_by_workspace = {}
    engine._l5_admitted_timestamps_by_lang = {}
    engine._l5_lang_buckets = {}
    engine._l5_workspace_buckets = {}
    engine._l5_cooldown_until_by_scope_file = {}
    engine._l5_admission_policy = L5AdmissionPolicy(
        config=L5AdmissionPolicyConfig(
            l5_call_rate_total_max=max(0.0, min(1.0, float(engine._l5_call_rate_total_max))),
            l5_call_rate_batch_max=max(0.0, min(1.0, float(engine._l5_call_rate_batch_max))),
            language_policy_map=engine._build_default_language_policy_map(),
        ),
        global_bucket=TokenBucket(
            capacity=float(max(1, int(engine._l5_tokens_per_10sec_global_max))),
            refill_per_sec=float(max(1, int(engine._l5_tokens_per_10sec_global_max))) / 10.0,
            tokens=float(max(1, int(engine._l5_tokens_per_10sec_global_max))),
            last_ts=time.monotonic(),
        ),
        lang_bucket_provider=engine._get_l5_lang_bucket,
        workspace_bucket_provider=engine._get_l5_workspace_bucket,
    )
    engine._l4_admission_service = L4AdmissionService(policy=engine._l5_admission_policy)
    engine._l5_admission_runtime_service = L5AdmissionRuntimeService(
        l4_admission_service=engine._l4_admission_service,
        lsp_backend=engine._lsp_backend,
        monotonic_now=time.monotonic,
    )
    engine._l5_runtime_stats_service = L5RuntimeStatsService()
    engine._l5_cached_extract_service = L5CachedExtractService(
        tool_layer_repo=getattr(engine, "_tool_layer_repo", None),
        lsp_repo=getattr(engine, "_lsp_repo", None),
        delegate_extract=engine._lsp_backend.extract,
        enabled=bool(getattr(engine, "_l5_db_short_circuit_enabled", True)),
        log_miss_reason=bool(getattr(engine, "_l5_db_short_circuit_log_miss_reason", True)),
    )
    configured_l3_asset_mode = str(l3_asset_mode).strip().lower()
    if configured_l3_asset_mode not in {"shadow", "gate", "apply"}:
        configured_l3_asset_mode = "shadow"
    engine._l3_asset_mode = configured_l3_asset_mode
    engine._l3_asset_lang_allowlist = tuple(item.strip().lower() for item in l3_asset_lang_allowlist if item.strip() != "")
    engine._l3_preprocess_service = L3TreeSitterPreprocessService(
        query_compile_cache_enabled=l3_query_compile_cache_enabled,
        query_compile_ms_budget=l3_query_compile_ms_budget,
        query_budget_ms=l3_query_budget_ms,
        tree_sitter_executor_mode=l3_tree_sitter_executor_mode,
        tree_sitter_subinterp_workers=l3_tree_sitter_subinterp_workers,
        tree_sitter_subinterp_min_bytes=l3_tree_sitter_subinterp_min_bytes,
        asset_loader=engine._l3_asset_loader,
        asset_mode=engine._l3_asset_mode,
        asset_lang_allowlist=engine._l3_asset_lang_allowlist,
    )
    engine._l3_degraded_fallback_service = L3DegradedFallbackService()
    engine._l3_preprocess_io_service = L3PreprocessIoService(
        preprocess_service=engine._l3_preprocess_service,
        fallback_service=engine._l3_degraded_fallback_service,
    )
    engine._l3_preprocess_max_bytes = 262_144
    engine._l3_scope_resolution_service = L3ScopeResolutionService()
    engine._l3_broker_admission_service = L3BrokerAdmissionService()
    engine._l3_runtime_coordination_service = L3RuntimeCoordinationService(
        lsp_backend=engine._lsp_backend,
        lsp_probe_l1_languages=engine._lsp_probe_l1_languages,
        resolve_language_from_path_fn=lambda relative_path: engine._resolve_lsp_language(relative_path),
        l3_ready_queue=engine._l3_ready_queue,
        enrich_queue_repo=engine._enrich_queue_repo,
        now_iso_supplier=now_iso8601_utc,
        policy_repo=engine._policy_repo,
    )
    engine._l3_skip_runtime_service = L3SkipRuntimeService(
        l3_supported_languages=engine._l3_supported_languages,
        l3_recent_success_ttl_sec=engine._l3_recent_success_ttl_sec,
        readiness_repo=engine._readiness_repo,
        lsp_backend=engine._lsp_backend,
        resolve_language_from_path_fn=lambda relative_path: engine._resolve_lsp_language(relative_path),
    )
    engine._l3_skip_eligibility_service = L3SkipEligibilityService(
        is_recent_tool_ready=engine._is_recent_tool_ready,
        resolve_l3_skip_reason=engine._resolve_l3_skip_reason,
        build_l3_skipped_readiness=lambda job, reason, now_iso: engine._build_l3_skipped_readiness(
            job=job,
            reason=reason,
            now_iso=now_iso,
        ),
    )
    engine._l3_queue_transition_service = L3QueueTransitionService(
        queue_repo=engine._enrich_queue_repo,
        error_policy=engine._error_policy,
        now_iso_supplier=now_iso8601_utc,
        broker_admission=engine._l3_broker_admission_service,
        extract_error_code=engine._extract_error_code_fn,
        is_scope_escalation_trigger=engine._is_scope_escalation_trigger_fn,
        next_scope_level_for_escalation=engine._next_scope_level_for_escalation_fn,
    )
    engine._l5_queue_defer_service = L5QueueDeferService(
        queue_repo=engine._enrich_queue_repo,
        error_policy=engine._error_policy,
        now_iso_supplier=now_iso8601_utc,
    )
    engine._l3_scheduling_service = L3SchedulingService(
        resolve_lsp_language=lambda relative_path: engine._resolve_lsp_language(relative_path),
        lsp_backend=engine._lsp_backend,
        l3_parallel_enabled=engine._l3_parallel_enabled,
        executor_max_workers=engine._l3_executor_max_workers,
        backpressure_on_interactive=engine._l3_backpressure_on_interactive,
        backpressure_cooldown_sec=engine._l3_backpressure_cooldown_sec,
        monotonic_now=time.monotonic,
    )
    engine._l3_error_handling_service = L3ErrorHandlingService(
        queue_repo=engine._enrich_queue_repo,
        error_policy=engine._error_policy,
        now_iso_supplier=now_iso8601_utc,
    )
    engine._layer_upsert_builder = LayerUpsertBuilder()
    engine._l3_persist_service = L3PersistService(
        record_scope_learning=lambda job: engine._record_scope_learning_after_l3_success(job=job),
    )
    engine._l3_quality_eval_service = L3QualityEvaluationService(asset_loader=engine._l3_asset_loader)
    engine._l3_orchestrator = L3Orchestrator(
        file_repo=engine._file_repo,
        lsp_backend=engine._lsp_backend,
        policy=engine._policy,
        error_policy=engine._error_policy,
        run_mode=engine._run_mode,
        event_repo=engine._event_repo,
        deletion_hold_enabled=engine._is_deletion_hold_enabled,
        now_iso_supplier=now_iso8601_utc,
        record_enrich_latency=engine._record_enrich_latency,
        result_builder=lambda **kwargs: _L3JobResultDTO(**kwargs),
        classify_failure_kind=engine._classify_failure_kind_fn,
        schedule_l1_probe_after_l3_fallback=lambda job: engine._schedule_l1_probe_after_l3_fallback(job=job),
        extract_fn=engine._l5_cached_extract_service.extract,
        scope_resolution=engine._l3_scope_resolution_service,
        queue_transition=engine._l3_queue_transition_service,
        l5_queue_transition=engine._l5_queue_defer_service,
        skip_eligibility=engine._l3_skip_eligibility_service,
        persist_service=engine._l3_persist_service,
        preprocess_service=engine._l3_preprocess_service,
        degraded_fallback_service=engine._l3_degraded_fallback_service,
        preprocess_max_bytes=engine._l3_preprocess_max_bytes,
        evaluate_l5_admission=engine._evaluate_l5_admission_for_job if engine._l5_admission_shadow_enabled else None,
        l5_admission_enforced=engine._l5_admission_enforced,
        quality_eval_service=engine._l3_quality_eval_service,
        quality_shadow_enabled=False,
        quality_shadow_sample_rate=0.0,
        quality_shadow_max_files=0,
        quality_shadow_lang_allowlist=(),
    )


def wire_runtime_processors(engine: "EnrichEngine") -> None:
    deps = build_enrich_processor_deps(engine)
    engine._enrich_flush_coordinator = _EnrichFlushCoordinator(
        body_repo=engine._body_repo,
        lsp_repo=engine._lsp_repo,
        readiness_repo=engine._readiness_repo,
        file_repo=engine._file_repo,
        enrich_queue_repo=engine._enrich_queue_repo,
        tool_layer_repo=engine._tool_layer_repo,
    )
    engine._l3_flush_coordinator = _L3FlushCoordinator(flush_enrich_buffers=engine._enrich_flush_coordinator.flush)
    engine._l3_result_merger = _L3ResultMerger()
    engine._l3_timeout_failure_builder = _L3TimeoutFailureBuilder(
        retry_max_attempts=engine._policy.retry_max_attempts,
        retry_backoff_base_sec=engine._policy.retry_backoff_base_sec,
        record_error_event=engine._error_policy.record_error_event,
    )
    engine._l3_group_processor = _L3GroupProcessor(
        lsp_backend=engine._lsp_backend,
        l3_executor=engine._l3_executor,
        perf_tracer=engine._perf_tracer,
        resolve_lsp_language=engine._resolve_lsp_language,
        set_group_bulk_mode=engine._set_group_bulk_mode,
        resolve_l3_parallelism=engine._resolve_l3_parallelism,
        process_single_l3_job=engine._process_single_l3_job,
        merge_l3_result=lambda result, buffers: engine._l3_result_merger.merge(result=result, buffers=buffers),
        flush_l3_buffers=lambda buffers, body_upserts: engine._l3_flush_coordinator.flush(
            buffers=buffers,
            body_upserts=body_upserts,
        ),
        group_wait_timeout_sec=engine._l3_group_wait_timeout_sec,
        now_iso_supplier=now_iso8601_utc,
        build_timeout_failure_result=lambda **kwargs: engine._l3_timeout_failure_builder.build(**kwargs),
    )
    engine._l2_job_processor = _L2JobProcessor(
        assert_parent_alive=deps.assert_parent_alive,
        acquire_pending_for_l2=lambda limit, now_iso: engine._enrich_queue_repo.acquire_pending_for_l2(
            limit=limit,
            now_iso=now_iso,
        ),
        rebalance_jobs_by_language=deps.rebalance_jobs_by_language,
        flush_batch_size=engine._flush_batch_size,
        flush_interval_sec=engine._flush_interval_sec,
        flush_max_body_bytes=engine._flush_max_body_bytes,
        flush_enrich_buffers=engine._enrich_flush_coordinator.flush,
        run_mode=deps.run_mode,
        file_repo_get_file=deps.file_repo_get_file,
        retry_max_attempts=deps.retry_max_attempts,
        retry_backoff_base_sec=deps.retry_backoff_base_sec,
        persist_body_for_read=deps.persist_body_for_read,
        vector_index_sink=deps.vector_index_sink,
        is_deletion_hold_enabled=deps.is_deletion_hold_enabled,
        resolve_l3_skip_reason=deps.resolve_l3_skip_reason,
        build_l3_skipped_readiness=deps.build_l3_skipped_readiness,
        l3_ready_queue_put=engine._l3_ready_queue.put,
        record_error_event=deps.record_error_event,
        record_enrich_latency=deps.record_enrich_latency,
        record_event=deps.record_event,
    )
    engine._enrich_jobs_processor = _EnrichJobsProcessor(
        assert_parent_alive=deps.assert_parent_alive,
        acquire_pending=lambda limit, now_iso: engine._enrich_queue_repo.acquire_pending(limit=limit, now_iso=now_iso),
        rebalance_jobs_by_language=deps.rebalance_jobs_by_language,
        file_repo_get_file=deps.file_repo_get_file,
        retry_max_attempts=deps.retry_max_attempts,
        retry_backoff_base_sec=deps.retry_backoff_base_sec,
        persist_body_for_read=deps.persist_body_for_read,
        vector_index_sink=deps.vector_index_sink,
        is_deletion_hold_enabled=deps.is_deletion_hold_enabled,
        resolve_l3_skip_reason=deps.resolve_l3_skip_reason,
        build_l3_skipped_readiness=deps.build_l3_skipped_readiness,
        lsp_extract=engine._l5_cached_extract_service.extract,
        schedule_l1_probe_after_l3_fallback=lambda job: engine._schedule_l1_probe_after_l3_fallback(job=job),
        record_error_event=deps.record_error_event,
        run_mode=deps.run_mode,
        flush_batch_size=engine._flush_batch_size,
        flush_interval_sec=engine._flush_interval_sec,
        flush_max_body_bytes=engine._flush_max_body_bytes,
        flush_enrich=engine._enrich_flush_coordinator.flush,
        record_enrich_latency=deps.record_enrich_latency,
        record_event=deps.record_event,
    )
