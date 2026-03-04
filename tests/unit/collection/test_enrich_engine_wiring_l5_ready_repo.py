from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.enrich_engine_wiring import _is_recent_l5_ready


@dataclass
class _ToolLayerRepo:
    response: bool

    def has_l5_semantics(self, *, repo_root: str, relative_path: str, content_hash: str) -> bool:
        del repo_root, relative_path, content_hash
        return self.response


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
