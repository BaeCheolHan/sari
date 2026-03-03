from __future__ import annotations

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_scheduling_service import L3SchedulingService


def _job(path: str, repo_root: str = "/r") -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id=f"j-{path}",
        repo_id="r1",
        repo_root=repo_root,
        relative_path=path,
        content_hash=f"h-{path}",
        priority=100,
        enqueue_source="watcher",
        status="pending",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_l3_scheduling_rebalance_round_robin() -> None:
    def resolve(path: str):  # noqa: ANN202
        if path.endswith(".py"):
            return type("L", (), {"value": "python"})()
        if path.endswith(".ts"):
            return type("L", (), {"value": "typescript"})()
        return None

    service = L3SchedulingService(
        resolve_lsp_language=resolve,
        lsp_backend=object(),
        l3_parallel_enabled=True,
        executor_max_workers=8,
        backpressure_on_interactive=False,
        backpressure_cooldown_sec=0.3,
        monotonic_now=lambda: 1000.0,
    )
    jobs = [_job("a.py"), _job("b.py"), _job("a.ts"), _job("b.ts")]

    rebalanced = service.rebalance_jobs_by_language(jobs)

    assert [job.relative_path for job in rebalanced] == ["a.py", "a.ts", "b.py", "b.ts"]


def test_l3_scheduling_parallelism_applies_backpressure() -> None:
    class _Backend:
        def get_interactive_pressure(self):  # noqa: ANN201
            return {"pending_interactive": 1, "interactive_timeout_count": 0}

        def get_parallelism_for_batch(self, repo_root: str, language: object, requested: int):  # noqa: ANN001, ANN201
            return requested

    def resolve(path: str):  # noqa: ANN202
        if path.endswith(".py"):
            return type("L", (), {"value": "python"})()
        return None

    service = L3SchedulingService(
        resolve_lsp_language=resolve,
        lsp_backend=_Backend(),
        l3_parallel_enabled=True,
        executor_max_workers=8,
        backpressure_on_interactive=True,
        backpressure_cooldown_sec=0.3,
        monotonic_now=lambda: 1000.0,
    )
    jobs = [_job("a.py"), _job("b.py"), _job("c.py")]

    parallelism = service.resolve_l3_parallelism(jobs)

    assert parallelism == 1
