"""심볼 캐시 업서트/무효화 정책을 검증한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sari.core.exceptions import DaemonError, ErrorContext
from sari.core.models import CandidateFileDTO, SearchItemDTO
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.schema import init_schema
from sari.lsp.hub import LspHub
from sari.search.symbol_resolve import LspQueryBackend, SolidLspQueryBackend, SymbolResolveService
from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException


@dataclass
class CountingBackend(LspQueryBackend):
    """호출 횟수를 추적하는 테스트 백엔드다."""

    calls: int = 0

    def query_document_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
    ) -> list[SearchItemDTO]:
        """고정 심볼 결과를 반환한다."""
        self.calls += 1
        _ = (repo_root, language, relative_paths, query, limit)
        return [
            SearchItemDTO(
                item_type="symbol",
                repo=repo_root,
                relative_path="sample.py",
                score=1.0,
                source="lsp",
                name="cached_symbol",
                kind="12",
            )
        ]


def test_symbol_cache_hit_avoids_second_backend_call(tmp_path) -> None:
    """동일 파일 해시에서는 캐시 히트로 백엔드 재호출을 피하는지 검증한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    backend = CountingBackend()
    cache_repo = SymbolCacheRepository(db_path)
    service = SymbolResolveService(hub=LspHub(), cache_repo=cache_repo, backend=backend)

    candidates = [
        CandidateFileDTO(
            repo_root=str(tmp_path),
            relative_path="sample.py",
            score=1.0,
            file_hash="hash-a",
        )
    ]

    first_items, first_errors = service.resolve(candidates=candidates, query="abc", limit=5)
    second_items, second_errors = service.resolve(candidates=candidates, query="abc", limit=5)

    assert len(first_errors) == 0
    assert len(second_errors) == 0
    assert len(first_items) == 1
    assert len(second_items) == 1
    assert backend.calls == 1


def test_symbol_cache_miss_on_changed_hash_calls_backend_again(tmp_path) -> None:
    """파일 해시가 바뀌면 캐시 미스로 재조회하는지 검증한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    backend = CountingBackend()
    cache_repo = SymbolCacheRepository(db_path)
    service = SymbolResolveService(hub=LspHub(), cache_repo=cache_repo, backend=backend)

    first = [
        CandidateFileDTO(
            repo_root=str(tmp_path),
            relative_path="sample.py",
            score=1.0,
            file_hash="hash-a",
        )
    ]
    second = [
        CandidateFileDTO(
            repo_root=str(tmp_path),
            relative_path="sample.py",
            score=1.0,
            file_hash="hash-b",
        )
    ]

    service.resolve(candidates=first, query="abc", limit=5)
    service.resolve(candidates=second, query="abc", limit=5)

    assert backend.calls == 2


@dataclass
class FailingBackend(LspQueryBackend):
    """항상 조회 실패를 발생시키는 테스트 백엔드다."""

    def query_document_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
    ) -> list[SearchItemDTO]:
        """백엔드 실패를 재현한다."""
        _ = (repo_root, language, relative_paths, query, limit)
        raise RuntimeError("backend crashed")


def test_symbol_resolve_exposes_backend_failure_message(tmp_path) -> None:
    """백엔드 예외 원인은 ERR_LSP_QUERY_FAILED 메시지에 포함되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    backend = FailingBackend()
    cache_repo = SymbolCacheRepository(db_path)
    service = SymbolResolveService(hub=LspHub(), cache_repo=cache_repo, backend=backend)

    candidates = [
        CandidateFileDTO(
            repo_root=str(tmp_path),
            relative_path="sample.py",
            score=1.0,
            file_hash="hash-a",
        )
    ]

    items, errors = service.resolve(candidates=candidates, query="abc", limit=5)

    assert items == []
    assert len(errors) == 1
    assert errors[0].code == "ERR_LSP_QUERY_FAILED"
    assert errors[0].severity == "FATAL"
    assert errors[0].origin == "symbol_resolve"
    assert "backend crashed" in errors[0].message


def test_symbol_resolve_strict_mode_blocks_lsp_fallback_on_cache_miss(tmp_path) -> None:
    """strict 모드에서는 캐시 미스 시 LSP fallback을 차단해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    backend = CountingBackend()
    cache_repo = SymbolCacheRepository(db_path)
    service = SymbolResolveService(
        hub=LspHub(),
        cache_repo=cache_repo,
        backend=backend,
        lsp_fallback_mode="strict",
    )

    candidates = [
        CandidateFileDTO(
            repo_root=str(tmp_path),
            relative_path="sample.py",
            score=1.0,
            file_hash="hash-a",
        )
    ]

    items, errors = service.resolve(candidates=candidates, query="abc", limit=5)

    assert items == []
    assert len(errors) == 1
    assert errors[0].code == "ERR_LSP_FALLBACK_BLOCKED"
    assert backend.calls == 0


class _FakeDocumentSymbols:
    """documentSymbol 반복자를 제공하는 테스트 더블이다."""

    def iter_symbols(self) -> list[dict[str, object]]:
        """URL 인코딩된 URI를 포함한 심볼 응답을 반환한다."""
        return [
            {
                "name": "main",
                "kind": 12,
                "location": {
                    "uri": "file:///repo%20root/src/main.py",
                },
            }
        ]


class _FakeLsp:
    """documentSymbol 응답을 고정 반환하는 테스트 더블이다."""

    def request_document_symbols(self, relative_path: str) -> _FakeDocumentSymbols:
        """상대경로 입력을 무시하고 고정 documentSymbol을 반환한다."""
        del relative_path
        return _FakeDocumentSymbols()


class _FakeHub:
    """고정 LSP 인스턴스를 반환하는 테스트 더블이다."""

    def get_or_start(self, language: Language, repo_root: str) -> _FakeLsp:
        """언어/저장소 인자를 무시하고 고정 LSP를 반환한다."""
        del language, repo_root
        return _FakeLsp()

    def ensure_healthy(self, language: Language, repo_root: str) -> None:
        """테스트에서는 항상 healthy로 간주한다."""
        _ = (language, repo_root)

    def restart_if_unhealthy(self, language: Language, repo_root: str) -> _FakeLsp:
        """테스트에서는 restart 시에도 동일 LSP를 반환한다."""
        _ = (language, repo_root)
        return _FakeLsp()


def test_solid_lsp_query_backend_decodes_file_uri_path() -> None:
    """file URI는 URL decode되어 repo-relative path로 변환되어야 한다."""
    backend = SolidLspQueryBackend(hub=_FakeHub())  # type: ignore[arg-type]
    items = backend.query_document_symbols(
        repo_root="/repo root",
        language=Language.PYTHON,
        relative_paths=["src/main.py"],
        query="main",
        limit=10,
    )

    assert len(items) == 1
    assert items[0].relative_path == str(Path("src/main.py").as_posix())


def test_solid_lsp_query_backend_restarts_unhealthy_server() -> None:
    """unhealthy 서버 감지 시 restart 경로가 1회 호출되어야 한다."""

    class _UnhealthyHub:
        """첫 건강검사 실패 후 재시작으로 복구되는 허브 더블이다."""

        def __init__(self) -> None:
            self.ensure_calls = 0
            self.restart_calls = 0

        def ensure_healthy(self, language: Language, repo_root: str) -> None:
            _ = (language, repo_root)
            self.ensure_calls += 1
            raise DaemonError(ErrorContext(code="ERR_LSP_UNHEALTHY", message="unhealthy"))

        def restart_if_unhealthy(self, language: Language, repo_root: str) -> _FakeLsp:
            _ = (language, repo_root)
            self.restart_calls += 1
            return _FakeLsp()

        def get_or_start(self, language: Language, repo_root: str) -> _FakeLsp:
            _ = (language, repo_root)
            return _FakeLsp()

    hub = _UnhealthyHub()
    backend = SolidLspQueryBackend(hub=hub)  # type: ignore[arg-type]

    items = backend.query_document_symbols(
        repo_root="/repo root",
        language=Language.PYTHON,
        relative_paths=["src/main.py"],
        query="main",
        limit=10,
    )

    assert len(items) == 1
    assert hub.ensure_calls == 1
    assert hub.restart_calls == 1


def test_solid_lsp_query_backend_maps_sync_error_to_domain_error() -> None:
    """documentSymbol 동기화 실패는 명시 코드로 DaemonError로 승격되어야 한다."""

    class _SyncFailLsp:
        """documentSymbol 호출 시 동기화 오류를 발생시키는 테스트 더블이다."""

        def request_document_symbols(self, relative_path: str) -> _FakeDocumentSymbols:
            del relative_path
            raise SolidLSPException("ERR_LSP_SYNC_OPEN_FAILED: forced")

    class _SyncFailHub:
        """고정 동기화 실패 LSP를 반환하는 허브 더블이다."""

        def ensure_healthy(self, language: Language, repo_root: str) -> None:
            _ = (language, repo_root)

        def restart_if_unhealthy(self, language: Language, repo_root: str) -> _SyncFailLsp:
            _ = (language, repo_root)
            return _SyncFailLsp()

        def get_or_start(self, language: Language, repo_root: str) -> _SyncFailLsp:
            _ = (language, repo_root)
            return _SyncFailLsp()

    backend = SolidLspQueryBackend(hub=_SyncFailHub())  # type: ignore[arg-type]
    try:
        backend.query_document_symbols(
            repo_root="/repo",
            language=Language.PYTHON,
            relative_paths=["a.py"],
            query="a",
            limit=10,
        )
        assert False, "expected DaemonError"
    except DaemonError as exc:
        assert exc.context.code == "ERR_LSP_SYNC_OPEN_FAILED"
