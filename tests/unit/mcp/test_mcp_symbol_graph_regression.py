"""MCP symbol graph 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.mcp.server import McpServer
from sari.services.workspace.service import WorkspaceService
from .fixtures.graph_regression_fixture import build_graph_regression_fixture


def _upsert_repo_identity(db_path: Path, *, repo_id: str, repo_root: str, repo_label: str) -> None:
    """repo_id 정합성 게이트를 통과하기 위한 repositories 행을 준비한다."""
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repositories(repo_id, repo_label, repo_root, workspace_root, updated_at, is_active)
            VALUES(:repo_id, :repo_label, :repo_root, :workspace_root, '2026-03-05T00:00:00Z', 1)
            ON CONFLICT(repo_id) DO UPDATE SET
                repo_label = excluded.repo_label,
                repo_root = excluded.repo_root,
                workspace_root = excluded.workspace_root,
                updated_at = excluded.updated_at,
                is_active = 1
            """,
            {
                "repo_id": repo_id,
                "repo_label": repo_label,
                "repo_root": repo_root,
                "workspace_root": repo_root,
            },
        )
        conn.commit()


def test_fixture_builds_symbol_and_relation_baseline(tmp_path: Path) -> None:
    fixture = build_graph_regression_fixture(str(tmp_path.resolve()))
    assert fixture.repo_root != ""
    assert len(fixture.symbols) > 0
    assert len(fixture.relations) > 0


def test_get_callers_matches_suffix_when_relation_has_qualified_target(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir.resolve()))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    fixture = build_graph_regression_fixture(str(repo_dir.resolve()))
    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        repo_root=fixture.repo_root,
        relative_path=fixture.relative_path,
        content_hash=fixture.content_hash,
        symbols=fixture.symbols,
        created_at="2026-03-24T00:00:00+00:00",
    )
    lsp_repo.replace_relations(
        repo_root=fixture.repo_root,
        relative_path=fixture.relative_path,
        content_hash=fixture.content_hash,
        relations=fixture.relations,
        created_at="2026-03-24T00:00:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3001,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "replace_file_data_many",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    items = payload["result"]["structuredContent"]["items"]
    assert len(items) >= 1
    assert items[0]["from_symbol"] == "run_installed_freshdb_smoke"


def test_call_graph_matches_get_callers_for_suffix_matched_symbol(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir.resolve()))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    fixture = build_graph_regression_fixture(str(repo_dir.resolve()))
    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        repo_root=fixture.repo_root,
        relative_path=fixture.relative_path,
        content_hash=fixture.content_hash,
        symbols=fixture.symbols,
        created_at="2026-03-24T00:00:00+00:00",
    )
    lsp_repo.replace_relations(
        repo_root=fixture.repo_root,
        relative_path=fixture.relative_path,
        content_hash=fixture.content_hash,
        relations=fixture.relations,
        created_at="2026-03-24T00:00:00+00:00",
    )

    server = McpServer(db_path=db_path)
    callers_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3002,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "replace_file_data_many",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    ).to_dict()
    graph_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3003,
            "method": "tools/call",
            "params": {
                "name": "call_graph",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "replace_file_data_many",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    ).to_dict()

    callers_items = callers_response["result"]["structuredContent"]["items"]
    graph = graph_response["result"]["structuredContent"]["items"][0]
    assert len(callers_items) >= 1
    assert graph["caller_count"] >= len(callers_items)
