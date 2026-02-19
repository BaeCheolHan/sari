"""파일 스캔 전용 컴포넌트."""

from __future__ import annotations

import hashlib
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pathspec import PathSpec
from solidlsp.ls_config import Language

from sari.core.exceptions import CollectionError, ErrorContext
from sari.core.models import CandidateIndexChangeDTO, CollectionScanResultDTO, CollectedFileL1DTO, EnqueueRequestDTO, RepoIdentityDTO, now_iso8601_utc


@dataclass(frozen=True)
class _ScanHashJobDTO:
    repo_id: str
    repo_root: str
    relative_path: str
    absolute_path: str
    repo_label: str
    mtime_ns: int
    size_bytes: int
    observed_at: str


@dataclass(frozen=True)
class _ScanHashResultDTO:
    repo_id: str
    repo_root: str
    relative_path: str
    absolute_path: str
    repo_label: str
    mtime_ns: int
    size_bytes: int
    content_hash: str
    observed_at: str


class FileScanner:
    """L1 스캔 책임을 담당하는 전용 서비스."""

    def __init__(
        self,
        *,
        file_repo: object,
        enrich_queue_repo: object,
        candidate_index_sink: object | None,
        resolve_lsp_language: Callable[[str], Language | None],
        configure_lsp_prewarm_languages: Callable[[str, dict[Language, int]], None],
        resolve_repo_identity: Callable[[str], RepoIdentityDTO],
        load_gitignore_spec: Callable[[Path], PathSpec],
        is_collectible: Callable[[Path, Path, PathSpec], bool],
        priority_low: int,
        priority_medium: int,
        scan_flush_batch_size: int,
        scan_flush_interval_sec: float,
        scan_hash_max_workers: int,
    ) -> None:
        """필요 의존성만 주입받는다."""
        self._file_repo = file_repo
        self._enrich_queue_repo = enrich_queue_repo
        self._candidate_index_sink = candidate_index_sink
        self._resolve_lsp_language = resolve_lsp_language
        self._configure_lsp_prewarm_languages = configure_lsp_prewarm_languages
        self._resolve_repo_identity = resolve_repo_identity
        self._load_gitignore_spec = load_gitignore_spec
        self._is_collectible = is_collectible
        self._priority_low = priority_low
        self._priority_medium = priority_medium
        self._scan_flush_batch_size = scan_flush_batch_size
        self._scan_flush_interval_sec = scan_flush_interval_sec
        self._scan_hash_max_workers = scan_hash_max_workers

    def scan_once(self, repo_root: str) -> CollectionScanResultDTO:
        """단일 저장소 스캔을 실행한다."""
        root = Path(repo_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise CollectionError(ErrorContext(code="ERR_REPO_NOT_FOUND", message="repo 경로를 찾을 수 없습니다"))
        gitignore_spec = self._load_gitignore_spec(root)
        scan_started_at = now_iso8601_utc()
        now_iso = scan_started_at
        repo_identity = self._resolve_repo_identity(str(root))
        repo_label = repo_identity.repo_label
        repo_id = repo_identity.repo_id
        self._file_repo.sync_repo_identity(repo_root=str(root), repo_label=repo_label, repo_id=repo_id)
        seen_paths: list[str] = []
        scanned_count = 0
        indexed_count = 0
        l1_rows: list[CollectedFileL1DTO] = []
        enqueue_requests: list[EnqueueRequestDTO] = []
        candidate_changes: list[CandidateIndexChangeDTO] = []
        hash_jobs: list[_ScanHashJobDTO] = []
        language_counts: dict[Language, int] = defaultdict(int)
        last_flush_at = time.perf_counter()
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if not self._is_collectible(file_path=file_path, repo_root=root, gitignore_spec=gitignore_spec):
                continue
            scanned_count += 1
            resolved_language = self._resolve_lsp_language(relative_path=str(file_path.relative_to(root).as_posix()))
            if resolved_language is not None:
                language_counts[resolved_language] += 1
            relative_path = str(file_path.relative_to(root).as_posix())
            seen_paths.append(relative_path)
            stat = file_path.stat()
            existing = self._file_repo.get_file(str(root), relative_path)
            if (
                existing is not None
                and (not existing.is_deleted)
                and (existing.mtime_ns == stat.st_mtime_ns)
                and (existing.size_bytes == stat.st_size)
            ):
                l1_rows.append(
                    CollectedFileL1DTO(
                        repo_id=repo_id,
                        repo_root=str(root),
                        relative_path=relative_path,
                        absolute_path=str(file_path.resolve()),
                        repo_label=repo_label,
                        mtime_ns=stat.st_mtime_ns,
                        size_bytes=stat.st_size,
                        content_hash=existing.content_hash,
                        is_deleted=False,
                        last_seen_at=now_iso,
                        updated_at=now_iso,
                        enrich_state=existing.enrich_state,
                    )
                )
                should_flush_by_size = len(l1_rows) >= self._scan_flush_batch_size
                should_flush_by_time = time.perf_counter() - last_flush_at >= self._scan_flush_interval_sec
                if should_flush_by_size or should_flush_by_time:
                    self._flush_scan_buffers(l1_rows=l1_rows, enqueue_requests=enqueue_requests, candidate_changes=candidate_changes)
                    last_flush_at = time.perf_counter()
                continue
            hash_jobs.append(
                _ScanHashJobDTO(
                    repo_id=repo_id,
                    repo_root=str(root),
                    relative_path=relative_path,
                    absolute_path=str(file_path.resolve()),
                    repo_label=repo_label,
                    mtime_ns=stat.st_mtime_ns,
                    size_bytes=stat.st_size,
                    observed_at=now_iso,
                )
            )
        for hash_result in self._hash_scan_jobs_parallel(hash_jobs):
            indexed_count += 1
            l1_rows.append(
                CollectedFileL1DTO(
                    repo_id=hash_result.repo_id,
                    repo_root=hash_result.repo_root,
                    relative_path=hash_result.relative_path,
                    absolute_path=hash_result.absolute_path,
                    repo_label=hash_result.repo_label,
                    mtime_ns=hash_result.mtime_ns,
                    size_bytes=hash_result.size_bytes,
                    content_hash=hash_result.content_hash,
                    is_deleted=False,
                    last_seen_at=hash_result.observed_at,
                    updated_at=hash_result.observed_at,
                    enrich_state="PENDING",
                )
            )
            enqueue_requests.append(
                EnqueueRequestDTO(
                    repo_id=hash_result.repo_id,
                    repo_root=hash_result.repo_root,
                    relative_path=hash_result.relative_path,
                    content_hash=hash_result.content_hash,
                    priority=self._priority_low,
                    enqueue_source="scan",
                    now_iso=hash_result.observed_at,
                )
            )
            if self._candidate_index_sink is not None:
                candidate_changes.append(
                    CandidateIndexChangeDTO(
                        repo_id=hash_result.repo_id,
                        repo_root=hash_result.repo_root,
                        relative_path=hash_result.relative_path,
                        absolute_path=hash_result.absolute_path,
                        content_hash=hash_result.content_hash,
                        mtime_ns=hash_result.mtime_ns,
                        size_bytes=hash_result.size_bytes,
                        event_source="scan",
                        recorded_at=hash_result.observed_at,
                    )
                )
            should_flush_by_size = len(l1_rows) >= self._scan_flush_batch_size
            should_flush_by_time = time.perf_counter() - last_flush_at >= self._scan_flush_interval_sec
            if should_flush_by_size or should_flush_by_time:
                self._flush_scan_buffers(l1_rows=l1_rows, enqueue_requests=enqueue_requests, candidate_changes=candidate_changes)
                last_flush_at = time.perf_counter()
        self._flush_scan_buffers(l1_rows=l1_rows, enqueue_requests=enqueue_requests, candidate_changes=candidate_changes)
        self._configure_lsp_prewarm_languages(repo_root=str(root), language_counts=language_counts)
        deleted_count = self._file_repo.mark_missing_as_deleted(str(root), seen_paths, now_iso, scan_started_at=scan_started_at)
        if self._candidate_index_sink is not None and deleted_count > 0:
            self._candidate_index_sink.mark_repo_dirty(str(root))
        return CollectionScanResultDTO(scanned_count=scanned_count, indexed_count=indexed_count, deleted_count=deleted_count)

    def index_file(self, repo_root: str, relative_path: str) -> CollectionScanResultDTO:
        """단일 파일 강제 인덱싱을 실행한다."""
        if relative_path.strip() == "":
            raise CollectionError(ErrorContext(code="ERR_RELATIVE_PATH_REQUIRED", message="relative_path는 필수입니다"))
        root = Path(repo_root).expanduser().resolve()
        file_path = (root / relative_path).resolve()
        if not file_path.exists() or not file_path.is_file():
            raise CollectionError(ErrorContext(code="ERR_FILE_NOT_FOUND", message="대상 파일을 찾을 수 없습니다"))
        gitignore_spec = self._load_gitignore_spec(root)
        if not self._is_collectible(file_path=file_path, repo_root=root, gitignore_spec=gitignore_spec):
            raise CollectionError(ErrorContext(code="ERR_FILE_NOT_COLLECTIBLE", message="수집 정책 대상 파일이 아닙니다"))
        now_iso = now_iso8601_utc()
        repo_identity = self._resolve_repo_identity(str(root))
        repo_label = repo_identity.repo_label
        repo_id = repo_identity.repo_id
        content_bytes = file_path.read_bytes()
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        l1_row = CollectedFileL1DTO(
            repo_id=repo_id,
            repo_root=str(root),
            relative_path=str(file_path.relative_to(root).as_posix()),
            absolute_path=str(file_path),
            repo_label=repo_label,
            mtime_ns=file_path.stat().st_mtime_ns,
            size_bytes=file_path.stat().st_size,
            content_hash=content_hash,
            is_deleted=False,
            last_seen_at=now_iso,
            updated_at=now_iso,
            enrich_state="PENDING",
        )
        self._file_repo.upsert_file(l1_row)
        self._enrich_queue_repo.enqueue(
            repo_id=repo_id,
            repo_root=str(root),
            relative_path=str(file_path.relative_to(root).as_posix()),
            content_hash=content_hash,
            priority=self._priority_medium,
            enqueue_source="manual",
            now_iso=now_iso,
        )
        if self._candidate_index_sink is not None:
            self._candidate_index_sink.record_upsert(
                CandidateIndexChangeDTO(
                    repo_id=repo_id,
                    repo_root=str(root),
                    relative_path=str(file_path.relative_to(root).as_posix()),
                    absolute_path=str(file_path),
                    content_hash=content_hash,
                    mtime_ns=file_path.stat().st_mtime_ns,
                    size_bytes=file_path.stat().st_size,
                    event_source="manual",
                    recorded_at=now_iso,
                )
            )
        return CollectionScanResultDTO(scanned_count=1, indexed_count=1, deleted_count=0)

    def _hash_scan_jobs_parallel(self, jobs: list[_ScanHashJobDTO]) -> list[_ScanHashResultDTO]:
        """해시 계산 대상을 병렬 처리한다."""
        if len(jobs) == 0:
            return []
        worker_count = min(self._scan_hash_max_workers, max(1, os.cpu_count() or 1), len(jobs))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="scan-hash") as executor:
            return list(executor.map(self._compute_scan_hash, jobs))

    def _compute_scan_hash(self, job: _ScanHashJobDTO) -> _ScanHashResultDTO:
        """단일 파일 해시를 계산한다."""
        try:
            raw_bytes = Path(job.absolute_path).read_bytes()
        except OSError as exc:
            raise CollectionError(ErrorContext(code="ERR_SCAN_READ_FAILED", message=f"scan read failed: {job.relative_path}: {exc}")) from exc
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        return _ScanHashResultDTO(
            repo_id=job.repo_id,
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            absolute_path=job.absolute_path,
            repo_label=job.repo_label,
            mtime_ns=job.mtime_ns,
            size_bytes=job.size_bytes,
            content_hash=content_hash,
            observed_at=job.observed_at,
        )

    def _flush_scan_buffers(
        self,
        l1_rows: list[CollectedFileL1DTO],
        enqueue_requests: list[EnqueueRequestDTO],
        candidate_changes: list[CandidateIndexChangeDTO],
    ) -> None:
        """스캔 버퍼를 저장소에 반영한다."""
        if len(l1_rows) > 0:
            self._file_repo.upsert_files_many(l1_rows)
            l1_rows.clear()
        if len(enqueue_requests) > 0:
            self._enrich_queue_repo.enqueue_many(enqueue_requests)
            enqueue_requests.clear()
        if self._candidate_index_sink is not None and len(candidate_changes) > 0:
            for change in candidate_changes:
                self._candidate_index_sink.record_upsert(change)
            candidate_changes.clear()
