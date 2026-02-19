"""L2/L3 보강 파이프라인 전용 엔진."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import queue
import time
import traceback
import zlib
from collections import deque
from pathlib import Path
from typing import Callable

from solidlsp.ls_config import Language

from sari.core.exceptions import CollectionError, ErrorContext
from sari.core.language_registry import resolve_language_from_path
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
        self._l3_executor = ThreadPoolExecutor(max_workers=self._l3_executor_max_workers, thread_name_prefix="enrich-l3")
        self._l3_executor_closed = False
        self._indexing_mode = "steady"
        self._bootstrap_started_at = time.monotonic()

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
                extraction = self._lsp_backend.extract(job.repo_root, job.relative_path, job.content_hash)
                if extraction.error_message is not None:
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
                self._flush_enrich_buffers(
                    done_ids=done_ids,
                    failed_updates=failed_updates,
                    state_updates=state_updates,
                    body_upserts=body_upserts,
                    body_deletes=body_deletes,
                    lsp_updates=lsp_updates,
                    readiness_updates=readiness_updates,
                )
                body_buffer_bytes = 0
                last_flush_at = time.perf_counter()
        self._flush_enrich_buffers(
            done_ids=done_ids,
            failed_updates=failed_updates,
            state_updates=state_updates,
            body_upserts=body_upserts,
            body_deletes=body_deletes,
            lsp_updates=lsp_updates,
            readiness_updates=readiness_updates,
        )
        return processed

    def process_enrich_jobs_l2(self, limit: int) -> int:
        """L2 전용 보강 처리."""
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
                state_updates.append(EnrichStateUpdateDTO(repo_root=job.repo_root, relative_path=job.relative_path, enrich_state="BODY_READY", updated_at=now_iso))
                self._l3_ready_queue.put(job)
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
                body_buffer_bytes = 0
                last_flush_at = time.perf_counter()
        self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
        return processed

    def process_enrich_jobs_l3(self, limit: int) -> int:
        """L3 전용 보강 처리."""
        self._assert_parent_alive("enrich_worker_l3")
        jobs = self._acquire_l3_jobs(limit=limit)
        jobs = self._rebalance_jobs_by_language(jobs=jobs)
        processed = 0
        done_ids: list[str] = []
        failed_updates: list[FileEnrichFailureUpdateDTO] = []
        state_updates: list[EnrichStateUpdateDTO] = []
        body_upserts: list[CollectedFileBodyDTO] = []
        body_deletes: list[FileBodyDeleteTargetDTO] = []
        lsp_updates: list[LspExtractPersistDTO] = []
        readiness_updates: list[ToolReadinessStateDTO] = []
        last_flush_at = time.perf_counter()
        grouped_jobs = self._group_jobs_by_repo_and_language(jobs=jobs)
        for group in grouped_jobs:
            self._set_group_bulk_mode(group=group, enabled=True)
            group_parallelism = self._resolve_l3_parallelism(group)
            try:
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
                    for future in as_completed(futures):
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
            finally:
                self._set_group_bulk_mode(group=group, enabled=False)
            should_flush_by_size = len(done_ids) + len(failed_updates) >= self._flush_batch_size
            should_flush_by_time = time.perf_counter() - last_flush_at >= self._flush_interval_sec
            if should_flush_by_size or should_flush_by_time:
                self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
                last_flush_at = time.perf_counter()
        self._flush_enrich_buffers(done_ids=done_ids, failed_updates=failed_updates, state_updates=state_updates, body_upserts=body_upserts, body_deletes=body_deletes, lsp_updates=lsp_updates, readiness_updates=readiness_updates)
        return processed

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
        l2_ready = int(state_counts.get("BODY_READY", 0)) + int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0))
        l3_ready = int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0))
        return (int(l2_ready * 10000 / total), int(l3_ready * 10000 / total))

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
            file_row = self._file_repo.get_file(job.repo_root, job.relative_path)
            if file_row is None or file_row.is_deleted:
                done_id = job.job_id
                finished_status = "DONE"
            elif file_row.content_hash != job.content_hash:
                done_id = job.job_id
                finished_status = "DONE"
            else:
                now_iso = now_iso8601_utc()
                extraction = self._lsp_backend.extract(job.repo_root, job.relative_path, job.content_hash)
                if extraction.error_message is not None:
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
                        context_data={"content_hash": job.content_hash},
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
