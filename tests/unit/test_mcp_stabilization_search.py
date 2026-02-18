"""MCP search stabilization 메타를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer
from sari.services.workspace_service import WorkspaceService


def test_search_includes_stabilization_meta(tmp_path: Path) -> None:
    """search 성공 응답은 meta.stabilization을 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "sample.py").write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 501,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {"repo": str(repo_dir.resolve()), "query": "alpha_symbol", "limit": 5, "options": {"structured": 1}},
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    meta = payload["result"]["structuredContent"]["meta"]
    assert "stabilization" in meta
    stabilization = meta["stabilization"]
    assert isinstance(stabilization["metrics_snapshot"], dict)
    assert isinstance(stabilization["next_calls"], list)
    assert isinstance(stabilization["bundle_id"], str)
