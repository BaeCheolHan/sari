"""L2/L3 파이프라인 워커 전용 컴포넌트."""

from __future__ import annotations

from typing import Callable


class PipelineWorker:
    """보강 워커 책임을 담당하는 전용 서비스."""

    def __init__(
        self,
        *,
        process_enrich_jobs: Callable[[int], int],
        process_enrich_jobs_l2: Callable[[int], int],
        process_enrich_jobs_l3: Callable[[int], int],
    ) -> None:
        """보강 처리 함수를 주입받는다."""
        self._process_enrich_jobs = process_enrich_jobs
        self._process_enrich_jobs_l2 = process_enrich_jobs_l2
        self._process_enrich_jobs_l3 = process_enrich_jobs_l3

    def process_enrich_jobs(self, limit: int) -> int:
        """단일 루프에서 L2/L3 통합 처리를 실행한다."""
        return self._process_enrich_jobs(limit)

    def process_enrich_jobs_l2(self, limit: int) -> int:
        """L2 본문/벡터 보강 처리를 실행한다."""
        return self._process_enrich_jobs_l2(limit)

    def process_enrich_jobs_l3(self, limit: int) -> int:
        """L3 LSP 보강 처리를 실행한다."""
        return self._process_enrich_jobs_l3(limit)
