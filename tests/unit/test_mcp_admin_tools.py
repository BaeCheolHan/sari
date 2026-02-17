"""MCP 운영 도구(doctor/rescan/repo_candidates) 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer
from sari.services.workspace_service import WorkspaceService


def test_mcp_repo_candidates_returns_registered_workspace(tmp_path: Path) -> None:
    """repo_candidates는 등록된 워크스페이스를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "repo_candidates",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    payload = response.to_dict()
    result = payload["result"]
    assert result["isError"] is False
    items = result["structuredContent"]["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["repo"] == str(repo_dir.resolve())


def test_mcp_rescan_returns_invalidation_count(tmp_path: Path) -> None:
    """rescan은 invalidated_cache_rows를 구조화 응답에 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "rescan",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    payload = response.to_dict()
    result = payload["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert "invalidated_cache_rows" in structured
