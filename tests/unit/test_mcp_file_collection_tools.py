"""MCP 파일 수집 도구(scan_once/list_files/read_file/index_file)를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.mcp.server import McpServer
from sari.services.workspace_service import WorkspaceService


def test_mcp_tools_list_includes_file_collection_tools(tmp_path: Path) -> None:
    """tools/list 응답은 파일 수집 도구 4종을 포함해야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    list_response = server.handle_request({"jsonrpc": "2.0", "id": 21, "method": "tools/list"})
    payload = list_response.to_dict()
    tools = payload["result"]["tools"]
    names = {tool["name"] for tool in tools}

    assert "scan_once" in names
    assert "list_files" in names
    assert "read_file" in names
    assert "index_file" in names


def test_mcp_file_collection_scan_list_read_flow(tmp_path: Path) -> None:
    """scan_once 이후 list_files/read_file가 pack1 성공 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha():\n    return 7\n", encoding="utf-8")
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)

    scan_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    scan_payload = scan_response.to_dict()
    assert scan_payload["result"]["isError"] is False

    list_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 23,
            "method": "tools/call",
            "params": {
                "name": "list_files",
                "arguments": {"repo": str(repo_dir.resolve()), "limit": 10},
            },
        }
    )
    list_payload = list_response.to_dict()
    assert list_payload["result"]["isError"] is False
    items = list_payload["result"]["structuredContent"]["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["relative_path"] == "alpha.py"

    read_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 24,
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "relative_path": "alpha.py",
                    "offset": 0,
                    "limit": 20,
                },
            },
        }
    )
    read_payload = read_response.to_dict()
    assert read_payload["result"]["isError"] is False
    read_items = read_payload["result"]["structuredContent"]["items"]
    assert isinstance(read_items, list)
    assert len(read_items) == 1
    assert "alpha" in read_items[0]["content"]


def test_mcp_index_file_requires_relative_path(tmp_path: Path) -> None:
    """index_file은 relative_path 누락 시 명시적 오류를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 25,
            "method": "tools/call",
            "params": {
                "name": "index_file",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["code"] == "ERR_RELATIVE_PATH_REQUIRED"


def test_mcp_read_file_returns_explicit_error_for_corrupted_l2_body(tmp_path: Path) -> None:
    """L2 본문 손상 시 read_file은 명시적 오류 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-corrupt"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha():\n    return 7\n", encoding="utf-8")
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)

    scan_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 210,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    assert scan_response.to_dict()["result"]["isError"] is False

    with connect(db_path) as conn:
        file_row = conn.execute(
            """
            SELECT content_hash
            FROM collected_files_l1
            WHERE repo_root = :repo_root
              AND relative_path = :relative_path
            """,
            {"repo_root": str(repo_dir.resolve()), "relative_path": "alpha.py"},
        ).fetchone()
        assert file_row is not None

        conn.execute(
            """
            INSERT INTO collected_file_bodies_l2(
                repo_root, relative_path, content_hash, content_zlib, content_len,
                normalized_text, created_at, updated_at
            )
            VALUES(
                :repo_root, :relative_path, :content_hash, :content_zlib, :content_len,
                :normalized_text, :created_at, :updated_at
            )
            """,
            {
                "repo_root": str(repo_dir.resolve()),
                "relative_path": "alpha.py",
                "content_hash": str(file_row["content_hash"]),
                "content_zlib": b"corrupted-zlib",
                "content_len": 25,
                "normalized_text": "broken",
                "created_at": "2026-02-16T00:00:00+00:00",
                "updated_at": "2026-02-16T00:00:00+00:00",
            },
        )
        conn.commit()

    read_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 211,
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "relative_path": "alpha.py",
                    "offset": 0,
                    "limit": 20,
                },
            },
        }
    )
    payload = read_response.to_dict()
    assert payload["result"]["isError"] is True
    error = payload["result"]["structuredContent"]["meta"]["errors"][0]
    assert error["code"] == "ERR_L2_BODY_CORRUPT"
