from __future__ import annotations

from pathlib import Path

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)
from sari.services.collection.l3.l3_preprocess_io_service import L3PreprocessIoService


class _StubPreprocessService:
    def __init__(self, result: L3PreprocessResultDTO) -> None:
        self._result = result
        self.calls: list[tuple[str, str, int]] = []

    def preprocess(self, *, relative_path: str, content_text: str, max_bytes: int) -> L3PreprocessResultDTO:
        self.calls.append((relative_path, content_text, max_bytes))
        return self._result


class _StubFallbackService:
    def __init__(self, result: L3PreprocessResultDTO) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def fallback(self, *, relative_path: str, content_text: str) -> L3PreprocessResultDTO:
        self.calls.append((relative_path, content_text))
        return self._result


class _FileRow:
    def __init__(self, absolute_path: str | None) -> None:
        self.absolute_path = absolute_path


def _job() -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/repo",
        relative_path="src/main.py",
        content_hash="h1",
        priority=10,
        enqueue_source="scan",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at=None,
        created_at="2026-02-01T00:00:00Z",
        updated_at="2026-02-01T00:00:00Z",
    )


def test_preprocess_io_service_uses_fallback_when_empty_symbols(tmp_path: Path) -> None:
    file_path = tmp_path / "main.py"
    file_path.write_text("def x():\n    return 1\n", encoding="utf-8")

    preprocess = _StubPreprocessService(
        L3PreprocessResultDTO(
            symbols=[],
            degraded=False,
            decision=L3PreprocessDecision.L3_ONLY,
            source="tree_sitter",
            reason=None,
        )
    )
    fallback_result = L3PreprocessResultDTO(
        symbols=[{"name": "x", "kind": "function"}],
        degraded=True,
        decision=L3PreprocessDecision.L3_ONLY,
        source="regex_outline",
        reason="fallback",
    )
    fallback = _StubFallbackService(fallback_result)
    service = L3PreprocessIoService(preprocess_service=preprocess, fallback_service=fallback)

    result = service.run(job=_job(), file_row=_FileRow(str(file_path)), max_bytes=4096)

    assert result is fallback_result
    assert len(fallback.calls) == 1


def test_preprocess_io_service_skips_fallback_for_deferred_heavy(tmp_path: Path) -> None:
    file_path = tmp_path / "main.py"
    file_path.write_text("class A:\n    pass\n", encoding="utf-8")
    preprocess_result = L3PreprocessResultDTO(
        symbols=[],
        degraded=True,
        decision=L3PreprocessDecision.DEFERRED_HEAVY,
        source="tree_sitter",
        reason="large_file",
    )
    preprocess = _StubPreprocessService(preprocess_result)
    fallback = _StubFallbackService(
        L3PreprocessResultDTO(
            symbols=[{"name": "A", "kind": "class"}],
            degraded=True,
            decision=L3PreprocessDecision.L3_ONLY,
            source="regex_outline",
            reason="fallback",
        )
    )
    service = L3PreprocessIoService(preprocess_service=preprocess, fallback_service=fallback)

    result = service.run(job=_job(), file_row=_FileRow(str(file_path)), max_bytes=4096)

    assert result is preprocess_result
    assert fallback.calls == []


def test_preprocess_io_service_returns_explicit_degraded_needs_l5_on_exception() -> None:
    class _RaisingPreprocessService:
        def preprocess(self, *, relative_path: str, content_text: str, max_bytes: int) -> L3PreprocessResultDTO:
            raise ValueError("boom")

    service = L3PreprocessIoService(preprocess_service=_RaisingPreprocessService(), fallback_service=None)
    result = service.run(job=_job(), file_row=_FileRow(None), max_bytes=4096)

    assert result is not None
    assert result.degraded is True
    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.reason == "l3_preprocess_exception:ValueError"
