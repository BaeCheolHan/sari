"""MCP search 도구 pack1 계약 스냅샷을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path

from sari.mcp.server import McpServer
from sari.services.workspace_service import WorkspaceService
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema


def test_mcp_search_pack1_snapshot(tmp_path: Path) -> None:
    """search 도구 응답이 pack1 필수 키셋을 충족하는지 검증한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "sample.py").write_text("def demo_symbol():\n    return 1\n", encoding="utf-8")

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "query": "demo_symbol",
                    "limit": 5,
                },
            },
        }
    )
    payload = response.to_dict()

    snapshot_path = Path(__file__).resolve().parents[1] / "snapshots" / "search_pack1_expected.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    result = payload["result"]
    assert isinstance(result, dict)

    for key in snapshot["required_top_keys"]:
        assert key in result

    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    for key in snapshot["required_structured_keys"]:
        assert key in structured

    meta = structured["meta"]
    assert isinstance(meta, dict)
    for key in snapshot["required_meta_keys"]:
        assert key in meta

    items = structured["items"]
    assert isinstance(items, list)
    if len(items) > 0:
        first = items[0]
        assert isinstance(first, dict)
        for key in snapshot["required_item_keys"]:
            assert key in first
