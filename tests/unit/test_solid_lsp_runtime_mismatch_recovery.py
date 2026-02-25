from __future__ import annotations

from sari.services.collection.solid_lsp_extraction_backend import SolidLspExtractionBackend
from sari.services.lsp_extraction_contracts import LspExtractionResultDTO


class _FakeHub:
    def __init__(self) -> None:
        self.force_restart_calls: list[tuple[str, str, str]] = []

    def get_metrics(self) -> dict[str, int]:
        return {}

    def force_restart(self, *, language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
        self.force_restart_calls.append((language.value, repo_root, request_kind))
        return object()


def test_runtime_mismatch_auto_recovery_retries_once_and_recovers() -> None:
    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    calls = {"count": 0}

    def _extract_once(*, repo_root: str, normalized_relative_path: str) -> LspExtractionResultDTO:
        calls["count"] += 1
        if calls["count"] == 1:
            return LspExtractionResultDTO(
                symbols=[],
                relations=[],
                error_message="ERR_RUNTIME_MISMATCH: Java 17+ 런타임이 필요합니다(현재: 11)",
            )
        return LspExtractionResultDTO(symbols=[{"name": "A", "kind": "class", "line": 1, "end_line": 1}], relations=[], error_message=None)

    backend._extract_once = _extract_once  # type: ignore[method-assign]

    result = backend.extract(repo_root="/workspace/repo-a", relative_path="src/A.java", content_hash="h1")

    assert result.error_message is None
    assert calls["count"] == 2
    assert len(hub.force_restart_calls) == 1
    metrics = backend.get_runtime_metrics()
    assert metrics["runtime_mismatch_auto_recovered_count"] == 1
    assert metrics["runtime_mismatch_auto_recover_failed_count"] == 0


def test_runtime_mismatch_auto_recovery_records_failure_when_restart_unavailable() -> None:
    class _NoRestartHub:
        def get_metrics(self) -> dict[str, int]:
            return {}

    backend = SolidLspExtractionBackend(hub=_NoRestartHub())  # type: ignore[arg-type]

    def _extract_once(*, repo_root: str, normalized_relative_path: str) -> LspExtractionResultDTO:
        return LspExtractionResultDTO(
            symbols=[],
            relations=[],
            error_message="ERR_RUNTIME_MISMATCH: Java 17+ 런타임이 필요합니다(현재: 11)",
        )

    backend._extract_once = _extract_once  # type: ignore[method-assign]
    result = backend.extract(repo_root="/workspace/repo-a", relative_path="src/A.java", content_hash="h1")

    assert result.error_message is not None
    metrics = backend.get_runtime_metrics()
    assert metrics["runtime_mismatch_auto_recovered_count"] == 0
    assert metrics["runtime_mismatch_auto_recover_failed_count"] == 1

