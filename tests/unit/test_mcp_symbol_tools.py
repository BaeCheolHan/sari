"""MCP 심볼/호출자 도구(search_symbol/get_callers)를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer
from sari.services.workspace_service import WorkspaceService


def test_mcp_tools_list_includes_symbol_tools(tmp_path: Path) -> None:
    """tools/list 응답은 search_symbol/get_callers를 포함해야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    list_response = server.handle_request({"jsonrpc": "2.0", "id": 31, "method": "tools/list"})
    payload = list_response.to_dict()
    names = {tool["name"] for tool in payload["result"]["tools"]}

    assert "search_symbol" in names
    assert "get_callers" in names


def test_mcp_search_symbol_returns_indexed_symbols(tmp_path: Path) -> None:
    """search_symbol은 LSP 심볼 인덱스 결과를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/main.py",
        content_hash="h1",
        symbols=[{"name": "AuthService", "kind": "Class", "line": 10, "end_line": 40}],
        created_at="2026-02-16T08:30:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 32,
            "method": "tools/call",
            "params": {
                "name": "search_symbol",
                    "arguments": {
                        "repo": str(repo_dir.resolve()),
                        "query": "Auth",
                        "limit": 10,
                        "options": {"structured": 1},
                    },
                },
            }
        )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    items = payload["result"]["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["name"] == "AuthService"
    assert items[0]["relative_path"] == "src/main.py"


def test_mcp_get_callers_returns_relation_edges(tmp_path: Path) -> None:
    """get_callers는 호출 관계 인덱스 결과를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/main.py",
        content_hash="h1",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 21}],
        created_at="2026-02-16T08:30:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 33,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                    "arguments": {
                        "repo": str(repo_dir.resolve()),
                        "symbol": "AuthService.login",
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
    assert items[0]["from_symbol"] == "AuthController.login"
    assert items[0]["to_symbol"] == "AuthService.login"


def test_mcp_get_callers_requires_symbol_or_symbol_id(tmp_path: Path) -> None:
    """get_callers는 symbol 또는 symbol_id 중 하나를 요구해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 34,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                    "arguments": {
                        "repo": str(repo_dir.resolve()),
                        "options": {"structured": 1},
                    },
                },
            }
        )
    payload = response.to_dict()

    assert payload["result"]["isError"] is True
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["code"] == "ERR_SYMBOL_REQUIRED"
