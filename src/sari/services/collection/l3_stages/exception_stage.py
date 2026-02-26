"""L3 orchestration 예외 전이(stage)."""

from __future__ import annotations

from typing import Callable

from sari.core.exceptions import CollectionError, ErrorContext
from sari.core.models import FileEnrichJobDTO

from ..l3_job_context import L3JobContext


class L3ExceptionStage:
    """process_job outer 예외를 실패 상태로 정규화한다."""

    def __init__(
        self,
        *,
        persist_stage: object,
        now_iso_supplier: Callable[[], str],
        record_error_event: Callable[..., None] | None,
        retry_max_attempts: int,
        retry_backoff_base_sec: int,
        run_mode: str,
    ) -> None:
        self._persist_stage = persist_stage
        self._now_iso_supplier = now_iso_supplier
        self._record_error_event = record_error_event
        self._retry_max_attempts = int(retry_max_attempts)
        self._retry_backoff_base_sec = int(retry_backoff_base_sec)
        self._run_mode = str(run_mode)

    def handle_exception(
        self,
        *,
        context: L3JobContext,
        job: FileEnrichJobDTO,
        exc: Exception,
    ) -> CollectionError | None:
        error_message = f"L3 처리 실패: {exc}"
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
                phase="enrich_l3",
                severity="critical" if self._run_mode == "dev" else "error",
                error_code="ERR_ENRICH_L3_FAILED",
                error_message=error_message,
                error_type=type(exc).__name__,
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                job_id=job.job_id,
                attempt_count=job.attempt_count,
                context_data={"content_hash": job.content_hash},
            )
        if self._run_mode == "dev":
            return CollectionError(ErrorContext(code="ERR_ENRICH_L3_FAILED", message=error_message))
        return None
