"""워크스페이스 도메인 서비스를 구현한다."""

from pathlib import Path
from typing import Protocol

from sari.core.exceptions import ErrorContext, WorkspaceError
from sari.core.models import WorkspaceDTO


class WorkspaceRepositoryProtocol(Protocol):
    """워크스페이스 저장소 인터페이스를 정의한다."""

    def add(self, workspace: WorkspaceDTO) -> None:
        """워크스페이스를 추가한다."""

    def get_by_path(self, path: str) -> WorkspaceDTO | None:
        """경로로 워크스페이스를 조회한다."""

    def list_all(self) -> list[WorkspaceDTO]:
        """전체 워크스페이스를 조회한다."""

    def remove(self, path: str) -> None:
        """워크스페이스를 삭제한다."""

    def set_active(self, path: str, is_active: bool) -> None:
        """워크스페이스 활성 상태를 변경한다."""


class WorkspaceService:
    """워크스페이스 규칙을 담당한다."""

    def __init__(self, repository: WorkspaceRepositoryProtocol) -> None:
        """서비스 생성 시 저장소 의존성을 주입한다."""
        self._repository = repository

    def add_workspace(self, path: str, name: str | None = None) -> WorkspaceDTO:
        """워크스페이스를 검증 후 등록한다."""
        normalized_path = str(Path(path).expanduser().resolve())
        if not Path(normalized_path).exists():
            raise WorkspaceError(ErrorContext(code="ERR_WORKSPACE_NOT_FOUND", message="존재하지 않는 경로입니다"))
        if not Path(normalized_path).is_dir():
            raise WorkspaceError(ErrorContext(code="ERR_WORKSPACE_NOT_DIR", message="디렉터리 경로만 등록할 수 있습니다"))
        if self._repository.get_by_path(normalized_path) is not None:
            raise WorkspaceError(ErrorContext(code="ERR_WORKSPACE_DUPLICATE", message="이미 등록된 워크스페이스입니다"))

        workspace = WorkspaceDTO(path=normalized_path, name=name, indexed_at=None, is_active=True)
        self._repository.add(workspace)
        return workspace

    def list_workspaces(self) -> list[WorkspaceDTO]:
        """등록된 워크스페이스 목록을 반환한다."""
        return self._repository.list_all()

    def remove_workspace(self, path: str) -> None:
        """워크스페이스를 삭제한다."""
        normalized_path = str(Path(path).expanduser().resolve())
        self._repository.remove(normalized_path)

    def set_workspace_active(self, path: str, is_active: bool) -> WorkspaceDTO:
        """워크스페이스 활성 상태를 변경한다."""
        normalized_path = str(Path(path).expanduser().resolve())
        workspace = self._repository.get_by_path(normalized_path)
        if workspace is None:
            raise WorkspaceError(ErrorContext(code="ERR_WORKSPACE_NOT_FOUND", message="존재하지 않는 경로입니다"))
        self._repository.set_active(normalized_path, is_active)
        updated = self._repository.get_by_path(normalized_path)
        if updated is None:
            raise WorkspaceError(ErrorContext(code="ERR_WORKSPACE_NOT_FOUND", message="존재하지 않는 경로입니다"))
        return updated
