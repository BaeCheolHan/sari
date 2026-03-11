from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.enrich_engine_wiring import _is_recent_l5_ready


@dataclass
class _ToolLayerRepo:
    response: bool
    snapshot: dict[str, object] | None = None
    snapshot_exc: Exception | None = None

    def has_l5_semantics(self, *, repo_root: str, relative_path: str, content_hash: str) -> bool:
        del repo_root, relative_path, content_hash
        return self.response

    def load_effective_snapshot(self, *, workspace_id: str, repo_root: str, relative_path: str, content_hash: str):
        del workspace_id, repo_root, relative_path, content_hash
        if self.snapshot_exc is not None:
            raise self.snapshot_exc
        return self.snapshot or {"l5": []}


class _EngineStub:
    pass


def _sample_job() -> FileEnrichJobDTO:
    ts = "2026-03-04T00:00:00+00:00"
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h1",
        priority=1,
        enqueue_source="l5",
        status="PENDING",
        attempt_count=0,
        last_error=None,
        next_retry_at=ts,
        created_at=ts,
        updated_at=ts,
    )


def test_is_recent_l5_ready_uses_tool_layer_repo_when_present() -> None:
    engine = _EngineStub()
    engine._tool_layer_repo = _ToolLayerRepo(response=True)

    assert _is_recent_l5_ready(engine, _sample_job()) is True


def test_is_recent_l5_ready_returns_false_when_no_tool_repo_available() -> None:
    engine = _EngineStub()

    assert _is_recent_l5_ready(engine, _sample_job()) is False


def test_is_recent_l5_ready_bypasses_retry_pending_zero_relations_job() -> None:
    engine = _EngineStub()
    engine._tool_layer_repo = _ToolLayerRepo(
        response=True,
        snapshot={"l5": [{"semantics": {"zero_relations_retry_pending": True}}]},
    )
    job = _sample_job()
    object.__setattr__(job, "defer_reason", "retry_zero_relations")

    assert _is_recent_l5_ready(engine, job) is False


def test_is_recent_l5_ready_returns_false_when_retry_snapshot_lookup_fails() -> None:
    engine = _EngineStub()
    engine._tool_layer_repo = _ToolLayerRepo(response=True, snapshot_exc=RuntimeError("db busy"))
    job = _sample_job()
    object.__setattr__(job, "defer_reason", "retry_zero_relations")

    assert _is_recent_l5_ready(engine, job) is False


def test_is_recent_l5_ready_ignores_stale_retry_pending_row_when_newer_row_is_ready() -> None:
    engine = _EngineStub()
    engine._tool_layer_repo = _ToolLayerRepo(
        response=True,
        snapshot={
            "l5": [
                {
                    "reason_code": "L5_REASON_A",
                    "updated_at": "2026-03-11T00:00:00+00:00",
                    "semantics": {"zero_relations_retry_pending": True},
                },
                {
                    "reason_code": "L5_REASON_B",
                    "updated_at": "2026-03-11T00:00:01+00:00",
                    "semantics": {"zero_relations_retry_pending": False, "relations_count": 12},
                },
            ]
        },
    )
    job = _sample_job()
    object.__setattr__(job, "defer_reason", "retry_zero_relations")

    assert _is_recent_l5_ready(engine, job) is True
