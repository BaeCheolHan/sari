"""L3 queue 상태 전이 서비스."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Callable

from sari.core.models import FileEnrichJobDTO

from .l3_broker_admission_service import L3BrokerAdmissionService

log = logging.getLogger(__name__)


class L3QueueTransitionService:
    """L3 defer/escalation queue 상태 전이를 담당한다."""

    def __init__(
        self,
        *,
        queue_repo: object,
        error_policy: object,
        now_iso_supplier: Callable[[], str],
        broker_admission: L3BrokerAdmissionService,
        extract_error_code: Callable[[str], str],
        is_scope_escalation_trigger: Callable[[str, str], bool],
        next_scope_level_for_escalation: Callable[[str | None], str | None],
        min_defer_sec: int = 5,
    ) -> None:
        self._queue_repo = queue_repo
        self._error_policy = error_policy
        self._now_iso_supplier = now_iso_supplier
        self._broker_admission = broker_admission
        self._extract_error_code = extract_error_code
        self._is_scope_escalation_trigger = is_scope_escalation_trigger
        self._next_scope_level_for_escalation = next_scope_level_for_escalation
        self._min_defer_sec = max(0, int(min_defer_sec))

    def defer_after_broker_lease_denial(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        if not self._broker_admission.is_broker_lease_denial(error_message):
            return False
        defer_writer = getattr(self._queue_repo, "defer_jobs_to_pending", None)
        if not callable(defer_writer):
            return False
        lease_reason = self._broker_admission.extract_lease_reason(error_message)
        defer_reason = self._broker_admission.map_defer_reason(lease_reason)
        delay_sec = self._broker_admission.defer_delay_seconds(lease_reason)
        now_dt = self._now_datetime_utc()
        next_retry_at = (now_dt + timedelta(seconds=delay_sec)).isoformat()
        now_iso = now_dt.isoformat()
        try:
            try:
                updated = int(
                    defer_writer(
                        job_ids=[job.job_id],
                        next_retry_at=next_retry_at,
                        defer_reason=defer_reason,
                        now_iso=now_iso,
                        min_defer_sec=self._min_defer_sec,
                    )
                )
            except TypeError:
                updated = int(
                    defer_writer(
                        job_ids=[job.job_id],
                        next_retry_at=next_retry_at,
                        defer_reason=defer_reason,
                        now_iso=now_iso,
                    )
                )
        except (RuntimeError, OSError, ValueError, TypeError):
            log.warning(
                "Failed to defer job after broker lease denial (job_id=%s, reason=%s)",
                job.job_id,
                defer_reason,
                exc_info=True,
            )
            return False
        if updated <= 0:
            return False
        record_error_event = getattr(self._error_policy, "record_error_event", None)
        if callable(record_error_event):
            record_error_event(
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

    def escalate_scope_after_l3_extract_error(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        escalator = getattr(self._queue_repo, "escalate_scope_on_same_job", None)
        if not callable(escalator):
            return False
        error_code = self._extract_error_code(error_message)
        if not self._is_scope_escalation_trigger(error_code, error_message):
            return False
        current_attempts = max(0, int(getattr(job, "scope_attempts", 0)))
        if current_attempts >= 2:
            return False
        next_scope_level = self._next_scope_level_for_escalation(getattr(job, "scope_level", None))
        if next_scope_level is None:
            return False
        next_scope_root = self._resolve_next_scope_root(job=job, next_scope_level=next_scope_level)
        now_iso = self._now_iso_supplier()
        try:
            try:
                updated = bool(
                    escalator(
                        job_id=job.job_id,
                        next_scope_level=next_scope_level,
                        next_scope_root=next_scope_root,
                        next_retry_at=now_iso,
                        now_iso=now_iso,
                        min_defer_sec=self._min_defer_sec,
                    )
                )
            except TypeError:
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
            log.warning(
                "Failed to escalate scope after L3 extract error (job_id=%s, next_scope=%s)",
                job.job_id,
                next_scope_level,
                exc_info=True,
            )
            return False
        if not updated:
            return False
        record_error_event = getattr(self._error_policy, "record_error_event", None)
        if callable(record_error_event):
            record_error_event(
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

    def _resolve_next_scope_root(self, *, job: FileEnrichJobDTO, next_scope_level: str) -> str:
        if next_scope_level == "workspace":
            return job.repo_root
        if next_scope_level == "repo":
            parts = Path(job.relative_path).parts
            if len(parts) >= 2 and parts[0] not in ("", ".", ".."):
                return str(Path(job.repo_root) / parts[0])
        return job.repo_root

    def _now_datetime_utc(self) -> datetime:
        now_iso_supplier = self._now_iso_supplier
        try:
            raw_now_iso = str(now_iso_supplier())
            now_dt = datetime.fromisoformat(raw_now_iso.replace("Z", "+00:00"))
            if now_dt.tzinfo is None:
                return now_dt.replace(tzinfo=timezone.utc)
            return now_dt.astimezone(timezone.utc)
        except (RuntimeError, OSError, ValueError, TypeError):
            return datetime.now(timezone.utc)
