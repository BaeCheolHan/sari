# tests/test_workspace_service.py
import pytest
import os
from pathlib import Path
from sari.services.workspace_service import WorkspaceService
from sari.core.exceptions import WorkspaceError
from sari.core.models import Workspace

class MockWorkspaceRepository:
    """테스트를 위한 인메모리 저장소"""
    def __init__(self) -> None:
        self.workspaces: dict[str, Workspace] = {}

    def add(self, workspace: Workspace) -> None:
        self.workspaces[workspace.path] = workspace

    def get_by_path(self, path: str) -> Workspace | None:
        return self.workspaces.get(path)

    def list_all(self) -> list[Workspace]:
        return list(self.workspaces.values())

    def remove(self, path: str) -> None:
        if path in self.workspaces:
            del self.workspaces[path]

@pytest.fixture
def workspace_service() -> WorkspaceService:
    repo = MockWorkspaceRepository()
    return WorkspaceService(repository=repo)

def test_add_workspace_success(workspace_service: WorkspaceService, tmp_path: Path) -> None:
    # Given: 유효한 디렉토리 경로
    path = str(tmp_path / "repo-a")
    os.mkdir(path)
    
    # When: 워크스페이스 추가
    workspace_service.add_workspace(path)
    
    # Then: 등록 확인
    workspaces = workspace_service.list_workspaces()
    assert len(workspaces) == 1
    assert workspaces[0].path == path

def test_add_workspace_non_existent_path(workspace_service: WorkspaceService) -> None:
    # Given: 존재하지 않는 경로
    path = "/non/existent/path"
    
    # When & Then: WorkspaceError 발생 확인
    with pytest.raises(WorkspaceError, match="존재하지 않는 경로입니다"):
        workspace_service.add_workspace(path)

def test_add_workspace_duplicate_path(workspace_service: WorkspaceService, tmp_path: Path) -> None:
    # Given: 이미 등록된 경로
    path = str(tmp_path / "repo-b")
    os.mkdir(path)
    workspace_service.add_workspace(path)
    
    # When & Then: 중복 등록 시 WorkspaceError 발생 확인
    with pytest.raises(WorkspaceError, match="이미 등록된 워크스페이스입니다"):
        workspace_service.add_workspace(path)
