"""L3 job scheduling(재분배/그룹/정렬/병렬도) 책임을 분리한다."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from sari.core.models import FileEnrichJobDTO


class L3SchedulingService:
    def __init__(
        self,
        *,
        resolve_lsp_language: Callable[[str], Any | None],
        lsp_backend: object,
        l3_parallel_enabled: bool,
        executor_max_workers: int,
        backpressure_on_interactive: bool,
        backpressure_cooldown_sec: float,
        monotonic_now: Callable[[], float],
    ) -> None:
        self._resolve_lsp_language = resolve_lsp_language
        self._lsp_backend = lsp_backend
        self._l3_parallel_enabled = bool(l3_parallel_enabled)
        self._executor_max_workers = max(1, int(executor_max_workers))
        self._backpressure_on_interactive = bool(backpressure_on_interactive)
        self._backpressure_cooldown_sec = float(backpressure_cooldown_sec)
        self._monotonic_now = monotonic_now
        self._l3_backpressure_until = 0.0
        self._last_interactive_timeout_count = 0

    def rebalance_jobs_by_language(self, jobs: list[FileEnrichJobDTO]) -> list[FileEnrichJobDTO]:
        if len(jobs) <= 1:
            return jobs
        buckets: dict[str, deque[FileEnrichJobDTO]] = {}
        order: list[str] = []
        for job in jobs:
            language = self._resolve_lsp_language(job.relative_path)
            key = "other" if language is None else str(language.value)
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

    def group_jobs_by_repo_and_language(self, jobs: list[FileEnrichJobDTO]) -> list[list[FileEnrichJobDTO]]:
        grouped: dict[tuple[str, str], list[FileEnrichJobDTO]] = {}
        ordered_keys: list[tuple[str, str]] = []
        for job in jobs:
            language = self._resolve_lsp_language(job.relative_path)
            language_key = "other" if language is None else str(language.value)
            key = (job.repo_root, language_key)
            if key not in grouped:
                grouped[key] = []
                ordered_keys.append(key)
            grouped[key].append(job)
        return [grouped[key] for key in ordered_keys]

    def order_l3_groups_for_scheduling(self, groups: list[list[FileEnrichJobDTO]]) -> list[list[FileEnrichJobDTO]]:
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

    def resolve_l3_parallelism(self, jobs: list[FileEnrichJobDTO]) -> int:
        if len(jobs) <= 1:
            return 1
        if not self._l3_parallel_enabled:
            return 1
        language = self._resolve_lsp_language(jobs[0].relative_path)
        if language is None:
            return 1
        requested_parallelism = min(len(jobs), self._executor_max_workers)
        if requested_parallelism <= 1:
            return 1
        now = float(self._monotonic_now())
        if self._backpressure_on_interactive:
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
                        self._l3_backpressure_until = now + self._backpressure_cooldown_sec
                    self._last_interactive_timeout_count = max(self._last_interactive_timeout_count, timeout_count)
                    if pending_interactive > 0:
                        return 1
            if now < self._l3_backpressure_until:
                requested_parallelism = max(1, requested_parallelism // 2)
        batch_getter = getattr(self._lsp_backend, "get_parallelism_for_batch", None)
        if callable(batch_getter):
            try:
                backend_parallelism = int(batch_getter(jobs[0].repo_root, language, requested_parallelism))
            except (RuntimeError, OSError, ValueError, TypeError):
                backend_parallelism = 1
            return max(1, min(len(jobs), requested_parallelism, backend_parallelism))
        getter = getattr(self._lsp_backend, "get_parallelism", None)
        backend_parallelism = 1
        if callable(getter):
            try:
                backend_parallelism = int(getter(jobs[0].repo_root, language))
            except (RuntimeError, OSError, ValueError, TypeError):
                backend_parallelism = 1
        return max(1, min(len(jobs), requested_parallelism, backend_parallelism))
