"""Batch-89 L3 병렬도 결정 로직을 검증한다."""

from __future__ import annotations

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.enrich_engine import EnrichEngine


class _BackendWithParallelism:
    """병렬도 힌트를 반환하는 테스트 더블이다."""

    def __init__(self, value: int) -> None:
        self._value = value

    def get_parallelism(self, repo_root: str, language: object) -> int:
        del repo_root, language
        return self._value


def _sample_job(path: str) -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id=f"job-{path}",
        repo_id="repo",
        repo_root="/repo",
        relative_path=path,
        content_hash="h",
        priority=60,
        enqueue_source="scan",
        status="PENDING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _build_engine_for_parallelism(backend: object, *, enabled: bool) -> EnrichEngine:
    """_resolve_l3_parallelism 테스트에 필요한 최소 상태를 채운 엔진을 생성한다."""
    engine = object.__new__(EnrichEngine)
    engine._l3_parallel_enabled = enabled
    engine._lsp_backend = backend
    engine._l3_backpressure_on_interactive = False
    return engine


def test_l3_parallelism_disabled_forces_single_worker() -> None:
    engine = _build_engine_for_parallelism(_BackendWithParallelism(8), enabled=False)
    jobs = [_sample_job("a.py"), _sample_job("b.py")]
    assert engine._resolve_l3_parallelism(jobs) == 1


def test_l3_parallelism_uses_backend_hint_with_job_count_cap() -> None:
    engine = _build_engine_for_parallelism(_BackendWithParallelism(8), enabled=True)
    jobs = [_sample_job("a.py"), _sample_job("b.py"), _sample_job("c.py")]
    assert engine._resolve_l3_parallelism(jobs) == 3
