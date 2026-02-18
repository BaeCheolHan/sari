"""후보 검색 백엔드 선택/폴백 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CandidateFileDTO, WorkspaceDTO
import pytest

from sari.search.candidate_search import CandidateBackend, CandidateBackendError, CandidateSearchService


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
