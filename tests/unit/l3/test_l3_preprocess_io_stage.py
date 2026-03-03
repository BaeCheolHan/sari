from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.stages.preprocess_io_stage import L3PreprocessIoStage
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)


def _job(relative_path: str = "src/a.py") -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/workspace",
        relative_path=relative_path,
        content_hash="h1",
        priority=100,
        enqueue_source="scan",
        status="pending",
        attempt_count=1,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


@dataclass
class _FileRow:
    absolute_path: str | None


class _PreprocessService:
    def __init__(self, result: L3PreprocessResultDTO) -> None:
        self._result = result

    def preprocess(self, *, relative_path: str, content_text: str, max_bytes: int) -> L3PreprocessResultDTO:
        _ = (relative_path, content_text, max_bytes)
        return self._result


class _FallbackService:
    def fallback(self, *, relative_path: str, content_text: str) -> L3PreprocessResultDTO:
        _ = (relative_path, content_text)
        return L3PreprocessResultDTO(
            symbols=[{"name": "fallback"}],
            degraded=True,
            decision=L3PreprocessDecision.NEEDS_L5,
            source="regex_outline",
            reason="fallback",
        )


def test_preprocess_io_stage_uses_fallback_when_symbols_empty(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text("def a():\n  return 1\n", encoding="utf-8")
    stage = L3PreprocessIoStage(
        preprocess_service=_PreprocessService(
            L3PreprocessResultDTO(
                symbols=[],
                degraded=False,
                decision=L3PreprocessDecision.NEEDS_L5,
                source="tree_sitter",
                reason="empty",
            )
        ),
        degraded_fallback_service=_FallbackService(),
        preprocess_max_bytes=1024,
    )
    result = stage.run(job=_job("a.py"), file_row=_FileRow(str(p)))
    assert result is not None
    assert result.reason == "fallback"


def test_preprocess_io_stage_returns_none_without_service() -> None:
    stage = L3PreprocessIoStage(
        preprocess_service=None,
        degraded_fallback_service=None,
        preprocess_max_bytes=1024,
    )
    result = stage.run(job=_job(), file_row=_FileRow(None))
    assert result is None
