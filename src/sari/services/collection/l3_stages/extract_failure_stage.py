"""L3 extract 실패 전이(stage): defer/escalate/failure."""

from __future__ import annotations

from typing import Callable

from sari.core.models import FileEnrichJobDTO

from ..l3_job_context import L3JobContext


class L3ExtractFailureStage:
    """extract error 발생 시 전이 정책을 일관 처리한다."""

    def __init__(
        self,
        *,
        queue_transition: object,
        persist_stage: object,
        now_iso_supplier: Callable[[], str],
        record_error_event: Callable[..., None] | None,
        retry_max_attempts: int,
        retry_backoff_base_sec: int,
    ) -> None:
        self._queue_transition = queue_transition
        self._persist_stage = persist_stage
        self._now_iso_supplier = now_iso_supplier
        self._record_error_event = record_error_event
        self._retry_max_attempts = int(retry_max_attempts)
        self._retry_backoff_base_sec = int(retry_backoff_base_sec)

    def handle_extract_error(
        self,
        *,
        context: L3JobContext,
        job: FileEnrichJobDTO,
        error_message: str,
    ) -> str:
        defer_fn = getattr(self._queue_transition, "defer_after_broker_lease_denial", None)
        if callable(defer_fn):
            deferred = bool(defer_fn(job=job, error_message=error_message))
            if deferred:
                return "PENDING"
        escalate_fn = getattr(self._queue_transition, "escalate_scope_after_l3_extract_error", None)
        if callable(escalate_fn):
            escalated = bool(escalate_fn(job=job, error_message=error_message))
            if escalated:
                return "PENDING"
        failure_now = self._now_iso_supplier()
        self._persist_stage.mark_failure(
            context=context,
            job_id=job.job_id,
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            now_iso=failure_now,
            error_message=error_message,
            dead_threshold=self._retry_max_attempts,
            backoff_base_sec=self._retry_backoff_base_sec,
        )
        if callable(self._record_error_event):
            self._record_error_event(
                component="file_collection_service",
                phase="enrich_l3_extract",
                severity="error",
                error_code="ERR_LSP_EXTRACT_FAILED",
                error_message=error_message,
                error_type="LspExtractionError",
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                job_id=job.job_id,
                attempt_count=job.attempt_count,
                context_data={"content_hash": job.content_hash},
            )
        return "FAILED"
