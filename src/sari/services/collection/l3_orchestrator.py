"""L3 단일 job 오케스트레이터."""

from __future__ import annotations

import time
from datetime import timezone, datetime
from typing import Callable

from sari.core.exceptions import CollectionError, ErrorContext
from sari.core.models import (
    EnrichStateUpdateDTO,
    FileBodyDeleteTargetDTO,
    FileEnrichFailureUpdateDTO,
    FileEnrichJobDTO,
    LspExtractPersistDTO,
    ToolReadinessStateDTO,
)

from .l3_queue_transition_service import L3QueueTransitionService
from .l3_scope_resolution_service import L3ScopeResolutionService
from .l3_skip_eligibility_service import L3SkipEligibilityService
from .l3_persist_service import L3PersistService


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

    def process_job(self, job: FileEnrichJobDTO) -> object:
        started_at = time.perf_counter()
        finished_status = "FAILED"
        done_id = None
        failure_update = None
        state_update = None
        body_delete = None
        lsp_update = None
        readiness_update = None
        dev_error = None
        try:
            file_row = self._file_repo.get_file(job.repo_root, job.relative_path)
            if file_row is None or file_row.is_deleted:
                done_id = job.job_id
                finished_status = "DONE"
            elif file_row.content_hash != job.content_hash:
                done_id = job.job_id
                finished_status = "DONE"
            elif self._skip_eligibility.is_recent_tool_ready(job):
                now_iso = self._now_iso_supplier()
                state_update = EnrichStateUpdateDTO(
                    repo_root=job.repo_root,
                    relative_path=job.relative_path,
                    enrich_state="TOOL_READY",
                    updated_at=now_iso,
                )
                readiness_update = ToolReadinessStateDTO(
                    repo_root=job.repo_root,
                    relative_path=job.relative_path,
                    content_hash=job.content_hash,
                    list_files_ready=True,
                    read_file_ready=True,
                    search_symbol_ready=True,
                    get_callers_ready=True,
                    consistency_ready=True,
                    quality_ready=True,
                    tool_ready=True,
                    last_reason="skip_recent_success",
                    updated_at=now_iso,
                )
                if not self._deletion_hold_enabled():
                    body_delete = FileBodyDeleteTargetDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        content_hash=job.content_hash,
                    )
                done_id = job.job_id
                finished_status = "DONE"
            else:
                now_iso = self._now_iso_supplier()
                skip_reason = self._skip_eligibility.resolve_skip_reason(job)
                if skip_reason is not None:
                    state_update = EnrichStateUpdateDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        enrich_state="L3_SKIPPED",
                        updated_at=now_iso,
                    )
                    readiness_update = self._skip_eligibility.build_skipped_readiness(job=job, reason=skip_reason, now_iso=now_iso)
                    done_id = job.job_id
                    finished_status = "DONE"
                else:
                    _ = self._scope_resolution.resolve_language(job.relative_path)
                    extraction = self._lsp_backend.extract(job.repo_root, job.relative_path, job.content_hash)
                    if extraction.error_message is not None:
                        deferred = self._queue_transition.defer_after_broker_lease_denial(job=job, error_message=extraction.error_message)
                        if deferred:
                            finished_status = "PENDING"
                        else:
                            escalated = self._queue_transition.escalate_scope_after_l3_extract_error(job=job, error_message=extraction.error_message)
                            if escalated:
                                finished_status = "PENDING"
                            else:
                                self._schedule_l1_probe_after_l3_fallback(job)
                                failure_now = self._now_iso_supplier()
                                failure_kind = self._classify_failure_kind(extraction.error_message)
                                state_update = EnrichStateUpdateDTO(
                                    repo_root=job.repo_root,
                                    relative_path=job.relative_path,
                                    enrich_state="FAILED",
                                    updated_at=failure_now,
                                )
                                failure_update = FileEnrichFailureUpdateDTO(
                                    job_id=job.job_id,
                                    error_message=extraction.error_message,
                                    now_iso=failure_now,
                                    dead_threshold=self._policy.retry_max_attempts,
                                    backoff_base_sec=self._policy.retry_backoff_base_sec,
                                )
                                record_error_event = getattr(self._error_policy, "record_error_event", None)
                                if callable(record_error_event):
                                    record_error_event(
                                        component="file_collection_service",
                                        phase="enrich_l3_extract",
                                        severity="error",
                                        error_code="ERR_LSP_EXTRACT_FAILED",
                                        error_message=extraction.error_message,
                                        error_type="LspExtractionError",
                                        repo_root=job.repo_root,
                                        relative_path=job.relative_path,
                                        job_id=job.job_id,
                                        attempt_count=job.attempt_count,
                                        context_data={"content_hash": job.content_hash, "failure_kind": failure_kind},
                                    )
                                if self._run_mode == "dev":
                                    dev_error = CollectionError(
                                        ErrorContext(code="ERR_LSP_EXTRACT_FAILED", message=f"LSP 추출 실패: {extraction.error_message}")
                                    )
                    else:
                        lsp_update = LspExtractPersistDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            content_hash=job.content_hash,
                            symbols=extraction.symbols,
                            relations=extraction.relations,
                            created_at=now_iso,
                        )
                        readiness_update = ToolReadinessStateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            content_hash=job.content_hash,
                            list_files_ready=True,
                            read_file_ready=True,
                            search_symbol_ready=True,
                            get_callers_ready=True,
                            consistency_ready=True,
                            quality_ready=True,
                            tool_ready=True,
                            last_reason="ok",
                            updated_at=now_iso,
                        )
                        if not self._deletion_hold_enabled():
                            body_delete = FileBodyDeleteTargetDTO(
                                repo_root=job.repo_root,
                                relative_path=job.relative_path,
                                content_hash=job.content_hash,
                            )
                        state_update = EnrichStateUpdateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enrich_state="TOOL_READY",
                            updated_at=now_iso,
                        )
                        self._persist_service.record_scope_learning(job)
                        done_id = job.job_id
                        finished_status = "DONE"
        except (CollectionError, RuntimeError, OSError, ValueError) as exc:
            failure_now = self._now_iso_supplier()
            state_update = EnrichStateUpdateDTO(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                enrich_state="FAILED",
                updated_at=failure_now,
            )
            failure_update = FileEnrichFailureUpdateDTO(
                job_id=job.job_id,
                error_message=f"L3 처리 실패: {exc}",
                now_iso=failure_now,
                dead_threshold=self._policy.retry_max_attempts,
                backoff_base_sec=self._policy.retry_backoff_base_sec,
            )
            record_error_event = getattr(self._error_policy, "record_error_event", None)
            if callable(record_error_event):
                record_error_event(
                    component="file_collection_service",
                    phase="enrich_l3",
                    severity="critical" if self._run_mode == "dev" else "error",
                    error_code="ERR_ENRICH_L3_FAILED",
                    error_message=f"L3 처리 실패: {exc}",
                    error_type=type(exc).__name__,
                    repo_root=job.repo_root,
                    relative_path=job.relative_path,
                    job_id=job.job_id,
                    attempt_count=job.attempt_count,
                    context_data={"content_hash": job.content_hash},
                )
            if self._run_mode == "dev":
                dev_error = CollectionError(ErrorContext(code="ERR_ENRICH_L3_FAILED", message=f"L3 처리 실패: {exc}"))
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        self._record_enrich_latency(elapsed_ms)
        if self._event_repo is not None:
            self._event_repo.record_event(
                job_id=job.job_id,
                status=finished_status,
                latency_ms=int(elapsed_ms),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        return self._result_builder(
            job_id=job.job_id,
            finished_status=finished_status,
            elapsed_ms=elapsed_ms,
            done_id=done_id,
            failure_update=failure_update,
            state_update=state_update,
            body_delete=body_delete,
            lsp_update=lsp_update,
            readiness_update=readiness_update,
            dev_error=dev_error,
        )

