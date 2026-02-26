"""L3 단일 job 오케스트레이터."""

from __future__ import annotations

import logging
import time
from typing import Callable

from sari.core.exceptions import CollectionError
from sari.core.models import (
    EnrichStateUpdateDTO,
    FileBodyDeleteTargetDTO,
    FileEnrichFailureUpdateDTO,
    FileEnrichJobDTO,
    LspExtractPersistDTO,
    ToolReadinessStateDTO,
    L4AdmissionDecisionDTO,
    L5ReasonCode,
)
from sari.services.collection.perf_trace import PerfTracer

from .l3_job_context import L3JobContext
from .l3_queue_transition_service import L3QueueTransitionService
from .l3_scope_resolution_service import L3ScopeResolutionService
from .l3_skip_eligibility_service import L3SkipEligibilityService
from .l3_persist_service import L3PersistService
from .l3_degraded_fallback_service import L3DegradedFallbackService
from .l3_treesitter_preprocess_service import (
    L3TreeSitterPreprocessService,
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)
from .l3_quality_evaluation_service import L3QualityEvaluationService
from .l3_quality_shadow_tracker import L3QualityShadowTracker
from .layer_upsert_builder import LayerUpsertBuilder
from .l3_stages.admission_stage import L3AdmissionStage
from .l3_stages.file_guard_stage import L3FileGuardStage
from .l3_stages.extract_stage import L3ExtractStage
from .l3_stages.extract_failure_stage import L3ExtractFailureStage
from .l3_stages.exception_stage import L3ExceptionStage
from .l3_stages.finalize_stage import L3FinalizeStage
from .l3_stages.persist_stage import L3PersistStage
from .l3_stages.preprocess_stage import L3PreprocessStage

log = logging.getLogger(__name__)


class L3Orchestrator:
    """L3 단일 job 처리 흐름을 조정한다."""

    def __init__(
        self,
        *,
        file_repo: object,
        lsp_backend: object,
        policy: object,
        error_policy: object,
        run_mode: str,
        event_repo: object | None,
        deletion_hold_enabled: Callable[[], bool],
        now_iso_supplier: Callable[[], str],
        record_enrich_latency: Callable[[float], None],
        result_builder: Callable[..., object],
        classify_failure_kind: Callable[[str], str],
        schedule_l1_probe_after_l3_fallback: Callable[[FileEnrichJobDTO], None],
        scope_resolution: L3ScopeResolutionService,
        queue_transition: L3QueueTransitionService,
        skip_eligibility: L3SkipEligibilityService,
        persist_service: L3PersistService,
        preprocess_service: L3TreeSitterPreprocessService | None = None,
        degraded_fallback_service: L3DegradedFallbackService | None = None,
        preprocess_max_bytes: int = 262_144,
        evaluate_l5_admission: Callable[[FileEnrichJobDTO, str], L4AdmissionDecisionDTO | None] | None = None,
        l5_admission_enforced: bool = False,
        quality_eval_service: L3QualityEvaluationService | None = None,
        quality_shadow_enabled: bool = False,
        quality_shadow_sample_rate: float = 0.0,
        quality_shadow_max_files: int = 0,
        quality_shadow_lang_allowlist: tuple[str, ...] = (),
    ) -> None:
        self._file_repo = file_repo
        self._lsp_backend = lsp_backend
        self._policy = policy
        self._error_policy = error_policy
        self._run_mode = run_mode
        self._event_repo = event_repo
        self._deletion_hold_enabled = deletion_hold_enabled
        self._now_iso_supplier = now_iso_supplier
        self._record_enrich_latency = record_enrich_latency
        self._result_builder = result_builder
        self._classify_failure_kind = classify_failure_kind
        self._schedule_l1_probe_after_l3_fallback = schedule_l1_probe_after_l3_fallback
        self._scope_resolution = scope_resolution
        self._queue_transition = queue_transition
        self._skip_eligibility = skip_eligibility
        self._persist_service = persist_service
        self._preprocess_service = preprocess_service
        self._degraded_fallback_service = degraded_fallback_service
        self._preprocess_max_bytes = max(1, int(preprocess_max_bytes))
        self._evaluate_l5_admission = evaluate_l5_admission
        self._l5_admission_enforced = bool(l5_admission_enforced)
        self._perf_tracer = PerfTracer(component="l3_orchestrator")
        self._quality_eval_service = quality_eval_service
        self._quality_shadow_enabled = bool(quality_shadow_enabled) and quality_eval_service is not None
        self._quality_shadow_sample_rate = max(0.0, min(1.0, float(quality_shadow_sample_rate)))
        self._quality_shadow_max_files = max(0, int(quality_shadow_max_files))
        self._quality_shadow_lang_allowlist = {
            str(item).strip().lower() for item in quality_shadow_lang_allowlist if str(item).strip() != ""
        }
        self._quality_shadow_sampled_count = 0
        self._quality_shadow_eval_errors = 0
        self._quality_shadow_accumulators: dict[str, dict[str, float]] = {}
        self._quality_shadow_flag_counts: dict[str, int] = {}
        self._quality_shadow_missing_pattern_counts: dict[str, dict[str, int]] = {}
        self._quality_shadow_tracker = L3QualityShadowTracker(self)
        self._layer_upsert_builder = LayerUpsertBuilder()
        self._preprocess_stage = L3PreprocessStage(run_preprocess=self._run_preprocess)
        self._file_guard_stage = L3FileGuardStage(get_file=self._file_repo.get_file)
        self._admission_stage = L3AdmissionStage(
            evaluate_l5_admission=self._evaluate_l5_admission,
            enforced=self._l5_admission_enforced,
        )
        self._extract_stage = L3ExtractStage(extract_fn=self._lsp_backend.extract)
        self._finalize_stage = L3FinalizeStage(
            result_builder=self._result_builder,
            event_repo=self._event_repo,
        )
        self._persist_stage = L3PersistStage(
            build_l3_layer_upsert=self._build_l3_layer_upsert,
            build_l4_layer_upsert=self._build_l4_layer_upsert,
            build_l5_layer_upsert=self._build_l5_layer_upsert,
            deletion_hold_enabled=self._deletion_hold_enabled,
        )
        self._extract_failure_stage = L3ExtractFailureStage(
            queue_transition=self._queue_transition,
            persist_stage=self._persist_stage,
            now_iso_supplier=self._now_iso_supplier,
            record_error_event=getattr(self._error_policy, "record_error_event", None),
            retry_max_attempts=int(self._policy.retry_max_attempts),
            retry_backoff_base_sec=int(self._policy.retry_backoff_base_sec),
        )
        self._exception_stage = L3ExceptionStage(
            persist_stage=self._persist_stage,
            now_iso_supplier=self._now_iso_supplier,
            record_error_event=getattr(self._error_policy, "record_error_event", None),
            retry_max_attempts=int(self._policy.retry_max_attempts),
            retry_backoff_base_sec=int(self._policy.retry_backoff_base_sec),
            run_mode=self._run_mode,
        )

    def process_job(self, job: FileEnrichJobDTO) -> object:
        started_at = time.perf_counter()
        finished_status = "FAILED"
        context = L3JobContext()
        dev_error = None
        language = ""
        with self._perf_tracer.span("l3.process_job", phase="total"):
            try:
                with self._perf_tracer.span("l3.file_lookup", phase="file_lookup"):
                    guard = self._file_guard_stage.execute(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        content_hash=job.content_hash,
                    )
                file_row = guard.file_row
                if guard.done_immediately:
                    context.done_id = job.job_id
                    finished_status = "DONE"
                else:
                    preprocess_result = self._preprocess_stage.execute(job=job, file_row=file_row)
                    with self._perf_tracer.span("l3.skip_recent_check", phase="skip_check"):
                        recent_ready = self._skip_eligibility.is_recent_tool_ready(job)
                    if recent_ready:
                        now_iso = self._now_iso_supplier()
                        self._persist_stage.mark_recent_ready(
                            context=context,
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            content_hash=job.content_hash,
                            now_iso=now_iso,
                            reason="skip_recent_success",
                        )
                        context.done_id = job.job_id
                        finished_status = "DONE"
                    else:
                        now_iso = self._now_iso_supplier()
                        with self._perf_tracer.span("l3.skip_reason_check", phase="skip_check"):
                            skip_reason = self._skip_eligibility.resolve_skip_reason(job)
                        if skip_reason is not None:
                            context.state_update = EnrichStateUpdateDTO(
                                repo_root=job.repo_root,
                                relative_path=job.relative_path,
                                enrich_state="L3_SKIPPED",
                                updated_at=now_iso,
                            )
                            context.readiness_update = self._skip_eligibility.build_skipped_readiness(job=job, reason=skip_reason, now_iso=now_iso)
                            context.done_id = job.job_id
                            finished_status = "DONE"
                        else:
                            language = self._scope_resolution.resolve_language(job.relative_path)
                            admission_decision = self._admission_stage.evaluate(job=job, language=language)
                            if admission_decision is not None and not admission_decision.admit_l5 and self._admission_stage.enforced:
                                deferred = self._queue_transition.defer_after_l5_admission_rejection(
                                    job=job,
                                    admission=admission_decision,
                                )
                                if deferred:
                                    finished_status = "PENDING"
                                else:
                                    rejection = (
                                        admission_decision.reject_reason.value
                                        if admission_decision.reject_reason is not None
                                        else "unknown"
                                    )
                                    context.state_update = EnrichStateUpdateDTO(
                                        repo_root=job.repo_root,
                                        relative_path=job.relative_path,
                                        enrich_state="L3_SKIPPED",
                                        updated_at=now_iso,
                                    )
                                    context.readiness_update = self._skip_eligibility.build_skipped_readiness(
                                        job=job,
                                        reason=f"l5_reject:{rejection}",
                                        now_iso=now_iso,
                                    )
                                    context.done_id = job.job_id
                                    finished_status = "DONE"
                            else:
                                if (
                                    preprocess_result is not None
                                    and preprocess_result.decision is L3PreprocessDecision.DEFERRED_HEAVY
                                ):
                                    deferred = self._queue_transition.defer_after_preprocess_heavy(
                                        job=job,
                                        reason=preprocess_result.reason,
                                    )
                                    if deferred:
                                        finished_status = "PENDING"
                                    else:
                                        context.state_update = EnrichStateUpdateDTO(
                                            repo_root=job.repo_root,
                                            relative_path=job.relative_path,
                                            enrich_state="L3_SKIPPED",
                                            updated_at=now_iso,
                                        )
                                        context.readiness_update = self._skip_eligibility.build_skipped_readiness(
                                            job=job,
                                            reason=preprocess_result.reason,
                                            now_iso=now_iso,
                                        )
                                        context.done_id = job.job_id
                                        finished_status = "DONE"
                                elif (
                                    preprocess_result is not None
                                    and preprocess_result.decision is L3PreprocessDecision.L3_ONLY
                                    and len(preprocess_result.symbols) > 0
                                ):
                                    self._persist_stage.apply_l3_only_success(
                                        context=context,
                                        repo_root=job.repo_root,
                                        relative_path=job.relative_path,
                                        content_hash=job.content_hash,
                                        preprocess_result=preprocess_result,
                                        admission_decision=admission_decision,
                                        now_iso=now_iso,
                                    )
                                    context.done_id = job.job_id
                                    finished_status = "DONE"
                                else:
                                    if admission_decision is not None and not admission_decision.admit_l5:
                                        rejection = (
                                            admission_decision.reject_reason.value
                                            if admission_decision.reject_reason is not None
                                            else "unknown"
                                        )
                                        context.state_update = EnrichStateUpdateDTO(
                                            repo_root=job.repo_root,
                                            relative_path=job.relative_path,
                                            enrich_state="L3_SKIPPED",
                                            updated_at=now_iso,
                                        )
                                        context.readiness_update = self._skip_eligibility.build_skipped_readiness(
                                            job=job,
                                            reason=f"l5_reject:{rejection}",
                                            now_iso=now_iso,
                                        )
                                    else:
                                        extraction = self._extract_stage.execute(
                                            repo_root=job.repo_root,
                                            relative_path=job.relative_path,
                                            content_hash=job.content_hash,
                                        )
                                        if extraction.error_message is not None:
                                            finished_status = self._extract_failure_stage.handle_extract_error(
                                                context=context,
                                                job=job,
                                                error_message=extraction.error_message,
                                            )
                                        else:
                                            self._record_quality_shadow_compare(
                                                job=job,
                                                language=language,
                                                preprocess_result=preprocess_result,
                                                lsp_symbols=list(extraction.symbols),
                                            )
                                            context.l3_layer_upsert = self._build_l3_layer_upsert(
                                                repo_root=job.repo_root,
                                                relative_path=job.relative_path,
                                                content_hash=job.content_hash,
                                                preprocess_result=preprocess_result,
                                                now_iso=now_iso,
                                            )
                                            context.l4_layer_upsert = self._build_l4_layer_upsert(
                                                repo_root=job.repo_root,
                                                relative_path=job.relative_path,
                                                content_hash=job.content_hash,
                                                preprocess_result=preprocess_result,
                                                admission_decision=admission_decision,
                                                now_iso=now_iso,
                                            )
                                            reason_code = (
                                                admission_decision.reason_code
                                                if admission_decision is not None and admission_decision.reason_code is not None
                                                else L5ReasonCode.GOLDENSET_COVERAGE
                                            )
                                            self._persist_stage.apply_l5_success(
                                                context=context,
                                                repo_root=job.repo_root,
                                                relative_path=job.relative_path,
                                                content_hash=job.content_hash,
                                                preprocess_result=preprocess_result,
                                                admission_decision=admission_decision,
                                                reason_code=reason_code,
                                                lsp_symbols=extraction.symbols,
                                                lsp_relations=extraction.relations,
                                                now_iso=now_iso,
                                            )
                                            context.done_id = job.job_id
                                            finished_status = "DONE"
                                    if finished_status not in {"PENDING", "DONE"} and context.failure_update is None:
                                        context.done_id = job.job_id
                                        finished_status = "DONE"
            except (CollectionError, RuntimeError, OSError, ValueError) as exc:
                dev_error = self._exception_stage.handle_exception(
                    context=context,
                    job=job,
                    exc=exc,
                )
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        self._record_enrich_latency(elapsed_ms)
        return self._finalize_stage.execute(
            job_id=job.job_id,
            finished_status=finished_status,
            elapsed_ms=elapsed_ms,
            context=context,
            dev_error=dev_error,
        )

    def set_l5_admission_mode(
        self,
        *,
        evaluate_l5_admission: Callable[[FileEnrichJobDTO, str], L4AdmissionDecisionDTO | None] | None,
        enforced: bool,
    ) -> None:
        """L5 admission runtime 토글을 갱신한다."""
        self._evaluate_l5_admission = evaluate_l5_admission
        self._l5_admission_enforced = bool(enforced)
        self._admission_stage.set_mode(
            evaluate_l5_admission=evaluate_l5_admission,
            enforced=enforced,
        )

    def get_quality_shadow_summary(self) -> dict[str, object]:
        """AST vs LSP shadow 비교 요약을 반환한다 (behavior 영향 없음)."""
        tracker = getattr(self, "_quality_shadow_tracker", None)
        if tracker is None:
            tracker = L3QualityShadowTracker(self)
            self._quality_shadow_tracker = tracker
        return tracker.get_summary()

    def _record_quality_shadow_compare(
        self,
        *,
        job: FileEnrichJobDTO,
        language: str,
        preprocess_result: L3PreprocessResultDTO | None,
        lsp_symbols: list[dict[str, object]],
    ) -> None:
        tracker = getattr(self, "_quality_shadow_tracker", None)
        if tracker is None:
            tracker = L3QualityShadowTracker(self)
            self._quality_shadow_tracker = tracker
        tracker.record_compare(
            job=job,
            language=language,
            preprocess_result=preprocess_result,
            lsp_symbols=lsp_symbols,
        )

    def _run_preprocess(self, *, job: FileEnrichJobDTO, file_row: object) -> L3PreprocessResultDTO | None:
        preprocess_service = self._preprocess_service
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
                max_bytes=self._preprocess_max_bytes,
            )
            if (
                len(result.symbols) == 0
                and result.decision is not L3PreprocessDecision.DEFERRED_HEAVY
                and self._degraded_fallback_service is not None
            ):
                return self._degraded_fallback_service.fallback(
                    relative_path=job.relative_path,
                    content_text=content_text,
                )
            return result
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            log.warning(
                "L3 preprocess failed, returning explicit degraded NEEDS_L5 result (repo=%s, path=%s)",
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

    def _build_l3_layer_upsert(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        preprocess_result: L3PreprocessResultDTO | None,
        now_iso: str,
    ) -> dict[str, object]:
        return self._layer_upsert_builder.build_l3(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            preprocess_result=preprocess_result,
            now_iso=now_iso,
        )

    def _build_l4_layer_upsert(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        preprocess_result: L3PreprocessResultDTO | None,
        admission_decision: L4AdmissionDecisionDTO | None,
        now_iso: str,
    ) -> dict[str, object]:
        return self._layer_upsert_builder.build_l4(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            preprocess_result=preprocess_result,
            admission_decision=admission_decision,
            now_iso=now_iso,
        )

    def _build_l5_layer_upsert(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        reason_code: L5ReasonCode,
        symbols: list[dict[str, object]],
        relations: list[dict[str, object]],
        now_iso: str,
    ) -> dict[str, object]:
        return self._layer_upsert_builder.build_l5(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            reason_code=reason_code,
            symbols=symbols,
            relations=relations,
            now_iso=now_iso,
        )
