"""MCP read stabilization 메타를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer


def test_read_diff_preview_includes_stabilization_meta(tmp_path: Path) -> None:
    """read(diff_preview) 성공 응답은 stabilization 메타를 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    target_file = repo_path / "main.py"
    target_file.write_text("print('a')\n", encoding="utf-8")
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=str(repo_path), name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 601,
            "method": "tools/call",
            "params": {
                    "name": "read",
                    "arguments": {
                        "repo": str(repo_path),
                        "mode": "diff_preview",
                        "target": "main.py",
                        "content": "print('b')\n",
                        "options": {"structured": 1},
                    },
                },
            }
        )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    meta = payload["result"]["structuredContent"]["meta"]
    stabilization = meta["stabilization"]
    assert stabilization["budget_state"] == "NORMAL"
    assert isinstance(stabilization["reason_codes"], list)
    assert isinstance(stabilization["evidence_refs"], list)
    assert len(stabilization["evidence_refs"]) == 1
