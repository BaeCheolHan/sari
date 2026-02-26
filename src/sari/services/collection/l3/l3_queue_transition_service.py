"""L3 queue 상태 전이 서비스."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Callable

from sari.core.models import L4AdmissionDecisionDTO, L5RejectReason
from sari.core.models import FileEnrichJobDTO

from .l3_broker_admission_service import L3BrokerAdmissionService

log = logging.getLogger(__name__)


class L3QueueTransitionService:
    """L3 defer/escalation queue 상태 전이를 담당한다."""

    _TSLS_FAST_PATH_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

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
        max_deferred_queue_size: int = 50000,
        max_deferred_per_workspace: int = 3000,
        deferred_ttl_hours: int = 168,
    ) -> None:
        self._queue_repo = queue_repo
        self._error_policy = error_policy
        self._now_iso_supplier = now_iso_supplier
        self._broker_admission = broker_admission
        self._extract_error_code = extract_error_code
        self._is_scope_escalation_trigger = is_scope_escalation_trigger
        self._next_scope_level_for_escalation = next_scope_level_for_escalation
        self._max_deferred_queue_size = max(1, int(max_deferred_queue_size))
        self._max_deferred_per_workspace = max(1, int(max_deferred_per_workspace))
        self._deferred_ttl_hours = max(1, int(deferred_ttl_hours))

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

    def defer_after_l5_admission_rejection(self, *, job: FileEnrichJobDTO, admission: L4AdmissionDecisionDTO) -> bool:
        """L5 admission reject 중 재시도 가치가 있는 사유를 queue defer로 되돌린다."""
        reject_reason = admission.reject_reason
        if reject_reason not in {
            L5RejectReason.PRESSURE_RATE_EXCEEDED,
            L5RejectReason.PRESSURE_BURST_EXCEEDED,
            L5RejectReason.PRESSURE_WORKSPACE_EXCEEDED,
            L5RejectReason.COOLDOWN_ACTIVE,
        }:
            return False
        defer_writer = getattr(self._queue_repo, "defer_jobs_to_pending", None)
        if not callable(defer_writer):
            return False
        is_tsls_fast = self._is_tsls_fast_group_path(relative_path=job.relative_path)
        if is_tsls_fast:
            # TSLS fast 경로는 짧은 주기로 재시도한다(사용자 합의: 10~15초).
            delay_by_reason: dict[L5RejectReason, int] = {
                L5RejectReason.PRESSURE_RATE_EXCEEDED: 15,
                L5RejectReason.PRESSURE_BURST_EXCEEDED: 10,
                L5RejectReason.PRESSURE_WORKSPACE_EXCEEDED: 15,
                L5RejectReason.COOLDOWN_ACTIVE: 15,
            }
            defer_reason = f"l5_defer:tsls_fast:{reject_reason.value}"
        else:
            delay_by_reason = {
                L5RejectReason.PRESSURE_RATE_EXCEEDED: 30,
                L5RejectReason.PRESSURE_BURST_EXCEEDED: 10,
                L5RejectReason.PRESSURE_WORKSPACE_EXCEEDED: 20,
                L5RejectReason.COOLDOWN_ACTIVE: 15,
            }
            defer_reason = f"l5_defer:{reject_reason.value}"
        delay_sec = int(delay_by_reason.get(reject_reason, 10))
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
                        max_deferred_queue_size=self._max_deferred_queue_size,
                        max_deferred_per_workspace=self._max_deferred_per_workspace,
                        deferred_ttl_hours=self._deferred_ttl_hours,
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
                "Failed to defer job after L5 admission rejection (job_id=%s, reject_reason=%s, defer_reason=%s)",
                job.job_id,
                reject_reason.value,
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
                phase="enrich_l3_admission_defer",
                severity="warning",
                error_code="ERR_L5_DEFERRED_BY_ADMISSION",
                error_message=f"L5 admission deferred by {reject_reason.value}",
                error_type="L5AdmissionDeferred",
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                job_id=job.job_id,
                attempt_count=job.attempt_count,
                context_data={
                    "defer_reason": defer_reason,
                    "next_retry_at": next_retry_at,
                    "policy_version": admission.policy_version,
                    "reject_stage": admission.reject_stage,
                    "primary_cause": admission.primary_cause,
                },
            )
        return True

    def defer_after_preprocess_heavy(self, *, job: FileEnrichJobDTO, reason: str) -> bool:
        """DEFERRED_HEAVY 전처리 결과를 L5 보강 defer 큐로 보낸다."""
        defer_writer = getattr(self._queue_repo, "defer_jobs_to_pending", None)
        if not callable(defer_writer):
            return False
        now_dt = self._now_datetime_utc()
        next_retry_at = (now_dt + timedelta(seconds=60)).isoformat()
        now_iso = now_dt.isoformat()
        defer_reason = f"l5_defer:deferred_heavy:{reason.strip() or 'unknown'}"
        try:
            try:
                updated = int(
                    defer_writer(
                        job_ids=[job.job_id],
                        next_retry_at=next_retry_at,
                        defer_reason=defer_reason,
                        now_iso=now_iso,
                        max_deferred_queue_size=self._max_deferred_queue_size,
                        max_deferred_per_workspace=self._max_deferred_per_workspace,
                        deferred_ttl_hours=self._deferred_ttl_hours,
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
                "Failed to defer DEFERRED_HEAVY job (job_id=%s, defer_reason=%s)",
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
                phase="enrich_l3_preprocess_defer",
                severity="warning",
                error_code="ERR_L3_DEFERRED_HEAVY",
                error_message=f"L3 preprocess deferred heavy: {reason}",
                error_type="L3DeferredHeavy",
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                job_id=job.job_id,
                attempt_count=job.attempt_count,
                context_data={
                    "defer_reason": defer_reason,
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

    def _is_tsls_fast_group_path(self, *, relative_path: str) -> bool:
        lowered = relative_path.lower()
        if lowered.endswith(".vue"):
            return False
        return lowered.endswith(self._TSLS_FAST_PATH_EXTENSIONS)

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
