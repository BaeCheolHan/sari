"""read 도구 의존성 포트(Protocol) 정의."""

from __future__ import annotations

from typing import Protocol

from sari.core.models import SnippetRecordDTO, SymbolSearchItemDTO, WorkspaceDTO


class ReadWorkspacePort(Protocol):
    """read에서 사용하는 워크스페이스 조회 포트."""

    def get_by_path(self, path: str) -> WorkspaceDTO | None:
        """경로 기준 워크스페이스를 조회한다."""
        ...


class ReadSymbolPort(Protocol):
    """read(symbol)의 LSP 심볼 조회 포트."""

    def search_symbols(
        self,
        repo_root: str,
        query: str,
        limit: int,
        path_prefix: str | None = None,
    ) -> list[SymbolSearchItemDTO]:
        """심볼 검색 결과를 조회한다."""
        ...


class ReadKnowledgePort(Protocol):
    """read(snippet)의 스니펫 조회 포트."""

    def query_snippets(self, repo_root: str, tag: str | None, query: str | None, limit: int) -> list[SnippetRecordDTO]:
        """스니펫 목록을 조회한다."""
        ...


class ReadLayerSymbolPort(Protocol):
    """read(symbol)의 L3 레이어 심볼 조회 포트."""

    def search_l3_symbols(
        self,
        *,
        workspace_id: str,
        repo_root: str,
        query: str,
        limit: int,
        path_prefix: str | None = None,
    ) -> list[dict[str, object]]:
        """L3 스냅샷 기반 심볼 목록을 조회한다."""
        ...
