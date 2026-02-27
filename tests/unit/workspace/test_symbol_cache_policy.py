"""심볼 캐시 업서트/무효화 정책을 검증한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import itertools

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


@dataclass
class VariantAwareBackend(LspQueryBackend):
    """include_info 모드별 호출과 결과를 구분하는 테스트 백엔드다."""

    calls: int = 0

    def query_document_symbols(
        self,
        repo_root: str,
        language: Language,
        relative_paths: list[str],
        query: str,
        limit: int,
        include_info: bool = False,
        symbol_info_budget_sec: float = 0.0,
    ) -> list[SearchItemDTO]:
        _ = (language, relative_paths, query, limit, symbol_info_budget_sec)
        self.calls += 1
        return [
            SearchItemDTO(
                item_type="symbol",
                repo=repo_root,
                relative_path="sample.py",
                score=1.0,
                source="lsp",
                name="cached_symbol",
                kind="12",
                symbol_info="info" if include_info else None,
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


def test_symbol_cache_uses_separate_variants_for_list_and_detail(tmp_path) -> None:
    """list/detail 캐시는 서로 분리되어야 하며 각 변형에서 재호출을 피해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    backend = VariantAwareBackend()
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

    detail_first, _ = service.resolve(candidates=candidates, query="abc", limit=5, include_info=True)
    detail_second, _ = service.resolve(candidates=candidates, query="abc", limit=5, include_info=True)
    list_first, _ = service.resolve(candidates=candidates, query="abc", limit=5, include_info=False)
    list_second, _ = service.resolve(candidates=candidates, query="abc", limit=5, include_info=False)

    assert backend.calls == 2
    assert detail_first[0].symbol_info == "info"
    assert detail_second[0].symbol_info == "info"
    assert list_first[0].symbol_info is None
    assert list_second[0].symbol_info is None


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


def test_symbol_resolve_blocks_fallback_when_interactive_pressure_high(tmp_path) -> None:
    """interactive 압력이 임계치를 넘으면 LSP fallback을 차단해야 한다."""

    class _PressureHub:
        def resolve_language(self, file_path: str) -> Language:
            _ = file_path
            return Language.PYTHON

        def get_interactive_pressure(self) -> dict[str, int]:
            return {"pending_interactive": 3, "interactive_timeout_count": 0, "interactive_rejected_count": 0}

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    backend = VariantAwareBackend()
    service = SymbolResolveService(
        hub=_PressureHub(),  # type: ignore[arg-type]
        cache_repo=SymbolCacheRepository(db_path),
        backend=backend,
        lsp_pressure_guard_enabled=True,
        lsp_pressure_pending_threshold=1,
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
    assert "interactive 압력 보호모드" in errors[0].message
    assert service.last_fallback_reason == "interactive_pressure"
    assert backend.calls == 0


def test_symbol_resolve_recent_failure_cooldown_blocks_repeated_lsp_calls(tmp_path) -> None:
    """최근 실패 쿨다운 동안 동일 scope LSP fallback 재시도를 막아야 한다."""

    class _CooldownHub:
        def resolve_language(self, file_path: str) -> Language:
            _ = file_path
            return Language.PYTHON

        def get_interactive_pressure(self) -> dict[str, int]:
            return {"pending_interactive": 0, "interactive_timeout_count": 0, "interactive_rejected_count": 0}

    @dataclass
    class _FailOnceBackend(LspQueryBackend):
        calls: int = 0

        def query_document_symbols(
            self,
            repo_root: str,
            language: Language,
            relative_paths: list[str],
            query: str,
            limit: int,
            include_info: bool = False,
            symbol_info_budget_sec: float = 0.0,
        ) -> list[SearchItemDTO]:
            _ = (repo_root, language, relative_paths, query, limit, include_info, symbol_info_budget_sec)
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("forced failure")
            return [
                SearchItemDTO(
                    item_type="symbol",
                    repo=repo_root,
                    relative_path="sample.py",
                    score=1.0,
                    source="lsp",
                    name="x",
                    kind="12",
                )
            ]

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    backend = _FailOnceBackend()
    service = SymbolResolveService(
        hub=_CooldownHub(),  # type: ignore[arg-type]
        cache_repo=SymbolCacheRepository(db_path),
        backend=backend,
        lsp_recent_failure_cooldown_sec=60.0,
    )
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

    assert first_items == []
    assert len(first_errors) == 1
    assert first_errors[0].code == "ERR_LSP_QUERY_FAILED"
    assert second_items == []
    assert len(second_errors) == 1
    assert second_errors[0].code == "ERR_LSP_FALLBACK_BLOCKED"
    assert "최근 LSP 실패 cooldown" in second_errors[0].message
    assert service.last_fallback_reason == "recent_failure_cooldown"
    assert backend.calls == 1


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


class _CaptureLsp:
    """documentSymbol 호출 옵션을 캡처하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.sync_with_ls_flags: list[bool] = []

    def request_document_symbols(self, relative_path: str, *, sync_with_ls: bool = True) -> _FakeDocumentSymbols:
        del relative_path
        self.sync_with_ls_flags.append(bool(sync_with_ls))
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


class _CaptureHub:
    """호출 옵션 캡처용 LSP를 반환하는 허브 더블이다."""

    def __init__(self, lsp: _CaptureLsp) -> None:
        self._lsp = lsp

    def get_or_start(self, language: Language, repo_root: str, request_kind: str = "interactive") -> _CaptureLsp:
        del language, repo_root, request_kind
        return self._lsp

    def ensure_healthy(self, language: Language, repo_root: str) -> None:
        _ = (language, repo_root)

    def restart_if_unhealthy(self, language: Language, repo_root: str) -> _CaptureLsp:
        _ = (language, repo_root)
        return self._lsp


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


def test_solid_lsp_query_backend_requests_document_symbols_without_sync_when_supported() -> None:
    """검색용 documentSymbol 경로는 sync_with_ls=False를 우선 사용해야 한다."""
    lsp = _CaptureLsp()
    backend = SolidLspQueryBackend(hub=_CaptureHub(lsp))  # type: ignore[arg-type]

    items = backend.query_document_symbols(
        repo_root="/repo",
        language=Language.PYTHON,
        relative_paths=["a.py"],
        query="main",
        limit=5,
    )

    assert len(items) == 1
    assert lsp.sync_with_ls_flags == [False]


def test_solid_lsp_query_backend_include_info_populates_symbol_info() -> None:
    """include_info=true면 hover 기반 symbol_info가 채워져야 한다."""

    class _InfoSymbols:
        def iter_symbols(self) -> list[dict[str, object]]:
            return [
                {
                    "name": "main",
                    "kind": 12,
                    "location": {
                        "relativePath": "src/main.py",
                        "range": {"start": {"line": 3, "character": 1}},
                    },
                }
            ]

    class _InfoLsp:
        def request_document_symbols(self, relative_path: str, *, sync_with_ls: bool = True) -> _InfoSymbols:
            del relative_path, sync_with_ls
            return _InfoSymbols()

        def request_hover(self, relative_path: str, line: int, column: int) -> dict[str, object]:
            assert relative_path == "src/main.py"
            assert line == 3
            assert column == 1
            return {"contents": {"value": "def main() -> int"}}

    class _InfoHub:
        def ensure_healthy(self, language: Language, repo_root: str) -> None:
            _ = (language, repo_root)

        def restart_if_unhealthy(self, language: Language, repo_root: str) -> _InfoLsp:
            _ = (language, repo_root)
            return _InfoLsp()

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "interactive") -> _InfoLsp:
            _ = (language, repo_root, request_kind)
            return _InfoLsp()

    backend = SolidLspQueryBackend(hub=_InfoHub())  # type: ignore[arg-type]
    items = backend.query_document_symbols(
        repo_root="/repo",
        language=Language.PYTHON,
        relative_paths=["src/main.py"],
        query="main",
        limit=10,
        include_info=True,
        symbol_info_budget_sec=10.0,
    )

    assert len(items) == 1
    assert items[0].symbol_info == "def main() -> int"
    assert backend.last_query_stats.symbol_info_requested_count == 1


def test_solid_lsp_query_backend_symbol_info_budget_skips_after_exceeded(monkeypatch) -> None:
    """symbol_info_budget_sec를 초과하면 이후 상세조회는 건너뛰어야 한다."""

    class _BudgetSymbols:
        def iter_symbols(self) -> list[dict[str, object]]:
            return [
                {
                    "name": "alpha",
                    "kind": 12,
                    "location": {"relativePath": "src/a.py", "range": {"start": {"line": 1, "character": 0}}},
                },
                {
                    "name": "beta",
                    "kind": 12,
                    "location": {"relativePath": "src/a.py", "range": {"start": {"line": 2, "character": 0}}},
                },
            ]

    class _BudgetLsp:
        def __init__(self) -> None:
            self.hover_calls = 0

        def request_document_symbols(self, relative_path: str, *, sync_with_ls: bool = True) -> _BudgetSymbols:
            del relative_path, sync_with_ls
            return _BudgetSymbols()

        def request_hover(self, relative_path: str, line: int, column: int) -> dict[str, object]:
            del relative_path, line, column
            self.hover_calls += 1
            return {"contents": "info"}

    class _BudgetHub:
        def __init__(self, lsp: _BudgetLsp) -> None:
            self._lsp = lsp

        def ensure_healthy(self, language: Language, repo_root: str) -> None:
            _ = (language, repo_root)

        def restart_if_unhealthy(self, language: Language, repo_root: str) -> _BudgetLsp:
            _ = (language, repo_root)
            return self._lsp

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "interactive") -> _BudgetLsp:
            _ = (language, repo_root, request_kind)
            return self._lsp

    tick = itertools.count()

    def _fake_monotonic() -> float:
        return next(tick) * 0.04

    monkeypatch.setattr("sari.search.symbol_resolve.time.monotonic", _fake_monotonic)
    lsp = _BudgetLsp()
    backend = SolidLspQueryBackend(hub=_BudgetHub(lsp))  # type: ignore[arg-type]
    items = backend.query_document_symbols(
        repo_root="/repo",
        language=Language.PYTHON,
        relative_paths=["src/a.py"],
        query="",
        limit=10,
        include_info=True,
        symbol_info_budget_sec=0.05,
    )

    assert len(items) == 2
    assert lsp.hover_calls == 1
    assert items[0].symbol_info == "info"
    assert items[1].symbol_info is None
    assert backend.last_query_stats.symbol_info_budget_exceeded_count >= 1
    assert backend.last_query_stats.symbol_info_skipped_count >= 1
