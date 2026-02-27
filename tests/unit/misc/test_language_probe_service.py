"""언어별 LSP readiness probe 서비스를 검증한다."""

from __future__ import annotations

from pathlib import Path
import time

from sari.core.language.registry import LanguageSupportEntry
from sari.core.models import WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.language_probe.service import LanguageProbeService
from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException


class _FakeDocumentSymbols:
    """고정 심볼 목록을 제공하는 테스트 더블이다."""

    def iter_symbols(self) -> list[dict[str, object]]:
        """고정 심볼 목록을 반환한다."""
        return [{"name": "ok", "kind": "function"}]


class _FakeLsp:
    """request_document_symbols 호출만 제공하는 테스트 더블이다."""

    def request_document_symbols(self, relative_path: str) -> _FakeDocumentSymbols:
        """입력 경로를 무시하고 고정 응답을 반환한다."""
        _ = relative_path
        return _FakeDocumentSymbols()


class _FakeHub:
    """언어별 fake LSP를 반환하는 테스트 허브다."""

    def get_or_start(self, language: Language, repo_root: str) -> _FakeLsp:
        """언어/레포 입력을 무시하고 fake LSP를 반환한다."""
        _ = (language, repo_root)
        return _FakeLsp()


class _MissingServerLsp:
    """언어 서버 미설치를 재현하는 테스트 더블이다."""

    def request_document_symbols(self, relative_path: str) -> _FakeDocumentSymbols:
        """실행 파일 누락 예외를 발생시킨다."""
        _ = relative_path
        raise SolidLSPException("No such file or directory: pyright-langserver")


class _MissingServerHub:
    """항상 미설치 LSP를 반환하는 테스트 허브다."""

    def get_or_start(self, language: Language, repo_root: str) -> _MissingServerLsp:
        """언어/레포 입력을 무시하고 미설치 더블을 반환한다."""
        _ = (language, repo_root)
        return _MissingServerLsp()


class _SlowLsp:
    """documentSymbol 호출이 지연되는 테스트 더블이다."""

    def request_document_symbols(self, relative_path: str) -> _FakeDocumentSymbols:
        """의도적으로 타임아웃을 유도한다."""
        _ = relative_path
        time.sleep(0.2)
        return _FakeDocumentSymbols()


class _SlowHub:
    """항상 느린 LSP를 반환하는 테스트 허브다."""

    def get_or_start(self, language: Language, repo_root: str) -> _SlowLsp:
        """언어/레포 입력을 무시하고 느린 더블을 반환한다."""
        _ = (language, repo_root)
        return _SlowLsp()


class _AssertionLsp:
    """request_document_symbols에서 AssertionError를 발생시키는 더블이다."""

    def request_document_symbols(self, relative_path: str) -> _FakeDocumentSymbols:
        _ = relative_path
        raise AssertionError("forced assertion during probe")


class _AssertionHub:
    """항상 AssertionError를 발생시키는 LSP를 반환한다."""

    def get_or_start(self, language: Language, repo_root: str) -> _AssertionLsp:
        _ = (language, repo_root)
        return _AssertionLsp()


class _GoWarmupLsp:
    """Go warm-up 호출 여부를 기록하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.requested_paths: list[str] = []
        self.request_timeout_values: list[float | None] = []

    def set_request_timeout(self, timeout: float | None) -> None:
        self.request_timeout_values.append(timeout)

    def request_document_symbols(self, relative_path: str) -> _FakeDocumentSymbols:
        self.requested_paths.append(relative_path)
        return _FakeDocumentSymbols()


class _GoWarmupHub:
    """항상 동일한 Go LSP 인스턴스를 반환한다."""

    def __init__(self, lsp: _GoWarmupLsp) -> None:
        self._lsp = lsp

    def get_or_start(self, language: Language, repo_root: str) -> _GoWarmupLsp:
        _ = (language, repo_root)
        return self._lsp


class _CaptureProbeLsp:
    """probe 요청 옵션을 캡처하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.flags: list[bool] = []

    def request_document_symbols(self, relative_path: str, *, sync_with_ls: bool = True) -> _FakeDocumentSymbols:
        _ = relative_path
        self.flags.append(bool(sync_with_ls))
        return _FakeDocumentSymbols()


class _CaptureProbeHub:
    """항상 캡처 LSP를 반환하는 허브 더블이다."""

    def __init__(self, lsp: _CaptureProbeLsp) -> None:
        self._lsp = lsp

    def get_or_start(self, language: Language, repo_root: str) -> _CaptureProbeLsp:
        _ = (language, repo_root)
        return self._lsp


def _register_repo(db_path: Path, repo_root: Path) -> None:
    """테스트 저장소를 워크스페이스에 등록한다."""
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(repo_root.resolve()),
            name=repo_root.name,
            indexed_at=None,
            is_active=True,
        )
    )


def test_language_probe_marks_missing_sample_as_unavailable(tmp_path: Path) -> None:
    """샘플 파일이 없으면 ERR_LANGUAGE_SAMPLE_NOT_FOUND로 실패해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir(parents=True, exist_ok=True)
    _register_repo(db_path=db_path, repo_root=repo_root)

    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_FakeHub(),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.PYTHON, extensions=(".py",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
    )
    result = service.run(repo_root=str(repo_root.resolve()))

    assert result["summary"]["total_languages"] == 1
    assert result["summary"]["available_languages"] == 0
    first = result["languages"][0]
    assert first["language"] == "python"
    assert first["available"] is False
    assert first["last_error_code"] == "ERR_LANGUAGE_SAMPLE_NOT_FOUND"
    assert first["provisioning_mode"] == "hybrid"
    assert isinstance(first["install_hint"], str)


def test_language_probe_reports_success_when_document_symbol_works(tmp_path: Path) -> None:
    """샘플 파일과 documentSymbol 호출이 가능하면 available=true여야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "sample.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    _register_repo(db_path=db_path, repo_root=repo_root)

    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_FakeHub(),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.PYTHON, extensions=(".py",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
    )
    result = service.run(repo_root=str(repo_root.resolve()))

    assert result["summary"]["total_languages"] == 1
    assert result["summary"]["available_languages"] == 1
    first = result["languages"][0]
    assert first["available"] is True
    assert first["last_error_code"] is None
    assert first["last_error_message"] is None
    assert first["symbol_extract_success"] is True
    assert first["document_symbol_count"] == 1
    assert first["path_mapping_ok"] is True
    assert first["timeout_occurred"] is False
    assert first["recovered_by_restart"] is False
    assert first["provisioning_mode"] == "hybrid"
    assert first["missing_dependency"] is None


def test_language_probe_requests_document_symbols_without_sync_when_supported(tmp_path: Path) -> None:
    """언어 probe는 지원 시 sync_with_ls=False로 문서 심볼을 요청해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "sample.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    _register_repo(db_path=db_path, repo_root=repo_root)

    capture_lsp = _CaptureProbeLsp()
    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_CaptureProbeHub(capture_lsp),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.PYTHON, extensions=(".py",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
    )
    result = service.run(repo_root=str(repo_root.resolve()))

    assert result["summary"]["available_languages"] == 1
    assert capture_lsp.flags == [False]


def test_language_probe_classifies_missing_server_as_explicit_error(tmp_path: Path) -> None:
    """언어 서버 미설치는 ERR_LSP_SERVER_MISSING으로 분류되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "sample.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    _register_repo(db_path=db_path, repo_root=repo_root)

    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_MissingServerHub(),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.PYTHON, extensions=(".py",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
    )
    result = service.run(repo_root=str(repo_root.resolve()))

    first = result["languages"][0]
    assert first["available"] is False
    assert first["last_error_code"] == "ERR_LSP_SERVER_MISSING"
    assert first["symbol_extract_success"] is False
    assert first["missing_dependency"] == "pyright"
    assert first["provisioning_mode"] == "hybrid"


def test_language_probe_marks_timeout_as_explicit_error(tmp_path: Path) -> None:
    """언어 probe가 제한 시간을 초과하면 ERR_LSP_TIMEOUT으로 기록해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "sample.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    _register_repo(db_path=db_path, repo_root=repo_root)

    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_SlowHub(),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.PYTHON, extensions=(".py",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
        per_language_timeout_sec=0.05,
    )
    result = service.run(repo_root=str(repo_root.resolve()))

    first = result["languages"][0]
    assert first["available"] is False
    assert first["last_error_code"] == "ERR_LSP_TIMEOUT"
    assert first["timeout_occurred"] is True


def test_language_probe_maps_assertion_error_without_crash(tmp_path: Path) -> None:
    """probe worker에서 AssertionError가 발생해도 IndexError 없이 명시 오류로 매핑해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "sample.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    _register_repo(db_path=db_path, repo_root=repo_root)

    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_AssertionHub(),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.PYTHON, extensions=(".py",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
    )
    result = service.run(repo_root=str(repo_root.resolve()))

    first = result["languages"][0]
    assert first["available"] is False
    assert first["last_error_code"] in {"ERR_LSP_PROBE_INTERNAL", "ERR_LSP_DOCUMENT_SYMBOL_FAILED"}
    assert "assertion" in str(first["last_error_message"])


def test_language_probe_applies_go_specific_timeout_override(tmp_path: Path) -> None:
    """Go는 per-language timeout override를 우선 적용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-go"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "sample.go").write_text("package main\nfunc main(){}\n", encoding="utf-8")
    _register_repo(db_path=db_path, repo_root=repo_root)

    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_SlowHub(),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.GO, extensions=(".go",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
        per_language_timeout_sec=0.05,
        per_language_timeout_overrides={"go": 0.30},
        go_warmup_enabled=False,
    )
    result = service.run(repo_root=str(repo_root.resolve()))

    first = result["languages"][0]
    assert first["available"] is True
    assert first["last_error_code"] is None


def test_language_probe_prefers_small_non_test_go_sample(tmp_path: Path) -> None:
    """Go 샘플은 작은 비테스트 파일을 우선 선택해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-go"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "z_large.go").write_text("package main\n" + ("x:=1\n" * 500), encoding="utf-8")
    (repo_root / "a_small_test.go").write_text("package main\nfunc TestX(){}\n", encoding="utf-8")
    (repo_root / "m_small.go").write_text("package main\nfunc ok(){}\n", encoding="utf-8")
    _register_repo(db_path=db_path, repo_root=repo_root)

    lsp = _GoWarmupLsp()
    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_GoWarmupHub(lsp),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.GO, extensions=(".go",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
        go_warmup_enabled=False,
    )
    _ = service.run(repo_root=str(repo_root.resolve()))

    assert len(lsp.requested_paths) == 1
    assert lsp.requested_paths[0] == "m_small.go"


def test_language_probe_go_warmup_runs_once_per_repo(tmp_path: Path) -> None:
    """Go warm-up은 repo 단위로 1회만 실행되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-go"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "sample.go").write_text("package main\nfunc main(){}\n", encoding="utf-8")
    _register_repo(db_path=db_path, repo_root=repo_root)

    lsp = _GoWarmupLsp()
    service = LanguageProbeService(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_hub=_GoWarmupHub(lsp),  # type: ignore[arg-type]
        entries=(LanguageSupportEntry(language=Language.GO, extensions=(".go",)),),
        now_provider=lambda: "2026-02-17T00:00:00+00:00",
        lsp_request_timeout_sec=20.0,
        go_warmup_timeout_sec=45.0,
    )
    _ = service.run(repo_root=str(repo_root.resolve()))
    _ = service.run(repo_root=str(repo_root.resolve()))

    # 1회차는 warm-up + probe(2회), 2회차는 probe(1회)
    assert len(lsp.requested_paths) == 3
    assert lsp.request_timeout_values == [45.0, 20.0]
