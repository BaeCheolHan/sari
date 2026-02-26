from __future__ import annotations

import queue
from dataclasses import dataclass

from solidlsp.ls_config import Language

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3_runtime_coordination_service import L3RuntimeCoordinationService


def _job(job_id: str = "j1") -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id=job_id,
        repo_id="r1",
        repo_root="/workspace",
        relative_path="module/src/main.py",
        content_hash="h1",
        priority=100,
        enqueue_source="scan",
        status="pending",
        attempt_count=1,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        scope_level="module",
        scope_attempts=0,
    )


class _Backend:
    def __init__(self) -> None:
        self.scheduled: list[dict[str, object]] = []
        self.recorded: list[dict[str, object]] = []

    def is_probe_inflight_for_file(self, *, repo_root: str, relative_path: str) -> bool:
        return False

    def schedule_probe_for_file(self, **kwargs: object) -> None:
        self.scheduled.append(kwargs)

    def record_scope_override_success(self, **kwargs: object) -> None:
        self.recorded.append(kwargs)


class _QueueRepo:
    def __init__(self, pending: list[FileEnrichJobDTO]) -> None:
        self._pending = pending

    def acquire_pending_for_l3(self, *, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        return self._pending[:limit]


@dataclass
class _Policy:
    deletion_hold: bool


class _PolicyRepo:
    def __init__(self, deletion_hold: bool) -> None:
        self._policy = _Policy(deletion_hold=deletion_hold)

    def get_policy(self) -> _Policy:
        return self._policy


def test_runtime_coordination_probe_and_scope_recording() -> None:
    backend = _Backend()
    svc = L3RuntimeCoordinationService(
        lsp_backend=backend,
        lsp_probe_l1_languages={Language.PYTHON},
        resolve_language_from_path_fn=lambda _: Language.PYTHON,
        l3_ready_queue=queue.Queue(),
        enrich_queue_repo=_QueueRepo([]),
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        policy_repo=_PolicyRepo(deletion_hold=False),
    )
    job = _job()
    svc.schedule_l1_probe_after_l3_fallback(job)
    svc.record_scope_learning_after_l3_success(job=job)
    assert len(backend.scheduled) == 1
    assert len(backend.recorded) == 1


def test_runtime_coordination_acquire_and_deletion_hold() -> None:
    q: queue.Queue[FileEnrichJobDTO] = queue.Queue()
    j1 = _job("j1")
    q.put(j1)
    j2 = _job("j2")
    svc = L3RuntimeCoordinationService(
        lsp_backend=_Backend(),
        lsp_probe_l1_languages={Language.PYTHON},
        resolve_language_from_path_fn=lambda _: Language.PYTHON,
        l3_ready_queue=q,
        enrich_queue_repo=_QueueRepo([j2]),
        now_iso_supplier=lambda: "2026-01-01T00:00:00Z",
        policy_repo=_PolicyRepo(deletion_hold=True),
    )
    jobs = svc.acquire_l3_jobs(2)
    assert [j.job_id for j in jobs] == ["j1", "j2"]
    assert svc.is_deletion_hold_enabled() is True
