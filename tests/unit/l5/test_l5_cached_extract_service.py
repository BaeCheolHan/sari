from __future__ import annotations

import sqlite3

import pytest

from sari.core.exceptions import ErrorContext, ValidationError
from sari.services.collection.l5.l5_cached_extract_service import L5CachedExtractService
from sari.services.lsp_extraction_contracts import LspExtractionResultDTO


class _ToolLayerRepo:
    def __init__(
        self,
        *,
        resolved_repo_root: str | None,
        has_l5: bool,
        l5_semantics: list[dict[str, object]] | None = None,
    ) -> None:
        self._resolved_repo_root = resolved_repo_root
        self._has_l5 = has_l5
        self._l5_semantics = l5_semantics

    def resolve_effective_repo_root(self, **kwargs):  # noqa: ANN003
        return self._resolved_repo_root

    def load_effective_snapshot(self, **kwargs):  # noqa: ANN003
        if self._l5_semantics is not None:
            return {"l5": self._l5_semantics}
        if self._has_l5:
            return {"l5": [{"kind": "class", "semantics": {"relations_count": 1}}]}
        return {"l5": []}


class _LspRepo:
    def __init__(self, *, symbols: list[dict[str, object]], relations: list[dict[str, object]]) -> None:
        self._symbols = symbols
        self._relations = relations

    def list_file_symbols_full(self, repo_root: str, relative_path: str, content_hash: str):  # noqa: ANN001
        return self._symbols

    def list_file_symbols(self, repo_root: str, relative_path: str, content_hash: str):  # noqa: ANN001
        return self._symbols

    def list_file_relations(self, repo_root: str, relative_path: str, content_hash: str):  # noqa: ANN001
        return self._relations


class _FailingToolLayerRepo:
    def resolve_effective_repo_root(self, **kwargs):  # noqa: ANN003
        raise sqlite3.OperationalError("database is locked")

    def load_effective_snapshot(self, **kwargs):  # noqa: ANN003
        return {"l5": []}


class _FailingValidationLspRepo(_LspRepo):
    def __init__(self) -> None:
        super().__init__(symbols=[], relations=[])

    def list_file_symbols_full(self, repo_root: str, relative_path: str, content_hash: str):  # noqa: ANN001
        raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message="bad mapping"))


def test_cached_extract_returns_db_hit_without_delegate() -> None:
    calls: list[tuple[str, str, str]] = []

    def _delegate(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        calls.append((repo_root, relative_path, content_hash))
        return LspExtractionResultDTO(symbols=[], relations=[], error_message="delegate")

    service = L5CachedExtractService(
        tool_layer_repo=_ToolLayerRepo(resolved_repo_root="/repo/module", has_l5=True),
        lsp_repo=_LspRepo(
            symbols=[
                {
                    "name": "UserService",
                    "kind": "class",
                    "line": 10,
                    "end_line": 42,
                    "symbol_key": "user_service",
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": "module",
                }
            ],
            relations=[{"source": "UserService", "target": "UserRepo"}],
        ),
        delegate_extract=_delegate,
        enabled=True,
        log_miss_reason=True,
    )
    result = service.extract("/repo/workspace", "src/service.py", "abc")

    assert result.error_message is None
    assert len(result.symbols) == 1
    assert result.symbols[0].get("symbol_key") == "user_service"
    assert result.symbols[0].get("container_name") == "module"
    assert len(result.relations) == 1
    assert calls == []
    metrics = service.get_metrics()
    assert metrics["l5_db_cache_hit_count"] == 1.0
    assert metrics["l5_lsp_call_skipped_by_content_hash"] == 1.0


def test_cached_extract_falls_back_to_delegate_when_symbols_missing() -> None:
    calls: list[tuple[str, str, str]] = []

    def _delegate(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        calls.append((repo_root, relative_path, content_hash))
        return LspExtractionResultDTO(
            symbols=[{"name": "FromLsp"}],
            relations=[],
            error_message=None,
        )

    service = L5CachedExtractService(
        tool_layer_repo=_ToolLayerRepo(resolved_repo_root="/repo/module", has_l5=False),
        lsp_repo=_LspRepo(symbols=[], relations=[]),
        delegate_extract=_delegate,
        enabled=True,
        log_miss_reason=True,
    )
    result = service.extract("/repo/workspace", "src/service.py", "abc")

    assert len(result.symbols) == 1
    assert calls == [("/repo/workspace", "src/service.py", "abc")]
    metrics = service.get_metrics()
    assert metrics["l5_db_cache_hit_count"] == 0.0
    assert metrics["l5_db_cache_miss_reason_no_symbols"] == 1.0


def test_cached_extract_falls_back_to_delegate_on_sqlite_error() -> None:
    calls: list[tuple[str, str, str]] = []

    def _delegate(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        calls.append((repo_root, relative_path, content_hash))
        return LspExtractionResultDTO(
            symbols=[{"name": "FromLsp"}],
            relations=[],
            error_message=None,
        )

    service = L5CachedExtractService(
        tool_layer_repo=_FailingToolLayerRepo(),
        lsp_repo=_LspRepo(symbols=[], relations=[]),
        delegate_extract=_delegate,
        enabled=True,
        log_miss_reason=True,
    )
    result = service.extract("/repo/workspace", "src/service.py", "abc")

    assert len(result.symbols) == 1
    assert calls == [("/repo/workspace", "src/service.py", "abc")]
    metrics = service.get_metrics()
    assert metrics["l5_db_cache_miss_reason_error_fallback"] == 1.0


def test_cached_extract_falls_back_to_delegate_on_validation_error() -> None:
    calls: list[tuple[str, str, str]] = []

    def _delegate(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        calls.append((repo_root, relative_path, content_hash))
        return LspExtractionResultDTO(
            symbols=[{"name": "FromLsp"}],
            relations=[],
            error_message=None,
        )

    service = L5CachedExtractService(
        tool_layer_repo=_ToolLayerRepo(resolved_repo_root="/repo/module", has_l5=False),
        lsp_repo=_FailingValidationLspRepo(),
        delegate_extract=_delegate,
        enabled=True,
        log_miss_reason=True,
    )
    result = service.extract("/repo/workspace", "src/service.py", "abc")

    assert len(result.symbols) == 1
    assert calls == [("/repo/workspace", "src/service.py", "abc")]
    metrics = service.get_metrics()
    assert metrics["l5_db_cache_miss_reason_error_fallback"] == 1.0


def test_cached_extract_does_not_call_delegate_twice_on_delegate_error() -> None:
    call_count = 0

    def _delegate(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("lsp backend failed")

    service = L5CachedExtractService(
        tool_layer_repo=_ToolLayerRepo(resolved_repo_root=None, has_l5=False),
        lsp_repo=_LspRepo(symbols=[], relations=[]),
        delegate_extract=_delegate,
        enabled=True,
        log_miss_reason=True,
    )

    with pytest.raises(RuntimeError, match="lsp backend failed"):
        service.extract("/repo/workspace", "src/service.py", "abc")

    assert call_count == 1


def test_cached_extract_keeps_db_hit_when_l5_semantics_have_retry_pending_zero_relations() -> None:
    calls: list[tuple[str, str, str]] = []

    def _delegate(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        calls.append((repo_root, relative_path, content_hash))
        return LspExtractionResultDTO(
            symbols=[{"name": "FromLsp"}],
            relations=[{"source": "FromLsp", "target": "Target"}],
            error_message=None,
        )

    service = L5CachedExtractService(
        tool_layer_repo=_ToolLayerRepo(
            resolved_repo_root="/repo/module",
            has_l5=True,
            l5_semantics=[
                {
                    "reason_code": "L5_REASON_GOLDENSET_COVERAGE",
                    "semantics": {"relations_count": 0, "zero_relations_retry_pending": True},
                }
            ],
        ),
        lsp_repo=_LspRepo(symbols=[{"name": "FromDb"}], relations=[]),
        delegate_extract=_delegate,
        enabled=True,
        log_miss_reason=True,
    )

    result = service.extract("/repo/workspace", "src/service.py", "abc")

    assert len(result.symbols) == 1
    assert len(result.relations) == 0
    assert calls == []
    metrics = service.get_metrics()
    assert metrics["l5_db_cache_hit_count"] == 1.0


def test_cached_extract_bypasses_db_hit_when_retry_pending_zero_relations_is_forced() -> None:
    calls: list[tuple[str, str, str]] = []

    def _delegate(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        calls.append((repo_root, relative_path, content_hash))
        return LspExtractionResultDTO(
            symbols=[{"name": "FromLsp"}],
            relations=[{"source": "FromLsp", "target": "Target"}],
            error_message=None,
        )

    service = L5CachedExtractService(
        tool_layer_repo=_ToolLayerRepo(
            resolved_repo_root="/repo/module",
            has_l5=True,
            l5_semantics=[
                {
                    "reason_code": "L5_REASON_GOLDENSET_COVERAGE",
                    "semantics": {"relations_count": 0, "zero_relations_retry_pending": True},
                }
            ],
        ),
        lsp_repo=_LspRepo(symbols=[{"name": "FromDb"}], relations=[]),
        delegate_extract=_delegate,
        enabled=True,
        log_miss_reason=True,
    )

    result = service.extract(
        "/repo/workspace",
        "src/service.py",
        "abc",
        bypass_zero_relations_retry_pending=True,
    )

    assert len(result.symbols) == 1
    assert len(result.relations) == 1
    assert calls == [("/repo/workspace", "src/service.py", "abc")]
    metrics = service.get_metrics()
    assert metrics["l5_db_cache_hit_count"] == 0.0
    assert metrics["l5_db_cache_miss_reason_zero_relations"] == 1.0


def test_cached_extract_ignores_stale_retry_pending_row_when_newer_row_is_ready() -> None:
    calls: list[tuple[str, str, str]] = []

    def _delegate(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        calls.append((repo_root, relative_path, content_hash))
        return LspExtractionResultDTO(symbols=[{"name": "FromLsp"}], relations=[], error_message=None)

    service = L5CachedExtractService(
        tool_layer_repo=_ToolLayerRepo(
            resolved_repo_root="/repo/module",
            has_l5=True,
            l5_semantics=[
                {
                    "reason_code": "L5_REASON_A",
                    "updated_at": "2026-03-11T00:00:00+00:00",
                    "semantics": {"relations_count": 0, "zero_relations_retry_pending": True},
                },
                {
                    "reason_code": "L5_REASON_B",
                    "updated_at": "2026-03-11T00:00:01+00:00",
                    "semantics": {"relations_count": 7, "zero_relations_retry_pending": False},
                },
            ],
        ),
        lsp_repo=_LspRepo(symbols=[{"name": "FromDb"}], relations=[{"source": "A", "target": "B"}]),
        delegate_extract=_delegate,
        enabled=True,
        log_miss_reason=True,
    )

    result = service.extract("/repo/workspace", "src/service.py", "abc")

    assert len(result.symbols) == 1
    assert len(result.relations) == 1
    assert calls == []
    metrics = service.get_metrics()
    assert metrics["l5_db_cache_hit_count"] == 1.0
    assert metrics["l5_db_cache_miss_reason_zero_relations"] == 0.0
