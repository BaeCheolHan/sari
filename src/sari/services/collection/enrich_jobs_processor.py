"""L2/L3 통합 enrich job 오케스트레이션 전담 처리기."""

from __future__ import annotations

import hashlib
from pathlib import Path
import time
import traceback
from typing import Callable
import zlib

from sari.core.exceptions import CollectionError, ErrorContext
from sari.core.models import (
    CollectedFileBodyDTO,
    EnrichStateUpdateDTO,
    FileBodyDeleteTargetDTO,
    FileEnrichFailureUpdateDTO,
    FileEnrichJobDTO,
    LspExtractPersistDTO,
    ToolReadinessStateDTO,
    now_iso8601_utc,
)
from sari.core.text_decode import decode_bytes_with_policy
from sari.services.collection.enrich_result_dto import _L2ResultBuffersDTO


class EnrichJobsProcessor:
    """L2/L3 통합 보강 처리 루프를 담당한다."""

    def __init__(
        self,
        *,
        assert_parent_alive: Callable[[str], None],
        acquire_pending: Callable[[int, str], list[FileEnrichJobDTO]],
        rebalance_jobs_by_language: Callable[[list[FileEnrichJobDTO]], list[FileEnrichJobDTO]],
        file_repo_get_file: Callable[[str, str], object | None],
        retry_max_attempts: int,
        retry_backoff_base_sec: float,
        persist_body_for_read: bool,
        vector_index_sink: object | None,
        is_deletion_hold_enabled: Callable[[], bool],
        resolve_l3_skip_reason: Callable[[FileEnrichJobDTO], str | None],
        build_l3_skipped_readiness: Callable[[FileEnrichJobDTO, str, str], ToolReadinessStateDTO],
        lsp_extract: Callable[[str, str, str], object],
        schedule_l1_probe_after_l3_fallback: Callable[[FileEnrichJobDTO], None],
        record_error_event: Callable[..., None],
        run_mode: str,
        flush_batch_size: int,
        flush_interval_sec: float,
        flush_max_body_bytes: int,
        flush_enrich: Callable[..., None],
        record_enrich_latency: Callable[[float], None],
        record_event: Callable[[str, str, int, str], None] | None,
    ) -> None:
        self._assert_parent_alive = assert_parent_alive
        self._acquire_pending = acquire_pending
        self._rebalance_jobs_by_language = rebalance_jobs_by_language
        self._file_repo_get_file = file_repo_get_file
        self._retry_max_attempts = retry_max_attempts
        self._retry_backoff_base_sec = retry_backoff_base_sec
        self._persist_body_for_read = persist_body_for_read
        self._vector_index_sink = vector_index_sink
        self._is_deletion_hold_enabled = is_deletion_hold_enabled
        self._resolve_l3_skip_reason = resolve_l3_skip_reason
        self._build_l3_skipped_readiness = build_l3_skipped_readiness
        self._lsp_extract = lsp_extract
        self._schedule_l1_probe_after_l3_fallback = schedule_l1_probe_after_l3_fallback
        self._record_error_event = record_error_event
        self._run_mode = run_mode
        self._flush_batch_size = flush_batch_size
        self._flush_interval_sec = flush_interval_sec
        self._flush_max_body_bytes = flush_max_body_bytes
        self._flush_enrich = flush_enrich
        self._record_enrich_latency = record_enrich_latency
        self._record_event = record_event

    def process_jobs(self, *, limit: int) -> int:
        """L2/L3 통합 보강 작업을 수행한다."""
        self._assert_parent_alive("enrich_worker")
        jobs = self._acquire_pending(limit, now_iso8601_utc())
        jobs = self._rebalance_jobs_by_language(jobs)
        processed = 0
        buffers = _L2ResultBuffersDTO.empty()
        body_upserts: list[CollectedFileBodyDTO] = []
        body_buffer_bytes = 0
        last_flush_at = time.perf_counter()
        for job in jobs:
            processed += 1
            now_iso = now_iso8601_utc()
            started_at = time.perf_counter()
            finished_status = "FAILED"
            try:
                file_row = self._file_repo_get_file(job.repo_root, job.relative_path)
                if file_row is None or file_row.is_deleted:
                    buffers.done_ids.append(job.job_id)
                    finished_status = "DONE"
                    continue
                file_path = Path(file_row.absolute_path)
                if not file_path.exists() or not file_path.is_file():
                    failure_now = now_iso8601_utc()
                    buffers.failed_updates.append(
                        FileEnrichFailureUpdateDTO(
                            job_id=job.job_id,
                            error_message="대상 파일이 존재하지 않습니다",
                            now_iso=failure_now,
                            dead_threshold=self._retry_max_attempts,
                            backoff_base_sec=self._retry_backoff_base_sec,
                        )
                    )
                    buffers.state_updates.append(
                        EnrichStateUpdateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enrich_state="FAILED",
                            updated_at=failure_now,
                        )
                    )
                    finished_status = "FAILED"
                    continue
                raw_bytes = file_path.read_bytes()
                stat_now = file_path.stat()
                file_hash_now = job.content_hash
                if stat_now.st_mtime_ns != file_row.mtime_ns or stat_now.st_size != file_row.size_bytes:
                    file_hash_now = hashlib.sha256(raw_bytes).hexdigest()
                if file_hash_now != job.content_hash:
                    buffers.done_ids.append(job.job_id)
                    finished_status = "DONE"
                    continue
                decoded = decode_bytes_with_policy(raw_bytes)
                content_text = decoded.text
                deletion_hold_enabled = self._is_deletion_hold_enabled()
                should_persist_body = self._persist_body_for_read and deletion_hold_enabled
                vector_error_message: str | None = None
                if self._vector_index_sink is not None:
                    try:
                        self._vector_index_sink.upsert_file_embedding(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            content_hash=job.content_hash,
                            content_text=content_text,
                        )
                    except (RuntimeError, OSError, ValueError, TypeError) as exc:
                        self._record_error_event(
                            component="file_collection_service",
                            phase="enrich_vector",
                            severity="error",
                            error_code="ERR_VECTOR_EMBED_FAILED",
                            error_message=f"벡터 임베딩 갱신 실패: {exc}",
                            error_type=type(exc).__name__,
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            job_id=job.job_id,
                            attempt_count=job.attempt_count,
                            context_data={"content_hash": job.content_hash},
                        )
                        vector_error_message = f"벡터 임베딩 갱신 실패: {exc}"
                if vector_error_message is not None:
                    failure_now = now_iso8601_utc()
                    buffers.state_updates.append(
                        EnrichStateUpdateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enrich_state="FAILED",
                            updated_at=failure_now,
                        )
                    )
                    buffers.failed_updates.append(
                        FileEnrichFailureUpdateDTO(
                            job_id=job.job_id,
                            error_message=vector_error_message,
                            now_iso=failure_now,
                            dead_threshold=self._retry_max_attempts,
                            backoff_base_sec=self._retry_backoff_base_sec,
                        )
                    )
                    finished_status = "FAILED"
                    continue
                if decoded.decode_warning is not None:
                    self._record_error_event(
                        component="file_collection_service",
                        phase="enrich_decode",
                        severity="warning",
                        error_code="ERR_TEXT_DECODE_FALLBACK",
                        error_message=decoded.decode_warning,
                        error_type="TextDecodeWarning",
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        job_id=job.job_id,
                        attempt_count=job.attempt_count,
                        context_data={"encoding_used": decoded.encoding_used},
                    )
                if should_persist_body:
                    compressed = zlib.compress(content_text.encode("utf-8", errors="surrogateescape"), level=6)
                    body_buffer_bytes += len(compressed)
                    body_upserts.append(
                        CollectedFileBodyDTO(
                            repo_id=job.repo_id,
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            content_hash=job.content_hash,
                            content_zlib=compressed,
                            content_len=len(content_text),
                            normalized_text=content_text.lower(),
                            created_at=now_iso,
                            updated_at=now_iso,
                        )
                    )
                skip_reason = self._resolve_l3_skip_reason(job)
                if skip_reason is not None:
                    buffers.readiness_updates.append(self._build_l3_skipped_readiness(job, skip_reason, now_iso))
                    buffers.state_updates.append(
                        EnrichStateUpdateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enrich_state="L3_SKIPPED",
                            updated_at=now_iso,
                        )
                    )
                    buffers.done_ids.append(job.job_id)
                    finished_status = "DONE"
                    continue
                extraction = self._lsp_extract(job.repo_root, job.relative_path, job.content_hash)
                if extraction.error_message is not None:
                    self._schedule_l1_probe_after_l3_fallback(job)
                    failure_now = now_iso8601_utc()
                    buffers.state_updates.append(
                        EnrichStateUpdateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enrich_state="FAILED",
                            updated_at=failure_now,
                        )
                    )
                    buffers.failed_updates.append(
                        FileEnrichFailureUpdateDTO(
                            job_id=job.job_id,
                            error_message=extraction.error_message,
                            now_iso=failure_now,
                            dead_threshold=self._retry_max_attempts,
                            backoff_base_sec=self._retry_backoff_base_sec,
                        )
                    )
                    self._record_error_event(
                        component="file_collection_service",
                        phase="enrich_extract",
                        severity="error",
                        error_code="ERR_LSP_EXTRACT_FAILED",
                        error_message=extraction.error_message,
                        error_type="LspExtractionError",
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        job_id=job.job_id,
                        attempt_count=job.attempt_count,
                        context_data={"content_hash": job.content_hash},
                    )
                    if self._run_mode == "dev":
                        self._flush_enrich(buffers=buffers, body_upserts=body_upserts)
                        raise CollectionError(
                            ErrorContext(code="ERR_LSP_EXTRACT_FAILED", message=f"LSP 추출 실패: {extraction.error_message}")
                        )
                    continue
                buffers.lsp_updates.append(
                    LspExtractPersistDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        content_hash=job.content_hash,
                        symbols=extraction.symbols,
                        relations=extraction.relations,
                        created_at=now_iso,
                    )
                )
                buffers.readiness_updates.append(
                    ToolReadinessStateDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        content_hash=job.content_hash,
                        list_files_ready=True,
                        read_file_ready=True,
                        search_symbol_ready=True,
                        get_callers_ready=len(extraction.relations) > 0,
                        consistency_ready=True,
                        quality_ready=True,
                        tool_ready=True,
                        last_reason="ok",
                        updated_at=now_iso,
                    )
                )
                if not deletion_hold_enabled:
                    buffers.body_deletes.append(
                        FileBodyDeleteTargetDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            content_hash=job.content_hash,
                        )
                    )
                buffers.state_updates.append(
                    EnrichStateUpdateDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        enrich_state="TOOL_READY",
                        updated_at=now_iso,
                    )
                )
                buffers.done_ids.append(job.job_id)
                finished_status = "DONE"
            except (CollectionError, RuntimeError, OSError, ValueError, zlib.error) as exc:
                failure_now = now_iso8601_utc()
                buffers.state_updates.append(
                    EnrichStateUpdateDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        enrich_state="FAILED",
                        updated_at=failure_now,
                    )
                )
                buffers.failed_updates.append(
                    FileEnrichFailureUpdateDTO(
                        job_id=job.job_id,
                        error_message=f"L2/L3 처리 실패: {exc}",
                        now_iso=failure_now,
                        dead_threshold=self._retry_max_attempts,
                        backoff_base_sec=self._retry_backoff_base_sec,
                    )
                )
                self._record_error_event(
                    component="file_collection_service",
                    phase="enrich_job",
                    severity="critical" if self._run_mode == "dev" else "error",
                    error_code="ERR_ENRICH_JOB_FAILED",
                    error_message=f"L2/L3 처리 실패: {exc}",
                    error_type=type(exc).__name__,
                    repo_root=job.repo_root,
                    relative_path=job.relative_path,
                    job_id=job.job_id,
                    attempt_count=job.attempt_count,
                    context_data={"content_hash": job.content_hash},
                    stacktrace_text=traceback.format_exc(),
                )
                finished_status = "FAILED"
                if self._run_mode == "dev":
                    self._flush_enrich(buffers=buffers, body_upserts=body_upserts)
                    raise CollectionError(ErrorContext(code="ERR_ENRICH_JOB_FAILED", message=f"L2/L3 처리 실패: {exc}")) from exc
            finally:
                elapsed_ms = (time.perf_counter() - started_at) * 1000.0
                self._record_enrich_latency(elapsed_ms)
                if self._record_event is not None:
                    self._record_event(job.job_id, finished_status, int(elapsed_ms), now_iso8601_utc())
            should_flush_by_size = len(buffers.done_ids) + len(buffers.failed_updates) >= self._flush_batch_size
            should_flush_by_time = time.perf_counter() - last_flush_at >= self._flush_interval_sec
            should_flush_by_body = body_buffer_bytes >= self._flush_max_body_bytes
            if should_flush_by_size or should_flush_by_time or should_flush_by_body:
                self._flush_enrich(buffers=buffers, body_upserts=body_upserts)
                body_buffer_bytes = 0
                last_flush_at = time.perf_counter()
        self._flush_enrich(buffers=buffers, body_upserts=body_upserts)
        return processed
