"""파이프라인 벤치마크 서비스를 구현한다."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from sari.core.exceptions import BenchmarkError, CollectionError, ErrorContext
from sari.core.language_registry import (
    get_default_collection_extensions,
    normalize_language_filter,
    resolve_language_from_path,
)
from sari.core.models import CollectionPolicyDTO, now_iso8601_utc
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_benchmark_repository import PipelineBenchmarkRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.services.collection.ports import CollectionPipelinePort, CollectionScanPort
from sari.services.file_collection_service import LspExtractionBackend, LspExtractionResultDTO


class BenchmarkLspExtractionBackend(LspExtractionBackend):
    """벤치마크용 경량 LSP 추출 백엔드다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        """파일명 기반 더미 심볼을 반환한다."""
        del repo_root, content_hash
        symbol_name = Path(relative_path).stem
        return LspExtractionResultDTO(
            symbols=[
                {
                    "name": f"bench_{symbol_name}",
                    "kind": "function",
                    "line": 0,
                    "end_line": 0,
                }
            ],
            relations=[],
            error_message=None,
        )


class PipelineBenchmarkService:
    """50k 기준 파이프라인 성능 벤치마크를 수행한다."""

    def __init__(
        self,
        file_collection_service: CollectionScanPort | CollectionPipelinePort,
        queue_repo: FileEnrichQueueRepository,
        lsp_repo: LspToolDataRepository,
        policy_repo: PipelinePolicyRepository,
        benchmark_repo: PipelineBenchmarkRepository,
        artifact_root: Path,
    ) -> None:
        """벤치마크 실행 의존성을 주입한다."""
        self._file_collection_service = file_collection_service
        self._queue_repo = queue_repo
        self._lsp_repo = lsp_repo
        self._policy_repo = policy_repo
        self._benchmark_repo = benchmark_repo
        self._artifact_root = artifact_root

    @staticmethod
    def default_collection_policy() -> CollectionPolicyDTO:
        """벤치마크 기본 수집 정책을 반환한다."""
        return CollectionPolicyDTO(
            include_ext=get_default_collection_extensions(),
            exclude_globs=("**/.git/**", "**/node_modules/**", "**/dist/**", "**/build/**"),
            max_file_size_bytes=512 * 1024,
            scan_interval_sec=180,
            max_enrich_batch=200,
            retry_max_attempts=2,
            retry_backoff_base_sec=1,
            queue_poll_interval_ms=100,
        )

    def run(
        self,
        repo_root: str,
        target_files: int,
        profile: str,
        language_filter: tuple[str, ...] | None = None,
        per_language_report: bool = False,
    ) -> dict[str, object]:
        """벤치마크를 실행하고 요약 결과를 반환한다."""
        root = Path(repo_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise BenchmarkError(ErrorContext(code="ERR_REPO_NOT_FOUND", message="repo 경로를 찾을 수 없습니다"))
        if target_files <= 0:
            raise BenchmarkError(ErrorContext(code="ERR_INVALID_TARGET_FILES", message="target_files는 1 이상이어야 합니다"))
        try:
            normalized_filter = normalize_language_filter(language_filter)
        except ValueError as exc:
            raise BenchmarkError(ErrorContext(code="ERR_INVALID_LANGUAGE_FILTER", message=str(exc))) from exc
        started_at = now_iso8601_utc()
        run_id = self._benchmark_repo.create_run(
            repo_root=str(root),
            target_files=target_files,
            profile=profile,
            started_at=started_at,
        )
        try:
            self._prepare_dataset(root=root, target_files=target_files)
            ingest_started = time.perf_counter()
            scan_result = self._file_collection_service.scan_once(repo_root=str(root))
            ingest_elapsed_ms = int((time.perf_counter() - ingest_started) * 1000.0)

            enrich_started = time.perf_counter()
            self._drain_enrich_queue(max_wait_sec=120.0)
            enrich_elapsed_sec = float(time.perf_counter() - enrich_started)
            counts = self._queue_repo.get_status_counts()
            done_count = int(counts.get("DONE", 0))
            dead_count = int(counts.get("DEAD", 0))
            denominator = done_count + dead_count
            dead_ratio_bps = int((dead_count * 10_000) / denominator) if denominator > 0 else 0

            search_latencies_ms = self._measure_search_latencies(repo_root=str(root))
            search_p95 = _percentile_95(search_latencies_ms)
            per_language = self._build_language_summary(
                repo_root=str(root),
                max_items=max(target_files, 1_000),
                language_filter=normalized_filter,
            )
            if normalized_filter is not None and len(per_language) == 0:
                raise BenchmarkError(
                    ErrorContext(
                        code="ERR_BENCHMARK_EMPTY_LANGUAGE_FILTER",
                        message="language filter에 해당하는 인덱싱 파일이 없습니다",
                    )
                )

            recommended_l3_p95 = max(1_000, int(search_p95 * 1.3))
            recommended_dead_ratio = max(5, dead_ratio_bps * 2)
            summary = {
                "run_id": run_id,
                "status": "COMPLETED",
                "repo_root": str(root),
                "target_files": target_files,
                "profile": profile,
                "language_filter": [] if normalized_filter is None else list(normalized_filter),
                "per_language_report": per_language_report,
                "scan": {
                    "scanned_count": scan_result.scanned_count,
                    "indexed_count": scan_result.indexed_count,
                    "deleted_count": scan_result.deleted_count,
                    "ingest_latency_ms_p95": ingest_elapsed_ms,
                },
                "enrich": {
                    "completion_sec": enrich_elapsed_sec,
                    "done_count": done_count,
                    "dead_count": dead_count,
                    "dead_ratio_bps": dead_ratio_bps,
                },
                "search": {
                    "latencies_ms": search_latencies_ms,
                    "search_latency_ms_p95": search_p95,
                },
                "recommended_policy": {
                    "l3_p95_threshold_ms": recommended_l3_p95,
                    "dead_ratio_threshold_bps": recommended_dead_ratio,
                },
                "current_policy": self._policy_repo.get_policy().to_dict(),
            }
            if per_language_report:
                summary["per_language"] = per_language
            self._write_artifact(run_id=run_id, summary=summary)
            self._benchmark_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="COMPLETED",
                summary=summary,
            )
            return summary
        except BenchmarkError as exc:
            failed_summary = {
                "run_id": run_id,
                "status": "FAILED",
                "repo_root": str(root),
                "target_files": target_files,
                "profile": profile,
                "error": str(exc),
            }
            self._benchmark_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="FAILED",
                summary=failed_summary,
            )
            raise
        except (CollectionError, OSError, RuntimeError, ValueError) as exc:
            failed_summary = {
                "run_id": run_id,
                "status": "FAILED",
                "repo_root": str(root),
                "target_files": target_files,
                "profile": profile,
                "error": str(exc),
            }
            self._benchmark_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="FAILED",
                summary=failed_summary,
            )
            raise BenchmarkError(ErrorContext(code="ERR_BENCHMARK_FAILED", message=f"benchmark failed: {exc}")) from exc

    def get_latest_report(self) -> dict[str, object]:
        """최신 벤치마크 리포트를 반환한다."""
        latest = self._benchmark_repo.get_latest_run()
        if latest is None:
            raise BenchmarkError(ErrorContext(code="ERR_BENCHMARK_NOT_FOUND", message="no benchmark run found"))
        summary = latest.get("summary")
        if isinstance(summary, dict):
            return summary
        return latest

    def _prepare_dataset(self, root: Path, target_files: int) -> None:
        """벤치마크용 파일 세트를 생성한다."""
        dataset_dir = root / "benchmark_dataset"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        for index in range(target_files):
            file_path = dataset_dir / f"bench_{index}.py"
            if file_path.exists():
                continue
            file_path.write_text(
                "def bench_symbol_{0}():\n"
                "    value = {0}\n"
                "    return value\n".format(index),
                encoding="utf-8",
            )

    def _drain_enrich_queue(self, max_wait_sec: float) -> None:
        """L2/L3 큐가 비워질 때까지 처리한다."""
        deadline = time.time() + max_wait_sec
        stop_event = threading.Event()
        worker_errors: list[Exception] = []
        worker_errors_lock = threading.Lock()
        worker_threads: list[threading.Thread] = []
        worker_count = self._resolve_benchmark_worker_count()

        def _worker_loop() -> None:
            """벤치마크 큐를 병렬 처리하는 워커 루프다."""
            while not stop_event.is_set():
                try:
                    processed = self._file_collection_service.process_enrich_jobs(limit=100)
                except (CollectionError, RuntimeError, OSError, ValueError) as exc:
                    with worker_errors_lock:
                        worker_errors.append(exc)
                    stop_event.set()
                    return
                if processed == 0:
                    time.sleep(0.02)

        for index in range(worker_count):
            thread = threading.Thread(target=_worker_loop, name=f"benchmark-enrich-{index}", daemon=True)
            worker_threads.append(thread)
            thread.start()

        try:
            while time.time() < deadline:
                with worker_errors_lock:
                    first_error = worker_errors[0] if len(worker_errors) > 0 else None
                if first_error is not None:
                    raise BenchmarkError(
                        ErrorContext(code="ERR_BENCHMARK_FAILED", message=f"benchmark enrich failed: {first_error}")
                    ) from first_error
                counts = self._queue_repo.get_status_counts()
                queue_depth = int(counts.get("PENDING", 0) + counts.get("FAILED", 0) + counts.get("RUNNING", 0))
                if queue_depth == 0:
                    return
                time.sleep(0.05)
        finally:
            stop_event.set()
            for thread in worker_threads:
                thread.join(timeout=1.0)
        raise BenchmarkError(ErrorContext(code="ERR_BENCHMARK_TIMEOUT", message="enrich queue drain timeout"))

    def _resolve_benchmark_worker_count(self) -> int:
        """벤치마크 큐 처리 워커 수를 정책에서 계산한다."""
        try:
            configured = int(self._policy_repo.get_policy().enrich_worker_count)
        except (RuntimeError, ValueError, TypeError):
            configured = 4
        return max(1, configured)

    def _measure_search_latencies(self, repo_root: str) -> list[int]:
        """대표 조회 경로 지연을 측정한다."""
        latencies: list[int] = []

        started = time.perf_counter()
        files = self._file_collection_service.list_files(repo_root=repo_root, limit=20, prefix="benchmark_dataset")
        latencies.append(int((time.perf_counter() - started) * 1000.0))

        if len(files) > 0:
            relative_path = str(files[0].get("relative_path", ""))
            if relative_path != "":
                started = time.perf_counter()
                self._file_collection_service.read_file(
                    repo_root=repo_root,
                    relative_path=relative_path,
                    offset=0,
                    limit=30,
                )
                latencies.append(int((time.perf_counter() - started) * 1000.0))

        started = time.perf_counter()
        self._lsp_repo.search_symbols(repo_root=repo_root, query="bench_", limit=50)
        latencies.append(int((time.perf_counter() - started) * 1000.0))
        return latencies

    def _build_language_summary(
        self,
        repo_root: str,
        max_items: int,
        language_filter: tuple[str, ...] | None,
    ) -> list[dict[str, object]]:
        """현재 인덱스 파일 기준 언어 분포를 생성한다."""
        rows = self._file_collection_service.list_files(repo_root=repo_root, limit=max_items, prefix=None)
        normalized_set = set(language_filter) if language_filter is not None else None
        counts: dict[str, int] = {}
        for row in rows:
            relative_path = str(row.get("relative_path", ""))
            resolved = resolve_language_from_path(relative_path)
            language_name = "unknown" if resolved is None else resolved.value
            if normalized_set is not None and language_name not in normalized_set:
                continue
            counts[language_name] = counts.get(language_name, 0) + 1
        return [
            {"language": language_name, "file_count": file_count}
            for language_name, file_count in sorted(counts.items(), key=lambda item: item[0])
        ]

    def _write_artifact(self, run_id: str, summary: dict[str, object]) -> None:
        """벤치마크 결과 아티팩트를 저장한다."""
        benchmark_dir = self._artifact_root / "benchmarks"
        benchmark_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = benchmark_dir / f"{run_id}.json"
        artifact_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _percentile_95(values: list[int]) -> int:
    """정수 리스트의 95퍼센타일 값을 계산한다."""
    if len(values) == 0:
        return 0
    ordered = sorted(values)
    index = (len(ordered) * 95 + 99) // 100 - 1
    if index < 0:
        index = 0
    if index >= len(ordered):
        index = len(ordered) - 1
    return int(ordered[index])
