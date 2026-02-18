"""언어별 LSP readiness probe 서비스를 검증한다."""

from __future__ import annotations

from pathlib import Path
import time

from sari.core.language_registry import LanguageSupportEntry
from sari.core.models import WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.language_probe_service import LanguageProbeService
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
