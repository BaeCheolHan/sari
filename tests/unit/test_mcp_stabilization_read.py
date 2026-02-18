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
    repo_root = str(repo_path.resolve())
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)
    scan_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6011,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": repo_root},
            },
        }
    ).to_dict()
    assert scan_response["result"]["isError"] is False

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


def test_read_normalizes_mode_and_path_alias(tmp_path: Path) -> None:
    """read는 file_preview/path 별칭 입력을 file/target으로 정규화해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    target_file = repo_path / "main.py"
    target_file.write_text("print('a')\n", encoding="utf-8")
    repo_root = str(repo_path.resolve())
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)
    scan_payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6011,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": repo_root, "options": {"structured": 1}},
            },
        }
    ).to_dict()
    assert scan_payload["result"]["isError"] is False

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 602,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {
                    "repo": repo_root,
                    "mode": "file_preview",
                    "path": "main.py",
                    "offset": 0,
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    items = payload["result"]["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["relative_path"] == "main.py"


def test_read_missing_target_returns_self_describing_error(tmp_path: Path) -> None:
    """read(file) target 누락 오류는 expected/example 힌트를 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    repo_root = str(repo_path.resolve())
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 603,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {
                    "repo": repo_root,
                    "mode": "file",
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    error = payload["result"]["structuredContent"]["error"]
    assert error["code"] == "ERR_TARGET_REQUIRED"
    assert "expected" in error
    assert "example" in error
    text = payload["result"]["content"][0]["text"]
    assert "@HINT expected=" in text
