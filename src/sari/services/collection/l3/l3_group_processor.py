"""L3 group 단위 처리 오케스트레이터."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable

from solidlsp.ls_config import Language

from sari.core.models import CollectedFileBodyDTO, FileEnrichJobDTO
from sari.services.collection.perf_trace import PerfTracer


class L3GroupProcessor:
    """L3 그룹 단위 처리 오케스트레이션을 담당한다."""

    def __init__(
        self,
        *,
        lsp_backend: object,
        l3_executor: ThreadPoolExecutor,
        perf_tracer: PerfTracer,
        resolve_lsp_language: Callable[[str], Language | None],
        set_group_bulk_mode: Callable[[list[FileEnrichJobDTO], bool], None],
        resolve_l3_parallelism: Callable[[list[FileEnrichJobDTO]], int],
        process_single_l3_job: Callable[[FileEnrichJobDTO], Any],
        merge_l3_result: Callable[..., None],
        flush_l3_buffers: Callable[..., None],
        group_wait_timeout_sec: float,
        now_iso_supplier: Callable[[], str],
        build_timeout_failure_result: Callable[..., Any],
    ) -> None:
        self._lsp_backend = lsp_backend
        self._l3_executor = l3_executor
        self._perf_tracer = perf_tracer
        self._resolve_lsp_language = resolve_lsp_language
        self._set_group_bulk_mode = set_group_bulk_mode
        self._resolve_l3_parallelism = resolve_l3_parallelism
        self._process_single_l3_job = process_single_l3_job
        self._merge_l3_result = merge_l3_result
        self._flush_l3_buffers = flush_l3_buffers
        self._group_wait_timeout_sec = max(0.0, float(group_wait_timeout_sec))
        self._now_iso_supplier = now_iso_supplier
        self._build_timeout_failure_result = build_timeout_failure_result

    def process_group(
        self,
        *,
        group: list[FileEnrichJobDTO],
        buffers: object,
        body_upserts: list[CollectedFileBodyDTO],
    ) -> int:
        group_language = (
            self._resolve_lsp_language(group[0].relative_path).value
            if len(group) > 0 and self._resolve_lsp_language(group[0].relative_path) is not None
            else "unknown"
        )
        prime_pending_hints = getattr(self._lsp_backend, "prime_l3_group_pending_hints", None)
        if callable(prime_pending_hints):
            try:
                prime_pending_hints(group_jobs=group)
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                ...
        processed = 0
        self._set_group_bulk_mode(group, True)
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
                        try:
                            result = self._process_single_l3_job(job)
                        except TimeoutError:
                            result = self._build_timeout_failure_result(
                                job=job,
                                timeout_sec=self._group_wait_timeout_sec,
                                now_iso=self._now_iso_supplier(),
                                group_size=len(group),
                            )
                        processed += 1
                        self._merge_l3_result(result=result, buffers=buffers)
                        if getattr(result, "dev_error", None) is not None:
                            self._flush_l3_buffers(buffers=buffers, body_upserts=body_upserts)
                            raise result.dev_error
                else:
                    futures: list[Future[Any]] = []
                    future_to_job: dict[Future[Any], FileEnrichJobDTO] = {}
                    for job in group[:group_parallelism]:
                        future = self._l3_executor.submit(self._process_single_l3_job, job)
                        futures.append(future)
                        future_to_job[future] = job
                    if len(group) > group_parallelism:
                        for job in group[group_parallelism:]:
                            future = self._l3_executor.submit(self._process_single_l3_job, job)
                            futures.append(future)
                            future_to_job[future] = job
                    with self._perf_tracer.span(
                        "process_enrich_jobs_l3.group_future_wait",
                        phase="l3_group_wait",
                        repo_root=(group[0].repo_root if len(group) > 0 else ""),
                        language=group_language,
                        group_size=len(group),
                        parallelism=group_parallelism,
                    ):
                        # 느린 작업은 완료까지 대기하고 정상 결과로 합류시킨다.
                        for future in as_completed(futures):
                            try:
                                result = future.result()
                            except TimeoutError:
                                result = self._build_timeout_failure_result(
                                    job=future_to_job[future],
                                    timeout_sec=self._group_wait_timeout_sec,
                                    now_iso=self._now_iso_supplier(),
                                    group_size=len(group),
                                )
                            processed += 1
                            self._merge_l3_result(result=result, buffers=buffers)
                            if getattr(result, "dev_error", None) is not None:
                                self._flush_l3_buffers(buffers=buffers, body_upserts=body_upserts)
                                raise result.dev_error
        finally:
            self._set_group_bulk_mode(group, False)
        return processed
