"""L3 단일 job 오케스트레이터."""

from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger(__name__)

from sari.core.exceptions import CollectionError
from sari.core.models import (
    FileEnrichJobDTO,
    L4AdmissionDecisionDTO,
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
    L3PreprocessResultDTO,
)
from .l3_quality_evaluation_service import L3QualityEvaluationService
from .l3_quality_shadow_tracker import L3QualityShadowTracker
from sari.services.collection.layer_upsert_builder import LayerUpsertBuilder
from .stages.admission_stage import L3AdmissionStage
from .stages.decision_stage import L3DecisionStage
from .stages.file_guard_stage import L3FileGuardStage
from .stages.extract_stage import L3ExtractStage
from .stages.extract_failure_stage import L3ExtractFailureStage
from .stages.extract_success_stage import L3ExtractSuccessStage
from .stages.exception_stage import L3ExceptionStage
from .stages.finalize_stage import L3FinalizeStage
from .stages.persist_stage import L3PersistStage
from .stages.preprocess_stage import L3PreprocessStage
from .stages.preprocess_io_stage import L3PreprocessIoStage


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
        l5_queue_transition: object,
        skip_eligibility: L3SkipEligibilityService,
        persist_service: L3PersistService,
        extract_fn: Callable[[str, str, str], object] | None = None,
        raw_extract_fn: Callable[[str, str, str], object] | None = None,
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
        self._preprocess_io_stage = L3PreprocessIoStage(
            preprocess_service=self._preprocess_service,
            degraded_fallback_service=self._degraded_fallback_service,
            preprocess_max_bytes=self._preprocess_max_bytes,
        )
        self._preprocess_stage = L3PreprocessStage(run_preprocess=self._run_preprocess)
        self._file_guard_stage = L3FileGuardStage(get_file=self._file_repo.get_file)
        self._admission_stage = L3AdmissionStage(
            evaluate_l5_admission=self._evaluate_l5_admission,
            enforced=self._l5_admission_enforced,
        )
        resolved_extract_fn = extract_fn if extract_fn is not None else self._lsp_backend.extract
        self._raw_extract_fn = raw_extract_fn if raw_extract_fn is not None else resolved_extract_fn
        self._extract_stage = L3ExtractStage(extract_fn=resolved_extract_fn)
        self._finalize_stage = L3FinalizeStage(
            result_builder=self._result_builder,
            event_repo=self._event_repo,
        )
        self._persist_stage = L3PersistStage(
            layer_upsert_builder=self._layer_upsert_builder,
            deletion_hold_enabled=self._deletion_hold_enabled,
        )
        self._decision_stage = L3DecisionStage(
            skip_eligibility=self._skip_eligibility,
            scope_resolution=self._scope_resolution,
            admission_stage=self._admission_stage,
            queue_transition=self._queue_transition,
            l5_queue_transition=l5_queue_transition,
            persist_stage=self._persist_stage,
            now_iso_supplier=self._now_iso_supplier,
            admission_enforced=self._l5_admission_enforced,
        )
        self._extract_failure_stage = L3ExtractFailureStage(
            queue_transition=self._queue_transition,
            persist_stage=self._persist_stage,
            now_iso_supplier=self._now_iso_supplier,
            record_error_event=getattr(self._error_policy, "record_error_event", None),
            retry_max_attempts=int(self._policy.retry_max_attempts),
            retry_backoff_base_sec=int(self._policy.retry_backoff_base_sec),
        )
        self._extract_success_stage = L3ExtractSuccessStage(
            persist_stage=self._persist_stage,
            record_quality_shadow_compare=self._record_quality_shadow_compare,
            l5_queue_transition=l5_queue_transition,
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
        return self._process_job_internal(job=job, allow_l5_handoff=True)

    def process_l5_job(self, job: FileEnrichJobDTO) -> object:
        """L5 lane으로 handoff된 작업을 처리한다."""
        return self._process_job_internal(job=job, allow_l5_handoff=False)

    def _process_job_internal(self, *, job: FileEnrichJobDTO, allow_l5_handoff: bool) -> object:
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
                    preprocess_result = self._preprocess_stage.execute(job=job, file_row=file_row, context=context)
                    with self._perf_tracer.span("l3.decision", phase="decision"):
                        decision = self._decision_stage.evaluate(
                            context=context,
                            job=job,
                            preprocess_result=preprocess_result,
                            l5_lane=not allow_l5_handoff,
                        )
                    language = decision.language
                    admission_decision = decision.admission_decision
                    if decision.finished_status is not None:
                        finished_status = decision.finished_status
                    elif decision.should_extract:
                        extraction = self._extract_stage.execute(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            content_hash=job.content_hash,
                            bypass_zero_relations_retry_pending=(
                                (not allow_l5_handoff)
                                and str(getattr(job, "defer_reason", "") or "").strip() == "retry_zero_relations"
                            ),
                        )
                        if extraction.error_message is not None:
                            finished_status = self._extract_failure_stage.handle_extract_error(
                                context=context,
                                job=job,
                                error_message=extraction.error_message,
                            )
                        else:
                            finished_status = self._extract_success_stage.handle_success(
                                context=context,
                                job=job,
                                language=language,
                                preprocess_result=preprocess_result,
                                admission_decision=admission_decision,
                                extraction=extraction,
                                now_iso=decision.now_iso,
                            )
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

    def get_quality_shadow_mode(self) -> dict[str, object]:
        """L3 quality shadow runtime 설정값을 반환한다."""
        return {
            "enabled": bool(getattr(self, "_quality_shadow_enabled", False)),
            "sample_rate": float(getattr(self, "_quality_shadow_sample_rate", 0.0)),
            "max_files": int(getattr(self, "_quality_shadow_max_files", 0)),
            "lang_allowlist": tuple(sorted(getattr(self, "_quality_shadow_lang_allowlist", set()))),
        }

    def set_quality_shadow_mode(
        self,
        *,
        enabled: bool,
        sample_rate: float,
        max_files: int,
        lang_allowlist: tuple[str, ...],
    ) -> None:
        """L3 quality shadow runtime 모드를 갱신하고 누적치를 초기화한다."""
        self._quality_shadow_enabled = bool(enabled) and self._quality_eval_service is not None
        self._quality_shadow_sample_rate = max(0.0, min(1.0, float(sample_rate)))
        self._quality_shadow_max_files = max(0, int(max_files))
        self._quality_shadow_lang_allowlist = {
            str(item).strip().lower() for item in lang_allowlist if str(item).strip() != ""
        }
        self._quality_shadow_sampled_count = 0
        self._quality_shadow_eval_errors = 0
        self._quality_shadow_accumulators = {}
        self._quality_shadow_flag_counts = {}
        self._quality_shadow_missing_pattern_counts = {}

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

    def _run_preprocess(self, *, job: FileEnrichJobDTO, file_row: object, context: L3JobContext | None = None) -> L3PreprocessResultDTO | None:
        return self._preprocess_io_stage.run(job=job, file_row=file_row, context=context)
