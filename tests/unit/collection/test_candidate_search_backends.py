"""후보 검색 백엔드 선택/폴백 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CandidateFileDTO, WorkspaceDTO
from sari.core.language.registry import get_default_collection_extensions
import pytest

from sari.search.candidate_search import (
    CandidateBackend,
    CandidateBackendError,
    CandidateSearchConfig,
    CandidateSearchService,
    TantivyCandidateBackend,
)


class _AlwaysFailBackend(CandidateBackend):
    """항상 예외를 발생시키는 테스트용 백엔드다."""

    def __init__(self, message: str) -> None:
        """오류 메시지를 저장한다."""
        self._message = message

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> list[CandidateFileDTO]:
        """호출 시 항상 실패시킨다."""
        del workspaces, query, limit
        raise CandidateBackendError(self._message)


class _UnexpectedFailBackend(CandidateBackend):
    """예상 외 예외를 발생시키는 테스트용 백엔드다."""

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> list[CandidateFileDTO]:
        """호출 시 RuntimeError를 발생시킨다."""
        del workspaces, query, limit
        raise RuntimeError("unexpected backend failure")


class _SingleCandidateBackend(CandidateBackend):
    """고정 후보 1건을 반환하는 테스트 백엔드다."""

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> list[CandidateFileDTO]:
        """호출 시 고정 후보를 반환한다."""
        del query, limit
        repo_root = workspaces[0].path if len(workspaces) > 0 else "/tmp/noop"
        return [
            CandidateFileDTO(
                repo_root=repo_root,
                relative_path="sample.py",
                score=1.0,
                file_hash="hash-a",
            )
        ]


def test_candidate_search_returns_explicit_error_when_both_backends_fail() -> None:
    """주/보조 백엔드 모두 실패하면 명시적 오류 응답을 반환해야 한다."""
    service = CandidateSearchService(
        backend=_AlwaysFailBackend("primary failed"),
        fallback_backend=_AlwaysFailBackend("fallback failed"),
    )

    result = service.search(
        workspaces=[WorkspaceDTO(path="/tmp/noop", name=None, indexed_at=None, is_active=True)],
        query="hello",
        limit=5,
    )

    assert result.candidates == []
    assert result.source == "backend_error"
    assert len(result.errors) == 1
    assert result.errors[0].code == "ERR_CANDIDATE_BACKEND"
    assert result.errors[0].severity == "FATAL"
    assert result.errors[0].origin == "candidate"
    assert "primary failed" in result.errors[0].message
    assert "fallback failed" in result.errors[0].message


def test_candidate_search_scan_mode_finds_python_file(tmp_path: Path) -> None:
    """scan 모드 기본 서비스는 파일 스캔으로 후보를 찾는다."""
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")

    service = CandidateSearchService.build_default(
        max_file_size_bytes=512 * 1024,
        index_root=tmp_path / "candidate-index",
        backend_mode="scan",
        enable_scan_fallback=False,
    )
    result = service.search(
        workspaces=[WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-a", indexed_at=None, is_active=True)],
        query="alpha_symbol",
        limit=10,
    )

    assert result.source == "scan"
    assert len(result.candidates) == 1
    assert result.candidates[0].relative_path == "alpha.py"
    assert result.candidates[0].file_hash != ""


def test_candidate_search_scan_mode_uses_allowed_suffixes_override(tmp_path: Path) -> None:
    """허용 확장자 오버라이드가 적용되면 후보 검색 범위가 제한되어야 한다."""
    repo_dir = tmp_path / "repo-ext"
    repo_dir.mkdir()
    (repo_dir / "alpha.py").write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    (repo_dir / "beta.swift").write_text("func alpha_symbol() -> Int { 1 }\n", encoding="utf-8")

    service = CandidateSearchService.build_default(
        max_file_size_bytes=512 * 1024,
        index_root=tmp_path / "candidate-index-ext",
        backend_mode="scan",
        enable_scan_fallback=False,
        allowed_suffixes=(".py",),
    )
    result = service.search(
        workspaces=[WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-ext", indexed_at=None, is_active=True)],
        query="alpha_symbol",
        limit=10,
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].relative_path == "alpha.py"


def test_candidate_search_scan_mode_defaults_to_language_registry_extensions(tmp_path: Path) -> None:
    """허용 확장자 미지정 시 language_registry 기본 확장자를 사용해야 한다."""
    repo_dir = tmp_path / "repo-default-ext"
    repo_dir.mkdir()
    target = repo_dir / "alpha.swift"
    target.write_text("func alpha_symbol() -> Int { 1 }\n", encoding="utf-8")

    assert ".swift" in set(get_default_collection_extensions())

    service = CandidateSearchService.build_default(
        max_file_size_bytes=512 * 1024,
        index_root=tmp_path / "candidate-index-default-ext",
        backend_mode="scan",
        enable_scan_fallback=False,
        allowed_suffixes=None,
    )
    result = service.search(
        workspaces=[WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-default-ext", indexed_at=None, is_active=True)],
        query="alpha_symbol",
        limit=10,
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].relative_path == str(target.relative_to(repo_dir).as_posix())


def test_candidate_search_marks_fallback_success_as_fatal_error() -> None:
    """fallback 성공이어도 주백엔드 실패는 FATAL 오류로 남아야 한다."""
    service = CandidateSearchService(
        backend=_AlwaysFailBackend("primary failed"),
        fallback_backend=_SingleCandidateBackend(),
    )

    result = service.search(
        workspaces=[WorkspaceDTO(path="/tmp/noop", name=None, indexed_at=None, is_active=True)],
        query="hello",
        limit=5,
    )

    assert result.source == "scan_fallback"
    assert len(result.candidates) == 1
    assert len(result.errors) == 1
    assert result.errors[0].code == "ERR_CANDIDATE_BACKEND"
    assert result.errors[0].severity == "FATAL"


def test_candidate_search_does_not_swallow_unexpected_exception() -> None:
    """도메인 예외가 아닌 예외는 fallback 처리 없이 전파되어야 한다."""
    service = CandidateSearchService(backend=_UnexpectedFailBackend(), fallback_backend=None)

    with pytest.raises(RuntimeError, match="unexpected backend failure"):
        service.search(
            workspaces=[WorkspaceDTO(path="/tmp/noop", name=None, indexed_at=None, is_active=True)],
            query="hello",
            limit=5,
        )


def test_candidate_search_preserves_tantivy_lockbusy_error_code() -> None:
    """Tantivy lockbusy 오류는 전용 코드로 유지되어야 한다."""
    service = CandidateSearchService(
        backend=_AlwaysFailBackend("ERR_TANTIVY_LOCK_BUSY: lock busy"),
        fallback_backend=_SingleCandidateBackend(),
    )

    result = service.search(
        workspaces=[WorkspaceDTO(path="/tmp/noop", name=None, indexed_at=None, is_active=True)],
        query="hello",
        limit=5,
    )

    assert result.source == "scan_fallback"
    assert len(result.errors) == 1
    assert result.errors[0].code == "ERR_TANTIVY_LOCK_BUSY"
    assert result.errors[0].severity == "FATAL"


def test_filter_workspaces_by_repo_allows_descendant_repo() -> None:
    """repo가 workspace 하위 경로여도 필터에 포함되어야 한다."""
    service = CandidateSearchService(
        backend=_SingleCandidateBackend(),
        fallback_backend=None,
    )
    workspaces = [
        WorkspaceDTO(path="/tmp/ws", name="ws", indexed_at=None, is_active=True),
        WorkspaceDTO(path="/tmp/other", name="other", indexed_at=None, is_active=True),
    ]

    filtered = service.filter_workspaces_by_repo(workspaces=workspaces, repo_root="/tmp/ws/repo-a")

    assert len(filtered) == 1
    assert filtered[0].path == "/tmp/ws"


def test_tantivy_build_index_rebuilds_when_first_open_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """인덱스 오픈이 1회 실패하면 백업 후 재빌드에 성공해야 한다."""
    index_root = tmp_path / "candidate-index-init-rebuild"
    index_root.mkdir(parents=True, exist_ok=True)
    (index_root / "meta.json").write_text("broken", encoding="utf-8")
    calls = {"count": 0}

    from sari.search import candidate_search as module

    real_index = module.tantivy.Index

    def _flaky_index(schema: object, path: str) -> object:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("schema mismatch")
        return real_index(schema, path=path)

    monkeypatch.setattr(module.tantivy, "Index", _flaky_index)

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=index_root,
    )

    assert backend is not None
    assert calls["count"] >= 2
    backups = list(tmp_path.glob("candidate-index-init-rebuild.bak.*"))
    assert len(backups) == 1
    assert index_root.exists()


def test_tantivy_build_index_raises_when_rebuild_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """인덱스 오픈/재빌드가 모두 실패하면 명시 오류를 반환해야 한다."""
    index_root = tmp_path / "candidate-index-init-fail"
    index_root.mkdir(parents=True, exist_ok=True)
    (index_root / "meta.json").write_text("broken", encoding="utf-8")
    from sari.search import candidate_search as module

    def _always_fail(schema: object, path: str) -> object:
        del schema, path
        raise RuntimeError("always fail")

    monkeypatch.setattr(module.tantivy, "Index", _always_fail)

    with pytest.raises(CandidateBackendError, match="ERR_TANTIVY_INDEX_REBUILD_FAILED"):
        TantivyCandidateBackend(
            config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
            index_root=index_root,
        )
