"""Workspace 도메인 포트 정의."""

from __future__ import annotations

from typing import Protocol

from sari.core.models import WorkspaceDTO


class WorkspaceRepositoryPort(Protocol):
    """워크스페이스 저장소 인터페이스."""

    def add(self, workspace: WorkspaceDTO) -> None:
        """워크스페이스를 추가한다."""
        ...

    def get_by_path(self, path: str) -> WorkspaceDTO | None:
        """경로로 워크스페이스를 조회한다."""
        ...

    def list_all(self) -> list[WorkspaceDTO]:
        """전체 워크스페이스를 조회한다."""
        ...

    def remove(self, path: str) -> None:
        """워크스페이스를 삭제한다."""
        ...

    def set_active(self, path: str, is_active: bool) -> None:
        """워크스페이스 활성 상태를 변경한다."""
        ...

