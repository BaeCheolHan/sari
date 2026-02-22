"""L2/L3 보강 파이프라인 전용 엔진."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass
import hashlib
from datetime import datetime, timedelta, timezone
import queue
import time
import traceback
import zlib
from collections import deque
from pathlib import Path
from typing import Callable

from solidlsp.ls_config import Language

from sari.core.exceptions import CollectionError, ErrorContext
from sari.core.language_registry import get_enabled_language_names, resolve_language_from_path
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
from sari.services.collection.error_policy import CollectionErrorPolicy
from sari.services.collection.perf_trace import PerfTracer


@dataclass(frozen=True)
class _L3JobResultDTO:
    job_id: str
    finished_status: str
    elapsed_ms: float
    done_id: str | None
    failure_update: FileEnrichFailureUpdateDTO | None
    state_update: EnrichStateUpdateDTO | None
    body_delete: FileBodyDeleteTargetDTO | None
    lsp_update: LspExtractPersistDTO | None
    readiness_update: ToolReadinessStateDTO | None
    dev_error: CollectionError | None


class EnrichEngine:
    """파일 보강(L2/L3) 처리와 bootstrap 모드를 관리한다."""

    def __init__(
        self,
        *,
        file_repo: object,
        enrich_queue_repo: object,
        body_repo: object,
        lsp_repo: object,
        readiness_repo: object,
        policy: object,
        lsp_backend: object,
        policy_repo: object | None,
        event_repo: object | None,
        vector_index_sink: object | None,
        run_mode: str,
        persist_body_for_read: bool,
        l3_ready_queue: queue.Queue[FileEnrichJobDTO],
        error_policy: CollectionErrorPolicy,
        record_enrich_latency: Callable[[float], None],
        assert_parent_alive: Callable[[str], None],
        flush_batch_size: int,
        flush_interval_sec: float,
        flush_max_body_bytes: int,
        l3_parallel_enabled: bool,
        l3_executor_max_workers: int,
        l3_recent_success_ttl_sec: int,
        l3_backpressure_on_interactive: bool,
        l3_backpressure_cooldown_ms: int,
        l3_supported_languages: tuple[str, ...],
        lsp_probe_l1_languages: tuple[str, ...],
    ) -> None:
        """엔진 실행에 필요한 의존성을 주입받는다."""
        self._file_repo = file_repo
        self._enrich_queue_repo = enrich_queue_repo
        self._body_repo = body_repo
        self._lsp_repo = lsp_repo
        self._readiness_repo = readiness_repo
        self._policy = policy
        self._lsp_backend = lsp_backend
        self._policy_repo = policy_repo
        self._event_repo = event_repo
        self._vector_index_sink = vector_index_sink
        self._run_mode = "prod" if run_mode == "prod" else "dev"
        self._persist_body_for_read = persist_body_for_read
        self._l3_ready_queue = l3_ready_queue
        self._error_policy = error_policy
        self._record_enrich_latency = record_enrich_latency
        self._assert_parent_alive = assert_parent_alive
        self._flush_batch_size = flush_batch_size
        self._flush_interval_sec = flush_interval_sec
        self._flush_max_body_bytes = flush_max_body_bytes
        self._l3_parallel_enabled = bool(l3_parallel_enabled)
        self._l3_executor_max_workers = max(1, int(l3_executor_max_workers)) if int(l3_executor_max_workers) > 0 else 32
        self._l3_group_wait_timeout_sec = 90.0
        self._l3_recent_success_ttl_sec = max(0, int(l3_recent_success_ttl_sec))
        self._l3_backpressure_on_interactive = bool(l3_backpressure_on_interactive)
        self._l3_backpressure_cooldown_sec = max(0.01, float(max(10, int(l3_backpressure_cooldown_ms))) / 1000.0)
        self._l3_backpressure_until = 0.0
        self._last_interactive_timeout_count = 0
        self._l3_executor = ThreadPoolExecutor(max_workers=self._l3_executor_max_workers, thread_name_prefix="enrich-l3")
        self._l3_executor_closed = False
        self._indexing_mode = "steady"
        self._bootstrap_started_at = time.monotonic()
        self._l3_supported_languages = self._parse_l3_supported_languages(l3_supported_languages)
        self._lsp_probe_l1_languages = self._parse_lsp_probe_l1_languages(lsp_probe_l1_languages)
        self._perf_tracer = PerfTracer(component="enrich_engine")

    def shutdown(self) -> None:
        """L3 전역 executor를 종료한다."""
        if self._l3_executor_closed:
            return
        self._l3_executor.shutdown(wait=True)
        self._l3_executor_closed = True

    def reset_runtime_state(self) -> None:
        """백그라운드 시작 시 엔진 상태를 초기화한다."""
        self._bootstrap_started_at = time.monotonic()
        self._indexing_mode = "steady"

    def indexing_mode(self) -> str:
        """현재 인덱싱 모드를 반환한다."""
        return self._indexing_mode

    def process_enrich_jobs(self, limit: int) -> int:
        """L2/L3 통합 보강 작업을 수행한다."""
        batch_started_at = time.perf_counter()
        self._assert_parent_alive("enrich_worker")
        jobs = self._enrich_queue_repo.acquire_pending(limit=limit, now_iso=now_iso8601_utc())
        jobs = self._rebalance_jobs_by_language(jobs=jobs)
        processed = 0
        done_ids: list[str] = []
        failed_updates: list[FileEnrichFailureUpdateDTO] = []
        state_updates: list[EnrichStateUpdateDTO] = []
        body_upserts: list[CollectedFileBodyDTO] = []
        body_buffer_bytes = 0
        body_deletes: list[FileBodyDeleteTargetDTO] = []
        lsp_updates: list[LspExtractPersistDTO] = []
        readiness_updates: list[ToolReadinessStateDTO] = []
        last_flush_at = time.perf_counter()
        flush_count = 0
        get_file_elapsed_ms_total = 0.0
        file_io_elapsed_ms_total = 0.0
        decode_elapsed_ms_total = 0.0
        extract_elapsed_ms_total = 0.0
        flush_elapsed_ms_total = 0.0
        for job in jobs:
            processed += 1
            now_iso = now_iso8601_utc()
            started_at = time.perf_counter()
            finished_status = "FAILED"
            try:
                get_file_started_at = time.perf_counter()
                file_row = self._file_repo.get_file(job.repo_root, job.relative_path)
                get_file_elapsed_ms_total += (time.perf_counter() - get_file_started_at) * 1000.0
                if file_row is None or file_row.is_deleted:
                    done_ids.append(job.job_id)
                    finished_status = "DONE"
                    continue
                file_path = Path(file_row.absolute_path)
                if not file_path.exists() or not file_path.is_file():
                    failure_now = now_iso8601_utc()
                    failed_updates.append(
                        FileEnrichFailureUpdateDTO(
                            job_id=job.job_id,
                            error_message="대상 파일이 존재하지 않습니다",
                            now_iso=failure_now,
                            dead_threshold=self._policy.retry_max_attempts,
                            backoff_base_sec=self._policy.retry_backoff_base_sec,
                        )
                    )
                    state_updates.append(
                        EnrichStateUpdateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enrich_state="FAILED",
                            updated_at=failure_now,
                        )
                    )
                    finished_status = "FAILED"
                    continue
                file_io_started_at = time.perf_counter()
                raw_bytes = file_path.read_bytes()
                stat_now = file_path.stat()
                file_hash_now = job.content_hash
                if stat_now.st_mtime_ns != file_row.mtime_ns or stat_now.st_size != file_row.size_bytes:
                    file_hash_now = hashlib.sha256(raw_bytes).hexdigest()
                file_io_elapsed_ms_total += (time.perf_counter() - file_io_started_at) * 1000.0
                if file_hash_now != job.content_hash:
                    done_ids.append(job.job_id)
                    finished_status = "DONE"
                    continue
                decode_started_at = time.perf_counter()
                decoded = decode_bytes_with_policy(raw_bytes)
                decode_elapsed_ms_total += (time.perf_counter() - decode_started_at) * 1000.0
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
                        self._error_policy.record_error_event(
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
                    state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="FAILED", updated_at=failure_now))
                    failed_updates.append(
                        FileEnrichFailureUpdateDTO(
                            job_id=job.job_id,
                            error_message=vector_error_message,
                            now_iso=failure_now,
                            dead_threshold=self._policy.retry_max_attempts,
                            backoff_base_sec=self._policy.retry_backoff_base_sec,
                        )
                    )
                    finished_status = "FAILED"
                    continue
                if decoded.decode_warning is not None:
                    self._error_policy.record_error_event(
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
                skip_reason = self._resolve_l3_skip_reason(job=job)
                if skip_reason is not None:
                    readiness_updates.append(self._build_l3_skipped_readiness(job=job, reason=skip_reason, now_iso=now_iso))
                    state_updates.append(
                        EnrichStateUpdateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enrich_state="L3_SKIPPED",
                            updated_at=now_iso,
                        )
                    )
                    done_ids.append(job.job_id)
                    finished_status = "DONE"
                    continue
                extract_started_at = time.perf_counter()
                extraction = self._lsp_backend.extract(job.repo_root, job.relative_path, job.content_hash)
                extract_elapsed_ms_total += (time.perf_counter() - extract_started_at) * 1000.0
                if extraction.error_message is not None:
                    self._schedule_l1_probe_after_l3_fallback(job=job)
                    failure_now = now_iso8601_utc()
                    state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="FAILED", updated_at=failure_now))
                    failed_updates.append(
                        FileEnrichFailureUpdateDTO(
                            job_id=job.job_id,
                            error_message=extraction.error_message,
                            now_iso=failure_now,
                            dead_threshold=self._policy.retry_max_attempts,
                            backoff_base_sec=self._policy.retry_backoff_base_sec,
                        )
                    )
                    self._error_policy.record_error_event(
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
                        self._flush_enrich_buffers(
                            done_ids=done_ids,
                            failed_updates=failed_updates,
                            state_updates=state_updates,
                            body_upserts=body_upserts,
                            body_deletes=body_deletes,
                            lsp_updates=lsp_updates,
                            readiness_updates=readiness_updates,
                        )
                        raise CollectionError(ErrorContext(code="ERR_LSP_EXTRACT_FAILED", message=f"LSP 추출 실패: {extraction.error_message}"))
                    continue
                lsp_updates.append(
                    LspExtractPersistDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        content_hash=job.content_hash,
                        symbols=extraction.symbols,
                        relations=extraction.relations,
                        created_at=now_iso,
                    )
                )
                tool_ready = True
                readiness_updates.append(
                    ToolReadinessStateDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        content_hash=job.content_hash,
                        list_files_ready=True,
                        read_file_ready=True,
                        search_symbol_ready=True,
                        get_callers_ready=True,
                        consistency_ready=True,
                        quality_ready=True,
                        tool_ready=tool_ready,
                        last_reason="ok",
                        updated_at=now_iso,
                    )
                )
                if not deletion_hold_enabled:
                    body_deletes.append(FileBodyDeleteTargetDTO(repo_root=job.repo_root, relative_path=job.relative_path, content_hash=job.content_hash))
                state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="TOOL_READY", updated_at=now_iso))
                done_ids.append(job.job_id)
                finished_status = "DONE"
            except (CollectionError, RuntimeError, OSError, ValueError, zlib.error) as exc:
                failure_now = now_iso8601_utc()
                state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="FAILED", updated_at=failure_now))
                failed_updates.append(
                    FileEnrichFailureUpdateDTO(
                        job_id=job.job_id,
                        error_message=f"L2/L3 처리 실패: {exc}",
                        now_iso=failure_now,
                        dead_threshold=self._policy.retry_max_attempts,
                        backoff_base_sec=self._policy.retry_backoff_base_sec,
                    )
                )
                self._error_policy.record_error_event(
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
                    self._flush_enrich_buffers(
                        done_ids=done_ids,
                        failed_updates=failed_updates,
                        state_updates=state_updates,
                        body_upserts=body_upserts,
                        body_deletes=body_deletes,
                        lsp_updates=lsp_updates,
                        readiness_updates=readiness_updates,
                    )
                    raise CollectionError(ErrorContext(code="ERR_ENRICH_JOB_FAILED", message=f"L2/L3 처리 실패: {exc}")) from exc
            finally:
                elapsed_ms = (time.perf_counter() - started_at) * 1000.0
                self._record_enrich_latency(elapsed_ms)
                if self._event_repo is not None:
                    self._event_repo.record_event(job_id=job.job_id, status=finished_status, latency_ms=int(elapsed_ms), created_at=now_iso8601_utc())
            should_flush_by_size = len(done_ids) + len(failed_updates) >= self._flush_batch_size
            should_flush_by_time = time.perf_counter() - last_flush_at >= self._flush_interval_sec
            should_flush_by_body = body_buffer_bytes >= self._flush_max_body_bytes
            if should_flush_by_size or should_flush_by_time or should_flush_by_body:
                flush_started_at = time.perf_counter()
                self._flush_enrich_buffers(
                    done_ids=done_ids,
                    failed_updates=failed_updates,
                    state_updates=state_updates,
                    body_upserts=body_upserts,
                    body_deletes=body_deletes,
                    lsp_updates=lsp_updates,
                    readiness_updates=readiness_updates,
                )
                flush_elapsed_ms_total += (time.perf_counter() - flush_started_at) * 1000.0
                flush_count += 1
                body_buffer_bytes = 0
                last_flush_at = time.perf_counter()
        flush_started_at = time.perf_counter()
        self._flush_enrich_buffers(
            done_ids=done_ids,
            failed_updates=failed_updates,
            state_updates=state_updates,
            body_upserts=body_upserts,
            body_deletes=body_deletes,
            lsp_updates=lsp_updates,
            readiness_updates=readiness_updates,
        )
        flush_elapsed_ms_total += (time.perf_counter() - flush_started_at) * 1000.0
        flush_count += 1
        return processed

    def process_enrich_jobs_l2(self, limit: int) -> int:
        """L2 전용 보강 처리."""
        batch_started_at = time.perf_counter()
        self._assert_parent_alive("enrich_worker_l2")
        jobs = self._enrich_queue_repo.acquire_pending_for_l2(limit=limit, now_iso=now_iso8601_utc())
        jobs = self._rebalance_jobs_by_language(jobs=jobs)
        processed = 0
        done_ids: list[str] = []
        failed_updates: list[FileEnrichFailureUpdateDTO] = []
        state_updates: list[EnrichStateUpdateDTO] = []
        body_upserts: list[CollectedFileBodyDTO] = []
        body_buffer_bytes = 0
        body_deletes: list[FileBodyDeleteTargetDTO] = []
        lsp_updates: list[LspExtractPersistDTO] = []
        readiness_updates: list[ToolReadinessStateDTO] = []
        last_flush_at = time.perf_counter()
        flush_count = 0
        for job in jobs:
            processed += 1
            now_iso = now_iso8601_utc()
            started_at = time.perf_counter()
            finished_status = "FAILED"
            try:
                file_row = self._file_repo.get_file(job.repo_root, job.relative_path)
                if file_row is None or file_row.is_deleted:
                    done_ids.append(job.job_id)
                    finished_status = "DONE"
                    continue
                file_path = Path(file_row.absolute_path)
                if not file_path.exists() or not file_path.is_file():
                    failure_now = now_iso8601_utc()
                    failed_updates.append(
                        FileEnrichFailureUpdateDTO(
                            job_id=job.job_id,
                            error_message="대상 파일이 존재하지 않습니다",
                            now_iso=failure_now,
                            dead_threshold=self._policy.retry_max_attempts,
                            backoff_base_sec=self._policy.retry_backoff_base_sec,
                        )
                    )
                    state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="FAILED", updated_at=failure_now))
                    continue
                raw_bytes = file_path.read_bytes()
                stat_now = file_path.stat()
                file_hash_now = job.content_hash
                if stat_now.st_mtime_ns != file_row.mtime_ns or stat_now.st_size != file_row.size_bytes:
                    file_hash_now = hashlib.sha256(raw_bytes).hexdigest()
                if file_hash_now != job.content_hash:
                    done_ids.append(job.job_id)
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
                        self._error_policy.record_error_event(
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
                    state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="FAILED", updated_at=failure_now))
                    failed_updates.append(
                        FileEnrichFailureUpdateDTO(
                            job_id=job.job_id,
                            error_message=vector_error_message,
                            now_iso=failure_now,
                            dead_threshold=self._policy.retry_max_attempts,
                            backoff_base_sec=self._policy.retry_backoff_base_sec,
                        )
                    )
                    finished_status = "FAILED"
                    continue
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
                skip_reason = self._resolve_l3_skip_reason(job=job)
                if skip_reason is None:
                    state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="BODY_READY", updated_at=now_iso))
                    self._l3_ready_queue.put(job)
                else:
                    state_updates.append(
                        EnrichStateUpdateDTO(
                            repo_root=job.repo_root,
                            relative_path=job.relative_path,
                            enrich_state="L3_SKIPPED",
                            updated_at=now_iso,
                        )
                    )
                    readiness_updates.append(self._build_l3_skipped_readiness(job=job, reason=skip_reason, now_iso=now_iso))
                    done_ids.append(job.job_id)
                finished_status = "DONE"
            except (CollectionError, RuntimeError, OSError, ValueError, zlib.error) as exc:
                failure_now = now_iso8601_utc()
                state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="FAILED", updated_at=failure_now))
                failed_updates.append(
                    FileEnrichFailureUpdateDTO(
                        job_id=job.job_id,
                        error_message=f"L2 처리 실패: {exc}",
                        now_iso=failure_now,
                        dead_threshold=self._policy.retry_max_attempts,
                        backoff_base_sec=self._policy.retry_backoff_base_sec,
                    )
                )
                self._error_policy.record_error_event(
                    component="file_collection_service",
                    phase="enrich_l2",
                    severity="critical" if self._run_mode == "dev" else "error",
                    error_code="ERR_ENRICH_L2_FAILED",
                    error_message=f"L2 처리 실패: {exc}",
                    error_type=type(exc).__name__,
                    repo_root=job.repo_root,
                    relative_path=job.relative_path,
                    job_id=job.job_id,
                    attempt_count=job.attempt_count,
                    context_data={"content_hash": job.content_hash},
                    stacktrace_text=traceback.format_exc(),
                )
                if self._run_mode == "dev":
                    self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
                    raise CollectionError(ErrorContext(code="ERR_ENRICH_L2_FAILED", message=f"L2 처리 실패: {exc}")) from exc
            finally:
                elapsed_ms = (time.perf_counter() - started_at) * 1000.0
                self._record_enrich_latency(elapsed_ms)
                if self._event_repo is not None:
                    self._event_repo.record_event(job_id=job.job_id, status=finished_status, latency_ms=int(elapsed_ms), created_at=now_iso8601_utc())
            should_flush_by_size = len(done_ids) + len(failed_updates) >= self._flush_batch_size
            should_flush_by_time = time.perf_counter() - last_flush_at >= self._flush_interval_sec
            should_flush_by_body = body_buffer_bytes >= self._flush_max_body_bytes
            if should_flush_by_size or should_flush_by_time or should_flush_by_body:
                self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
                flush_count += 1
                body_buffer_bytes = 0
                last_flush_at = time.perf_counter()
        self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
        flush_count += 1
        return processed

    def process_enrich_jobs_l3(self, limit: int) -> int:
        """L3 전용 보강 처리."""
        batch_started_at = time.perf_counter()
        self._assert_parent_alive("enrich_worker_l3")
        acquire_started_at = time.perf_counter()
        jobs = self._acquire_l3_jobs(limit=limit)
        acquire_elapsed_ms = (time.perf_counter() - acquire_started_at) * 1000.0
        rebalance_started_at = time.perf_counter()
        jobs = self._rebalance_jobs_by_language(jobs=jobs)
        rebalance_elapsed_ms = (time.perf_counter() - rebalance_started_at) * 1000.0
        processed = 0
        done_ids: list[str] = []
        failed_updates: list[FileEnrichFailureUpdateDTO] = []
        state_updates: list[EnrichStateUpdateDTO] = []
        body_upserts: list[CollectedFileBodyDTO] = []
        body_deletes: list[FileBodyDeleteTargetDTO] = []
        lsp_updates: list[LspExtractPersistDTO] = []
        readiness_updates: list[ToolReadinessStateDTO] = []
        last_flush_at = time.perf_counter()
        flush_count = 0
        group_count = 0
        grouped_jobs = self._group_jobs_by_repo_and_language(jobs=jobs)
        grouped_jobs = self._order_l3_groups_for_scheduling(groups=grouped_jobs)
        for group in grouped_jobs:
            group_count += 1
            group_started_at = time.perf_counter()
            group_language = self._resolve_lsp_language(group[0].relative_path).value if len(group) > 0 and self._resolve_lsp_language(group[0].relative_path) is not None else "unknown"
            prime_pending_hints = getattr(self._lsp_backend, "prime_l3_group_pending_hints", None)
            if callable(prime_pending_hints):
                try:
                    prime_pending_hints(group_jobs=group)
                except Exception:
                    pass
            self._set_group_bulk_mode(group=group, enabled=True)
            group_parallelism = self._resolve_l3_parallelism(group)
            try:
                with self._perf_tracer.span(
                    "process_enrich_jobs_l3.group",
                    phase="l3_group",
                    repo_root=(group[0].repo_root if len(group) > 0 else ""),
                    language=group_language,
                    group_size=len(group),
                    parallelism=group_parallelism,
                ):
                    if group_parallelism <= 1:
                        for job in group:
                            result = self._process_single_l3_job(job)
                            processed += 1
                            self._merge_l3_result(
                                result=result,
                                done_ids=done_ids,
                                failed_updates=failed_updates,
                                state_updates=state_updates,
                                body_deletes=body_deletes,
                                lsp_updates=lsp_updates,
                                readiness_updates=readiness_updates,
                            )
                            if result.dev_error is not None:
                                self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
                                raise result.dev_error
                    else:
                        futures: list[Future[_L3JobResultDTO]] = [self._l3_executor.submit(self._process_single_l3_job, job) for job in group[:group_parallelism]]
                        if len(group) > group_parallelism:
                            for job in group[group_parallelism:]:
                                futures.append(self._l3_executor.submit(self._process_single_l3_job, job))
                        future_to_job = {future: job for future, job in zip(futures, group)}
                        completed_futures: set[Future[_L3JobResultDTO]] = set()
                        try:
                            with self._perf_tracer.span(
                                "process_enrich_jobs_l3.group_future_wait",
                                phase="l3_group_wait",
                                repo_root=(group[0].repo_root if len(group) > 0 else ""),
                                language=group_language,
                                group_size=len(group),
                                parallelism=group_parallelism,
                            ):
                                for future in as_completed(futures, timeout=self._l3_group_wait_timeout_sec):
                                    completed_futures.add(future)
                                    result = future.result()
                                    processed += 1
                                    self._merge_l3_result(
                                        result=result,
                                        done_ids=done_ids,
                                        failed_updates=failed_updates,
                                        state_updates=state_updates,
                                        body_deletes=body_deletes,
                                        lsp_updates=lsp_updates,
                                        readiness_updates=readiness_updates,
                                    )
                                    if result.dev_error is not None:
                                        self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
                                        raise result.dev_error
                        except FuturesTimeoutError:
                            timeout_now = now_iso8601_utc()
                            remaining_futures = [future for future in futures if future not in completed_futures]
                            for future in remaining_futures:
                                job = future_to_job.get(future)
                                if job is None:
                                    continue
                                future.cancel()
                                timeout_result = self._build_l3_timeout_failure_result(
                                    job=job,
                                    timeout_sec=self._l3_group_wait_timeout_sec,
                                    now_iso=timeout_now,
                                    group_size=len(group),
                                )
                                processed += 1
                                self._merge_l3_result(
                                    result=timeout_result,
                                    done_ids=done_ids,
                                    failed_updates=failed_updates,
                                    state_updates=state_updates,
                                    body_deletes=body_deletes,
                                    lsp_updates=lsp_updates,
                                    readiness_updates=readiness_updates,
                                )
            finally:
                self._set_group_bulk_mode(group=group, enabled=False)
            should_flush_by_size = len(done_ids) + len(failed_updates) >= self._flush_batch_size
            should_flush_by_time = time.perf_counter() - last_flush_at >= self._flush_interval_sec
            if should_flush_by_size or should_flush_by_time:
                with self._perf_tracer.span("process_enrich_jobs_l3.flush_buffers", phase="l3_flush"):
                    self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
                flush_count += 1
                last_flush_at = time.perf_counter()
        with self._perf_tracer.span("process_enrich_jobs_l3.flush_buffers_final", phase="l3_flush"):
            self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
        flush_count += 1
        return processed

    def _build_l3_timeout_failure_result(
        self,
        *,
        job: FileEnrichJobDTO,
        timeout_sec: float,
        now_iso: str,
        group_size: int,
    ) -> _L3JobResultDTO:
        """병렬 L3 그룹 timeout으로 완료되지 않은 job을 FAILED로 전이시키는 합성 결과를 생성한다."""
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
            dead_threshold=self._policy.retry_max_attempts,
            backoff_base_sec=self._policy.retry_backoff_base_sec,
        )
        state_update = EnrichStateUpdateDTO(
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            enrich_state="FAILED",
            updated_at=now_iso,
        )
        self._error_policy.record_error_event(
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

    def process_enrich_jobs_bootstrap(self, limit: int) -> int:
        """bootstrap 모드 정책에 따라 L2/L3 비율을 조정한다."""
        self.refresh_indexing_mode()
        if self._indexing_mode == "steady":
            return self.process_enrich_jobs(limit=limit)
        _, l3_worker_count, _, _ = self._resolve_bootstrap_policy()
        processed_l2 = self.process_enrich_jobs_l2(limit=limit)
        if self._indexing_mode == "bootstrap_l2_priority":
            l3_limit = max(1, min(limit // 4, l3_worker_count * 32))
            if self._l3_ready_queue.qsize() <= l3_limit:
                return processed_l2
            processed_l3 = self.process_enrich_jobs_l3(limit=l3_limit)
            return processed_l2 + processed_l3
        processed_l3 = self.process_enrich_jobs_l3(limit=max(1, min(limit, l3_worker_count * 64)))
        return processed_l2 + processed_l3

    def compute_coverage_bps(self) -> tuple[int, int]:
        """L2/L3 커버리지를 bps 단위로 계산한다."""
        state_counts = self._file_repo.get_enrich_state_counts()
        total = int(sum(state_counts.values()))
        if total <= 0:
            return (0, 0)
        l3_skipped = int(state_counts.get("L3_SKIPPED", 0))
        l2_ready = int(state_counts.get("BODY_READY", 0)) + int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0)) + l3_skipped
        l3_ready = int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0))
        l3_total = max(0, total - l3_skipped)
        l2_bps = int(l2_ready * 10000 / total)
        l3_bps = 10000 if l3_total <= 0 else int(l3_ready * 10000 / l3_total)
        return (l2_bps, l3_bps)

    def refresh_indexing_mode(self) -> None:
        """bootstrap 전환 정책을 갱신한다."""
        bootstrap_enabled, _, bootstrap_exit_l2_bps, bootstrap_exit_max_sec = self._resolve_bootstrap_policy()
        if not bootstrap_enabled:
            self._indexing_mode = "steady"
            return
        elapsed_sec = time.monotonic() - self._bootstrap_started_at
        l2_bps, l3_bps = self.compute_coverage_bps()
        reenter_l2_bps = max(1, bootstrap_exit_l2_bps - 700)
        if elapsed_sec >= float(bootstrap_exit_max_sec):
            self._indexing_mode = "steady"
            return
        if self._indexing_mode == "steady" and l2_bps < bootstrap_exit_l2_bps:
            self._indexing_mode = "bootstrap_l2_priority"
            return
        if self._indexing_mode == "steady":
            return
        if self._indexing_mode == "bootstrap_balanced" and l2_bps < reenter_l2_bps:
            self._indexing_mode = "bootstrap_l2_priority"
            return
        if self._indexing_mode == "bootstrap_l2_priority" and l2_bps >= bootstrap_exit_l2_bps:
            self._indexing_mode = "bootstrap_balanced"
            return
        if self._indexing_mode == "bootstrap_balanced" and l3_bps >= 9990:
            self._indexing_mode = "steady"

    def _resolve_bootstrap_policy(self) -> tuple[bool, int, int, int]:
        if self._policy_repo is None:
            return (False, 1, 9500, 1800)
        policy = self._policy_repo.get_policy()
        return (
            bool(policy.bootstrap_mode_enabled),
            max(1, int(policy.bootstrap_l3_worker_count)),
            max(1, min(10000, int(policy.bootstrap_exit_min_l2_coverage_bps))),
            max(60, int(policy.bootstrap_exit_max_sec)),
        )

    def _resolve_lsp_language(self, relative_path: str) -> Language | None:
        return resolve_language_from_path(file_path=relative_path)

    def _rebalance_jobs_by_language(self, jobs: list[FileEnrichJobDTO]) -> list[FileEnrichJobDTO]:
        if len(jobs) <= 1:
            return jobs
        buckets: dict[str, deque[FileEnrichJobDTO]] = {}
        order: list[str] = []
        for job in jobs:
            language = self._resolve_lsp_language(job.relative_path)
            key = "other" if language is None else language.value
            if key not in buckets:
                buckets[key] = deque()
                order.append(key)
            buckets[key].append(job)
        rebalanced: list[FileEnrichJobDTO] = []
        while len(order) > 0:
            next_order: list[str] = []
            for key in order:
                bucket = buckets[key]
                if len(bucket) == 0:
                    continue
                rebalanced.append(bucket.popleft())
                if len(bucket) > 0:
                    next_order.append(key)
            order = next_order
        return rebalanced

    def _group_jobs_by_repo_and_language(self, jobs: list[FileEnrichJobDTO]) -> list[list[FileEnrichJobDTO]]:
        grouped: dict[tuple[str, str], list[FileEnrichJobDTO]] = {}
        ordered_keys: list[tuple[str, str]] = []
        for job in jobs:
            language = self._resolve_lsp_language(job.relative_path)
            language_key = "other" if language is None else language.value
            key = (job.repo_root, language_key)
            if key not in grouped:
                grouped[key] = []
                ordered_keys.append(key)
            grouped[key].append(job)
        return [grouped[key] for key in ordered_keys]

    def _order_l3_groups_for_scheduling(self, groups: list[list[FileEnrichJobDTO]]) -> list[list[FileEnrichJobDTO]]:
        """PR3 baseline: backend가 제공하는 lane-aware 정렬 힌트로 L3 그룹 순서를 조정한다."""
        if len(groups) <= 1:
            return groups
        sorter = getattr(self._lsp_backend, "get_l3_group_sort_key", None)
        if not callable(sorter):
            return groups
        keyed: list[tuple[tuple[object, ...], int, list[FileEnrichJobDTO]]] = []
        for idx, group in enumerate(groups):
            if len(group) == 0:
                keyed.append(((99, 99, 0.0, f"empty:{idx}"), idx, group))
                continue
            job0 = group[0]
            try:
                key = sorter(
                    repo_root=job0.repo_root,
                    sample_relative_path=job0.relative_path,
                    group_size=len(group),
                )
            except (RuntimeError, OSError, ValueError, TypeError):
                key = (9, 9, 0.0, f"{job0.repo_root}:{job0.relative_path}")
            keyed.append((tuple(key), idx, group))
        keyed.sort(key=lambda item: (item[0], item[1]))
        return [group for _key, _idx, group in keyed]

    def _resolve_l3_parallelism(self, jobs: list[FileEnrichJobDTO]) -> int:
        if len(jobs) <= 1:
            return 1
        if not self._l3_parallel_enabled:
            return 1
        language = self._resolve_lsp_language(jobs[0].relative_path)
        if language is None:
            return 1
        backend_parallelism = 1
        executor_cap = int(getattr(self, "_l3_executor_max_workers", len(jobs)))
        requested_parallelism = min(len(jobs), max(1, executor_cap))
        if requested_parallelism <= 1:
            return 1
        now = time.monotonic()
        if self._l3_backpressure_on_interactive:
            pressure_getter = getattr(self._lsp_backend, "get_interactive_pressure", None)
            if callable(pressure_getter):
                try:
                    pressure = pressure_getter()
                except (RuntimeError, OSError, ValueError, TypeError):
                    pressure = None
                if isinstance(pressure, dict):
                    pending_interactive = int(pressure.get("pending_interactive", 0))
                    timeout_count = int(pressure.get("interactive_timeout_count", 0))
                    if timeout_count > self._last_interactive_timeout_count:
                        self._l3_backpressure_until = now + self._l3_backpressure_cooldown_sec
                    self._last_interactive_timeout_count = max(self._last_interactive_timeout_count, timeout_count)
                    if pending_interactive > 0:
                        return 1
            if now < self._l3_backpressure_until:
                requested_parallelism = max(1, requested_parallelism // 2)
        getter = getattr(self._lsp_backend, "get_parallelism", None)
        batch_getter = getattr(self._lsp_backend, "get_parallelism_for_batch", None)
        if callable(batch_getter):
            try:
                backend_parallelism = int(batch_getter(jobs[0].repo_root, language, requested_parallelism))
            except (RuntimeError, OSError, ValueError, TypeError):
                backend_parallelism = 1
            return max(1, min(len(jobs), requested_parallelism, backend_parallelism))
        if callable(getter):
            try:
                backend_parallelism = int(getter(jobs[0].repo_root, language))
            except (RuntimeError, OSError, ValueError, TypeError):
                backend_parallelism = 1
        return max(1, min(len(jobs), requested_parallelism, backend_parallelism))

    def _set_group_bulk_mode(self, group: list[FileEnrichJobDTO], enabled: bool) -> None:
        """LSP 백엔드에 그룹 단위 bulk 모드를 전달한다."""
        if len(group) == 0:
            return
        language = self._resolve_lsp_language(group[0].relative_path)
        if language is None:
            return
        setter = getattr(self._lsp_backend, "set_bulk_mode", None)
        if callable(setter):
            try:
                setter(group[0].repo_root, language, enabled)
            except (RuntimeError, OSError, ValueError, TypeError):
                return

    def _process_single_l3_job(self, job: FileEnrichJobDTO) -> _L3JobResultDTO:
        started_at = time.perf_counter()
        finished_status = "FAILED"
        done_id: str | None = None
        failure_update: FileEnrichFailureUpdateDTO | None = None
        state_update: EnrichStateUpdateDTO | None = None
        body_delete: FileBodyDeleteTargetDTO | None = None
        lsp_update: LspExtractPersistDTO | None = None
        readiness_update: ToolReadinessStateDTO | None = None
        dev_error: CollectionError | None = None
        try:
            with self._perf_tracer.span("l3_job.get_file_row", phase="l3_job", repo_root=job.repo_root):
                file_row = self._file_repo.get_file(job.repo_root, job.relative_path)
            if file_row is None or file_row.is_deleted:
                done_id = job.job_id
                finished_status = "DONE"
            elif file_row.content_hash != job.content_hash:
                done_id = job.job_id
                finished_status = "DONE"
            elif self._is_recent_tool_ready(job=job):
                now_iso = now_iso8601_utc()
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
                if not self._is_deletion_hold_enabled():
                    body_delete = FileBodyDeleteTargetDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        content_hash=job.content_hash,
                    )
                done_id = job.job_id
                finished_status = "DONE"
            else:
                now_iso = now_iso8601_utc()
                with self._perf_tracer.span("l3_job.resolve_skip_reason", phase="l3_job", repo_root=job.repo_root):
                    skip_reason = self._resolve_l3_skip_reason(job=job)
                if skip_reason is not None:
                    state_update = EnrichStateUpdateDTO(
                        repo_root=job.repo_root,
                        relative_path=job.relative_path,
                        enrich_state="L3_SKIPPED",
                        updated_at=now_iso,
                    )
                    readiness_update = self._build_l3_skipped_readiness(job=job, reason=skip_reason, now_iso=now_iso)
                    done_id = job.job_id
                    finished_status = "DONE"
                else:
                    with self._perf_tracer.span(
                        "l3_job.lsp_extract",
                        phase="l3_job",
                        repo_root=job.repo_root,
                        language=(self._resolve_lsp_language(job.relative_path).value if self._resolve_lsp_language(job.relative_path) is not None else "unknown"),
                    ):
                        extraction = self._lsp_backend.extract(job.repo_root, job.relative_path, job.content_hash)
                    if extraction.error_message is not None:
                        deferred = self._try_defer_after_broker_lease_denial(
                            job=job,
                            error_message=extraction.error_message,
                        )
                        if deferred:
                            finished_status = "PENDING"
                            extraction = None  # type: ignore[assignment]
                            # defer는 실패/에러 카운트 오염 없이 queue 상태만 되돌린다.
                            pass
                        else:
                            failure_kind = _classify_l3_extract_failure_kind(extraction.error_message)
                            escalated = self._try_escalate_scope_after_l3_extract_error(
                                job=job,
                                error_message=extraction.error_message,
                            )
                            if escalated:
                                finished_status = "PENDING"
                            else:
                                self._schedule_l1_probe_after_l3_fallback(job=job)
                                failure_now = now_iso8601_utc()
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
                                self._error_policy.record_error_event(
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
                        if not self._is_deletion_hold_enabled():
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
                        self._record_scope_learning_after_l3_success(job=job)
                        done_id = job.job_id
                        finished_status = "DONE"
        except (CollectionError, RuntimeError, OSError, ValueError, zlib.error) as exc:
            failure_now = now_iso8601_utc()
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
            self._error_policy.record_error_event(
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
                stacktrace_text=traceback.format_exc(),
            )
            if self._run_mode == "dev":
                dev_error = CollectionError(ErrorContext(code="ERR_ENRICH_L3_FAILED", message=f"L3 처리 실패: {exc}"))
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        self._record_enrich_latency(elapsed_ms)
        if self._event_repo is not None:
            with self._perf_tracer.span("l3_job.record_event", phase="l3_job_event"):
                self._event_repo.record_event(
                    job_id=job.job_id,
                    status=finished_status,
                    latency_ms=int(elapsed_ms),
                    created_at=now_iso8601_utc(),
                )
        return _L3JobResultDTO(
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

    def _schedule_l1_probe_after_l3_fallback(self, job: FileEnrichJobDTO) -> None:
        """L3 fail-open 시 백그라운드 L1 probe를 조건부로 예약한다."""
        language = resolve_language_from_path(file_path=job.relative_path)
        if language is None or language not in self._lsp_probe_l1_languages:
            return
        inflight_checker = getattr(self._lsp_backend, "is_probe_inflight_for_file", None)
        if callable(inflight_checker):
            try:
                if bool(inflight_checker(repo_root=job.repo_root, relative_path=job.relative_path)):
                    return
            except (RuntimeError, OSError, ValueError, TypeError):
                return
        scheduler = getattr(self._lsp_backend, "schedule_probe_for_file", None)
        if not callable(scheduler):
            return
        try:
            scheduler(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                force=False,
                trigger="l3_fallback",
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            return

    def _try_escalate_scope_after_l3_extract_error(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        """L3 extract 실패가 scope 문제라면 same-row scope escalation을 시도한다."""
        queue_repo = self._enrich_queue_repo
        escalator = getattr(queue_repo, "escalate_scope_on_same_job", None)
        if not callable(escalator):
            return False
        error_code = _extract_error_code_from_lsp_error_message(error_message)
        if not _is_scope_escalation_trigger_error_for_l3(code=error_code, message=error_message):
            return False
        current_attempts = max(0, int(getattr(job, "scope_attempts", 0)))
        if current_attempts >= 2:
            return False
        next_scope_level = _next_scope_level_for_l3_escalation(getattr(job, "scope_level", None))
        if next_scope_level is None:
            return False
        next_scope_root = self._resolve_next_scope_root_for_escalation(job=job, next_scope_level=next_scope_level)
        now_iso = now_iso8601_utc()
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

    def _try_defer_after_broker_lease_denial(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        """broker lease 거부 오류는 실패가 아니라 queue defer로 되돌린다."""
        if "ERR_LSP_BROKER_LEASE_REQUIRED" not in error_message:
            return False
        defer_writer = getattr(self._enrich_queue_repo, "defer_jobs_to_pending", None)
        if not callable(defer_writer):
            return False
        now_dt = datetime.now(timezone.utc)
        lease_reason = _extract_broker_lease_reason_from_l3_error(error_message)
        defer_reason = _map_broker_lease_reason_to_defer_reason(lease_reason)
        defer_delay_sec = _broker_defer_delay_seconds_for_reason(lease_reason)
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

    def _resolve_next_scope_root_for_escalation(self, *, job: FileEnrichJobDTO, next_scope_level: str) -> str:
        """PR-B baseline scope root fallback 계산 (실제 planner 연계는 PR1에서 강화)."""
        if next_scope_level == "workspace":
            return job.repo_root
        if next_scope_level == "repo":
            parts = Path(job.relative_path).parts
            if len(parts) >= 2 and parts[0] not in ("", ".", ".."):
                return str(Path(job.repo_root) / parts[0])
        return job.repo_root

    def _record_scope_learning_after_l3_success(self, *, job: FileEnrichJobDTO) -> None:
        """성공한 scope 시도를 backend 학습 캐시에 기록한다 (Phase1 baseline)."""
        recorder = getattr(self._lsp_backend, "record_scope_override_success", None)
        if not callable(recorder):
            return
        scope_level = (getattr(job, "scope_level", None) or "module").strip().lower()
        scope_root = getattr(job, "scope_root", None) or job.repo_root
        try:
            recorder(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                scope_root=scope_root,
                scope_level=scope_level,
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            return

    def _should_perf_trace_tick(self) -> bool:
        """테스트용 래퍼: 트레이스 샘플링 틱을 반환한다."""
        return self._perf_tracer.should_sample()

    def _perf_trace(self, event: str, **fields: object) -> None:
        """테스트용 래퍼: 성능 트레이스 로그를 남긴다."""
        self._perf_tracer.emit(event, **fields)

    def _parse_lsp_probe_l1_languages(self, items: tuple[str, ...]) -> set[Language]:
        """lsp_probe_l1_languages 설정을 Language 집합으로 변환한다."""
        parsed: set[Language] = set()
        for item in items:
            raw = item.strip().lower()
            if raw == "":
                continue
            language = resolve_language_from_path(file_path=f"file.{raw}")
            if language is not None:
                parsed.add(language)
        return parsed

    def _parse_l3_supported_languages(self, items: tuple[str, ...]) -> set[Language]:
        """l3_supported_languages 설정을 Language 집합으로 변환한다."""
        parsed: set[Language] = set()
        aliases = {
            "py": Language.PYTHON,
            "js": Language.TYPESCRIPT,
            "ts": Language.TYPESCRIPT,
            "kt": Language.KOTLIN,
            "rs": Language.RUST,
            "cs": Language.CSHARP,
            "rb": Language.RUBY,
        }
        for item in items:
            raw = item.strip().lower()
            if raw == "":
                continue
            if raw in aliases:
                parsed.add(aliases[raw])
                continue
            try:
                parsed.add(Language(raw))
                continue
            except ValueError:
                pass
            language = resolve_language_from_path(file_path=f"file.{raw}")
            if language is not None:
                parsed.add(language)
        if len(parsed) > 0:
            return parsed
        # 잘못된 설정으로 전체가 비활성화되지 않도록 기본값으로 복구한다.
        return {Language(name) for name in get_enabled_language_names()}

    def _resolve_l3_skip_reason(self, job: FileEnrichJobDTO) -> str | None:
        """job이 L3 추출을 건너뛰어야 하는 사유를 반환한다."""
        language = resolve_language_from_path(file_path=job.relative_path)
        if language is None:
            return "skip_unsupported_extension"
        if language not in self._l3_supported_languages:
            return "skip_unsupported_language"
        checker = getattr(self._lsp_backend, "is_l3_permanently_unavailable_for_file", None)
        if callable(checker):
            try:
                if bool(checker(repo_root=job.repo_root, relative_path=job.relative_path)):
                    return "skip_probe_unavailable"
            except (RuntimeError, OSError, ValueError, TypeError):
                return None
        return None

    def _build_l3_skipped_readiness(
        self,
        *,
        job: FileEnrichJobDTO,
        reason: str,
        now_iso: str,
    ) -> ToolReadinessStateDTO:
        """L3 스킵 상태의 readiness 레코드를 생성한다."""
        return ToolReadinessStateDTO(
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            content_hash=job.content_hash,
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=False,
            get_callers_ready=False,
            consistency_ready=False,
            quality_ready=False,
            tool_ready=False,
            last_reason=reason,
            updated_at=now_iso,
        )

    def _is_recent_tool_ready(self, job: FileEnrichJobDTO) -> bool:
        """최근 성공 상태면 L3 재추출을 건너뛸지 판단한다."""
        if self._l3_recent_success_ttl_sec <= 0:
            return False
        state = self._readiness_repo.get_state(job.repo_root, job.relative_path)
        if state is None:
            return False
        if not state.tool_ready:
            return False
        if state.content_hash != job.content_hash:
            return False
        try:
            updated_at = datetime.fromisoformat(state.updated_at)
        except ValueError:
            return False
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_sec = (datetime.now(timezone.utc) - updated_at).total_seconds()
        return age_sec <= float(self._l3_recent_success_ttl_sec)

    def _merge_l3_result(
        self,
        *,
        result: _L3JobResultDTO,
        done_ids: list[str],
        failed_updates: list[FileEnrichFailureUpdateDTO],
        state_updates: list[EnrichStateUpdateDTO],
        body_deletes: list[FileBodyDeleteTargetDTO],
        lsp_updates: list[LspExtractPersistDTO],
        readiness_updates: list[ToolReadinessStateDTO],
    ) -> None:
        if result.done_id is not None:
            done_ids.append(result.done_id)
        if result.failure_update is not None:
            failed_updates.append(result.failure_update)
        if result.state_update is not None:
            state_updates.append(result.state_update)
        if result.body_delete is not None:
            body_deletes.append(result.body_delete)
        if result.lsp_update is not None:
            lsp_updates.append(result.lsp_update)
        if result.readiness_update is not None:
            readiness_updates.append(result.readiness_update)

    def _acquire_l3_jobs(self, limit: int) -> list[FileEnrichJobDTO]:
        jobs: list[FileEnrichJobDTO] = []
        while len(jobs) < limit:
            try:
                jobs.append(self._l3_ready_queue.get_nowait())
            except queue.Empty:
                break
        if len(jobs) < limit:
            now_iso = now_iso8601_utc()
            jobs.extend(self._enrich_queue_repo.acquire_pending_for_l3(limit=limit - len(jobs), now_iso=now_iso))
        return jobs

    def _is_deletion_hold_enabled(self) -> bool:
        if self._policy_repo is None:
            return False
        return bool(self._policy_repo.get_policy().deletion_hold)

    def _flush_enrich_buffers(
        self,
        *,
        done_ids: list[str],
        failed_updates: list[FileEnrichFailureUpdateDTO],
        state_updates: list[EnrichStateUpdateDTO],
        body_upserts: list[CollectedFileBodyDTO],
        body_deletes: list[FileBodyDeleteTargetDTO],
        lsp_updates: list[LspExtractPersistDTO],
        readiness_updates: list[ToolReadinessStateDTO],
    ) -> None:
        if len(body_upserts) > 0:
            self._body_repo.upsert_body_many(body_upserts)
            body_upserts.clear()
        if len(lsp_updates) > 0:
            self._lsp_repo.replace_file_data_many(lsp_updates)
            lsp_updates.clear()
        if len(readiness_updates) > 0:
            self._readiness_repo.upsert_state_many(readiness_updates)
            readiness_updates.clear()
        if len(body_deletes) > 0:
            self._body_repo.delete_body_many(body_deletes)
            body_deletes.clear()
        if len(state_updates) > 0:
            self._file_repo.update_enrich_state_many(state_updates)
            state_updates.clear()
        if len(done_ids) > 0:
            self._enrich_queue_repo.mark_done_many(done_ids)
            done_ids.clear()
        if len(failed_updates) > 0:
            self._enrich_queue_repo.mark_failed_with_backoff_many(failed_updates)
            failed_updates.clear()


def _extract_error_code_from_lsp_error_message(message: str) -> str:
    """LSP 에러 메시지에서 에러 코드를 추출한다 (prefix 우선)."""
    trimmed = message.strip()
    if trimmed.startswith("ERR_"):
        return trimmed.split(":", 1)[0].strip()
    lowered = trimmed.lower()
    if "workspace contains" in lowered and "no " in lowered and "contains" in lowered:
        return "ERR_LSP_WORKSPACE_MISMATCH"
    if "project model missing" in lowered:
        return "ERR_CONFIG_INVALID"
    if "project not found" in lowered or "no workspace contains" in lowered:
        return "ERR_LSP_DOCUMENT_SYMBOL_FAILED"
    return "ERR_LSP_EXTRACT_FAILED"


def _is_scope_escalation_trigger_error_for_l3(*, code: str, message: str) -> bool:
    """Phase1 baseline taxonomy에 해당하는 L3 extract 오류만 escalation trigger로 본다."""
    normalized_code = code.strip().upper()
    lowered = message.strip().lower()
    if normalized_code == "ERR_LSP_WORKSPACE_MISMATCH":
        return True
    if normalized_code == "ERR_CONFIG_INVALID":
        return True
    if normalized_code == "ERR_LSP_DOCUMENT_SYMBOL_FAILED":
        project_missing_patterns = (
            "no workspace contains",
            "project not found",
            "project model missing",
            "workspace contains",
        )
        return any(pattern in lowered for pattern in project_missing_patterns)
    return False


def _next_scope_level_for_l3_escalation(current_scope_level: str | None) -> str | None:
    """module -> repo -> workspace 순으로 다음 escalation 단계를 반환한다."""
    level = (current_scope_level or "module").strip().lower()
    if level == "module":
        return "repo"
    if level == "repo":
        return "workspace"
    return None


def _classify_l3_extract_failure_kind(message: str) -> str:
    """L3 extract 오류를 Phase1 3종 분류로 정규화한다."""
    code = _extract_error_code_from_lsp_error_message(message)
    if code in {
        "ERR_LSP_SERVER_MISSING",
        "ERR_LSP_SERVER_SPAWN_FAILED",
        "ERR_RUNTIME_MISMATCH",
        "ERR_CONFIG_INVALID",
        "ERR_LSP_WORKSPACE_MISMATCH",
    }:
        return "PERMANENT_UNAVAILABLE"
    if code in {
        "ERR_RPC_TIMEOUT",
        "ERR_BROKEN_PIPE",
        "ERR_SERVER_EXITED",
        "ERR_LSP_START_TIMEOUT",
        "ERR_LSP_DOCUMENT_SYMBOL_FAILED",
        "ERR_LSP_EXTRACT_FAILED",
    }:
        return "TRANSIENT_FAIL"
    return "TRANSIENT_FAIL"


def _extract_broker_lease_reason_from_l3_error(message: str) -> str:
    """ERR_LSP_BROKER_LEASE_REQUIRED 메시지에서 lease reason을 추출한다."""
    lowered = message.strip()
    marker = "reason="
    idx = lowered.find(marker)
    if idx < 0:
        return "budget_blocked"
    value = lowered[idx + len(marker) :].strip()
    if "," in value:
        value = value.split(",", 1)[0].strip()
    return value or "budget_blocked"


def _map_broker_lease_reason_to_defer_reason(lease_reason: str) -> str:
    """broker lease 거부 이유를 Phase1 defer_reason prefix로 정규화한다."""
    reason = lease_reason.strip().lower()
    if reason in {"cooldown", "min_lease"}:
        return "broker_defer:cooldown"
    if reason == "starvation_guard":
        return "broker_defer:starvation_guard"
    return "broker_defer:budget"


def _broker_defer_delay_seconds_for_reason(lease_reason: str) -> float:
    """broker defer reason별 기본 재평가 지연값 (Phase1 baseline)."""
    reason = lease_reason.strip().lower()
    if reason in {"cooldown", "min_lease"}:
        return 1.0
    if reason == "starvation_guard":
        return 0.2
    return 0.5
