"""L3 extract error 기반 scope escalation/defer 처리를 담당한다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_failure_classifier import (
    broker_defer_delay_seconds_for_reason,
    extract_broker_lease_reason_from_l3_error,
    extract_error_code_from_lsp_error_message,
    is_scope_escalation_trigger_error_for_l3,
    map_broker_lease_reason_to_defer_reason,
    next_scope_level_for_l3_escalation,
)


class _ErrorPolicyPort:
    def record_error_event(self, **kwargs: object) -> None: ...


class L3ErrorHandlingService:
    def __init__(
        self,
        *,
        queue_repo: object,
        error_policy: _ErrorPolicyPort,
        now_iso_supplier: Callable[[], str],
    ) -> None:
        self._queue_repo = queue_repo
        self._error_policy = error_policy
        self._now_iso_supplier = now_iso_supplier

    def try_escalate_scope_after_l3_extract_error(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        escalator = getattr(self._queue_repo, "escalate_scope_on_same_job", None)
        if not callable(escalator):
            return False
        error_code = extract_error_code_from_lsp_error_message(error_message)
        if not is_scope_escalation_trigger_error_for_l3(code=error_code, message=error_message):
            return False
        current_attempts = max(0, int(getattr(job, "scope_attempts", 0)))
        if current_attempts >= 2:
            return False
        next_scope_level = next_scope_level_for_l3_escalation(getattr(job, "scope_level", None))
        if next_scope_level is None:
            return False
        next_scope_root = self.resolve_next_scope_root_for_escalation(job=job, next_scope_level=next_scope_level)
        now_iso = self._now_iso_supplier()
        try:
            updated = bool(
                escalator(
                    job_id=job.job_id,
                    next_scope_level=next_scope_level,
                    next_scope_root=next_scope_root,
                    next_retry_at=now_iso,
                    now_iso=now_iso,
                )
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            return False
        if not updated:
            return False
        self._error_policy.record_error_event(
            component="file_collection_service",
            phase="enrich_l3_extract_scope_escalation",
            severity="warning",
            error_code="ERR_L3_SCOPE_ESCALATED",
            error_message=error_message,
            error_type="LspExtractionError",
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            job_id=job.job_id,
            attempt_count=job.attempt_count,
            context_data={
                "l3_error_code": error_code,
                "prev_scope_level": getattr(job, "scope_level", None) or "module",
                "next_scope_level": next_scope_level,
                "next_scope_root": next_scope_root,
                "scope_attempts_before": current_attempts,
                "scope_attempts_after": current_attempts + 1,
            },
        )
        return True

    def try_defer_after_broker_lease_denial(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        if "ERR_LSP_BROKER_LEASE_REQUIRED" not in error_message:
            return False
        defer_writer = getattr(self._queue_repo, "defer_jobs_to_pending", None)
        if not callable(defer_writer):
            return False
        now_dt = datetime.now(timezone.utc)
        lease_reason = extract_broker_lease_reason_from_l3_error(error_message)
        defer_reason = map_broker_lease_reason_to_defer_reason(lease_reason)
        defer_delay_sec = broker_defer_delay_seconds_for_reason(lease_reason)
        next_retry_at = (now_dt + timedelta(seconds=defer_delay_sec)).isoformat()
        now_iso = now_dt.isoformat()
        try:
            updated = int(
                defer_writer(
                    job_ids=[job.job_id],
                    next_retry_at=next_retry_at,
                    defer_reason=defer_reason,
                    now_iso=now_iso,
                )
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            return False
        if updated <= 0:
            return False
        self._error_policy.record_error_event(
            component="file_collection_service",
            phase="enrich_l3_broker_defer",
            severity="warning",
            error_code="ERR_L3_DEFERRED_BY_BROKER",
            error_message=error_message,
            error_type="LspBrokerLeaseDenied",
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            job_id=job.job_id,
            attempt_count=job.attempt_count,
            context_data={
                "defer_reason": defer_reason,
                "lease_reason": lease_reason,
                "next_retry_at": next_retry_at,
            },
        )
        return True

    @staticmethod
    def resolve_next_scope_root_for_escalation(*, job: FileEnrichJobDTO, next_scope_level: str) -> str:
        if next_scope_level == "workspace":
            return job.repo_root
        if next_scope_level == "repo":
            parts = Path(job.relative_path).parts
            if len(parts) >= 2 and parts[0] not in ("", ".", ".."):
                return str(Path(job.repo_root) / parts[0])
        return job.repo_root
