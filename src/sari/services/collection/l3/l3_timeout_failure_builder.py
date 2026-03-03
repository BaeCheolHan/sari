"""L3 그룹 timeout failure 결과 합성 빌더."""

from __future__ import annotations

from typing import Callable

from sari.core.language.registry import resolve_language_from_path
from sari.core.models import EnrichStateUpdateDTO, FileEnrichFailureUpdateDTO, FileEnrichJobDTO
from sari.services.collection.enrich_result_dto import _L3JobResultDTO


class L3TimeoutFailureBuilder:
    """L3 병렬 그룹 timeout 시 실패 결과 합성을 담당한다."""

    def __init__(
        self,
        *,
        retry_max_attempts: int,
        retry_backoff_base_sec: float,
        record_error_event: Callable[..., None],
    ) -> None:
        self._retry_max_attempts = retry_max_attempts
        self._retry_backoff_base_sec = retry_backoff_base_sec
        self._record_error_event = record_error_event

    def build(
        self,
        *,
        job: FileEnrichJobDTO,
        timeout_sec: float,
        now_iso: str,
        group_size: int,
    ) -> _L3JobResultDTO:
        """병렬 L3 그룹 timeout으로 완료되지 않은 job을 FAILED 결과로 합성한다."""
        language = resolve_language_from_path(file_path=job.relative_path)
        language_name = "unknown" if language is None else language.value
        error_message = (
            "L3 병렬 작업 타임아웃: "
            f"repo={job.repo_root}, path={job.relative_path}, language={language_name}, "
            f"group_size={group_size}, timeout_sec={timeout_sec:.1f}"
        )
        failure_update = FileEnrichFailureUpdateDTO(
            job_id=job.job_id,
            error_message=error_message,
            now_iso=now_iso,
            dead_threshold=self._retry_max_attempts,
            backoff_base_sec=self._retry_backoff_base_sec,
        )
        state_update = EnrichStateUpdateDTO(
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            enrich_state="FAILED",
            updated_at=now_iso,
        )
        self._record_error_event(
            component="file_collection_service",
            phase="enrich_l3_group",
            severity="error",
            error_code="ERR_ENRICH_L3_GROUP_TIMEOUT",
            error_message=error_message,
            error_type="L3GroupTimeout",
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            job_id=job.job_id,
            attempt_count=job.attempt_count,
            context_data={"content_hash": job.content_hash},
        )
        return _L3JobResultDTO(
            job_id=job.job_id,
            finished_status="FAILED",
            elapsed_ms=0.0,
            done_id=None,
            failure_update=failure_update,
            state_update=state_update,
            body_delete=None,
            lsp_update=None,
            readiness_update=None,
            dev_error=None,
        )

