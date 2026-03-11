"""L5 defer queue 상태 전이 서비스."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Callable

from sari.core.models import FileEnrichJobDTO, L4AdmissionDecisionDTO, L5RejectReason

log = logging.getLogger(__name__)


class L5QueueDeferService:
    """L5 admission/preprocess defer 전이를 담당한다."""

    _TSLS_FAST_PATH_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

    def __init__(
        self,
        *,
        queue_repo: object,
        error_policy: object,
        now_iso_supplier: Callable[[], str] | None = None,
        max_deferred_queue_size: int = 50000,
        max_deferred_per_workspace: int = 3000,
        deferred_ttl_hours: int = 168,
    ) -> None:
        self._queue_repo = queue_repo
        self._error_policy = error_policy
        self._now_iso_supplier = now_iso_supplier
        self._max_deferred_queue_size = max(1, int(max_deferred_queue_size))
        self._max_deferred_per_workspace = max(1, int(max_deferred_per_workspace))
        self._deferred_ttl_hours = max(1, int(deferred_ttl_hours))

    def defer_after_l5_admission_rejection(self, *, job: FileEnrichJobDTO, admission: L4AdmissionDecisionDTO) -> bool:
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

    def defer_after_zero_relations(self, *, job: FileEnrichJobDTO) -> bool:
        if str(getattr(job, "defer_reason", "") or "").strip() == "retry_zero_relations" and int(job.deferred_count) >= 1:
            return False
        enqueue_writer = getattr(self._queue_repo, "enqueue", None)
        if not callable(enqueue_writer):
            return False
        now_dt = self._now_datetime_utc()
        next_retry_at = (now_dt + timedelta(seconds=15)).isoformat()
        now_iso = now_dt.isoformat()
        defer_reason = "retry_zero_relations"
        retry_job_id: str | None = None
        had_preexisting_retry_row = False
        delete_writer = getattr(self._queue_repo, "delete_job", None)
        find_reusable_job_id = getattr(self._queue_repo, "find_reusable_job_id", None)
        get_job = getattr(self._queue_repo, "get_job", None)
        restore_job = getattr(self._queue_repo, "restore_job", None)
        preexisting_job_snapshot = None
        try:
            if callable(find_reusable_job_id):
                try:
                    existing_retry_job_id = find_reusable_job_id(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enqueue_source="l5",
                            repo_id=job.repo_id,
                        )
                except TypeError:
                    existing_retry_job_id = find_reusable_job_id(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enqueue_source="l5",
                        )
                had_preexisting_retry_row = bool(existing_retry_job_id)
                if had_preexisting_retry_row and callable(get_job):
                    preexisting_job_snapshot = get_job(job_id=str(existing_retry_job_id))
            retry_job_id = str(
                enqueue_writer(
                    repo_root=job.repo_root,
                    relative_path=job.relative_path,
                    content_hash=job.content_hash,
                    priority=job.priority,
                    enqueue_source="l5",
                    now_iso=next_retry_at,
                    repo_id=job.repo_id,
                )
            )
            defer_writer = getattr(self._queue_repo, "defer_jobs_to_pending", None)
            updated = 0
            if not callable(defer_writer):
                raise RuntimeError("defer_jobs_to_pending unavailable")
            try:
                updated = int(
                    defer_writer(
                        job_ids=[retry_job_id],
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
                        job_ids=[retry_job_id],
                        next_retry_at=next_retry_at,
                        defer_reason=defer_reason,
                        now_iso=now_iso,
                    )
                )
            if updated <= 0:
                self._rollback_zero_relations_retry_row(
                    retry_job_id=retry_job_id,
                    had_preexisting_retry_row=had_preexisting_retry_row,
                    preexisting_job_snapshot=preexisting_job_snapshot,
                    delete_writer=delete_writer,
                    restore_job=restore_job,
                )
                return False
        except (RuntimeError, OSError, ValueError, TypeError):
            self._rollback_zero_relations_retry_row(
                retry_job_id=retry_job_id,
                had_preexisting_retry_row=had_preexisting_retry_row,
                preexisting_job_snapshot=preexisting_job_snapshot,
                delete_writer=delete_writer,
                restore_job=restore_job,
            )
            log.warning(
                "Failed to schedule zero-relation L5 retry (job_id=%s, defer_reason=%s)",
                job.job_id,
                defer_reason,
                exc_info=True,
            )
            return False
        record_error_event = getattr(self._error_policy, "record_error_event", None)
        if callable(record_error_event):
            record_error_event(
                component="file_collection_service",
                phase="enrich_l5_zero_relations_defer",
                severity="warning",
                error_code="ERR_L5_ZERO_RELATIONS_DEFERRED",
                error_message="L5 zero-relations retry scheduled",
                error_type="L5ZeroRelationsDeferred",
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                job_id=job.job_id,
                attempt_count=job.attempt_count,
                context_data={
                    "defer_reason": defer_reason,
                    "next_retry_at": next_retry_at,
                    "deferred_count": int(job.deferred_count),
                },
            )
        return True

    @staticmethod
    def _rollback_zero_relations_retry_row(
        *,
        retry_job_id: str | None,
        had_preexisting_retry_row: bool,
        preexisting_job_snapshot: object,
        delete_writer: object,
        restore_job: object,
    ) -> None:
        if had_preexisting_retry_row:
            if callable(restore_job) and preexisting_job_snapshot is not None:
                try:
                    restore_job(preexisting_job_snapshot)
                except (RuntimeError, OSError, ValueError, TypeError):
                    log.debug("Failed to restore preexisting zero-relation retry row", exc_info=True)
            return
        if callable(delete_writer) and retry_job_id is not None:
            try:
                delete_writer(job_id=retry_job_id)
            except (RuntimeError, OSError, ValueError, TypeError):
                log.debug("Failed to rollback zero-relation retry row (job_id=%s)", retry_job_id, exc_info=True)

    @classmethod
    def _is_tsls_fast_group_path(cls, *, relative_path: str) -> bool:
        suffix = Path(relative_path).suffix.lower()
        return suffix in cls._TSLS_FAST_PATH_EXTENSIONS

    def _now_datetime_utc(self) -> datetime:
        now_iso_supplier = self._now_iso_supplier
        if now_iso_supplier is None:
            return datetime.now(timezone.utc)
        try:
            raw_now_iso = str(now_iso_supplier())
            now_dt = datetime.fromisoformat(raw_now_iso.replace("Z", "+00:00"))
            if now_dt.tzinfo is None:
                return now_dt.replace(tzinfo=timezone.utc)
            return now_dt.astimezone(timezone.utc)
        except (RuntimeError, OSError, ValueError, TypeError):
            return datetime.now(timezone.utc)
