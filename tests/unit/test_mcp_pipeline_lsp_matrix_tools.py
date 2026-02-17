"""MCP LSP 매트릭스 도구를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer
from sari.services.workspace_service import WorkspaceService


def test_mcp_pipeline_lsp_matrix_run_and_report(tmp_path: Path) -> None:
    """pipeline_lsp_matrix_run/report 도구가 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)

    run_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 91,
            "method": "tools/call",
            "params": {
                "name": "pipeline_lsp_matrix_run",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "required_languages": ["python"],
                    "fail_on_unavailable": False,
                    "strict_all_languages": False,
                    "strict_symbol_gate": False,
                },
            },
        }
    )
    run_payload = run_response.to_dict()
    assert run_payload["result"]["isError"] is False
    run_item = run_payload["result"]["structuredContent"]["items"][0]
    assert "gate" in run_item
    assert run_item["gate"]["required_languages"] == ["python"]
    assert "gate_decision" in run_item["gate"]
    assert "readiness_percent" in run_item["summary"]
    assert run_item["gate"]["strict_symbol_gate"] is False
    assert "missing_server_languages" in run_item["summary"]

    report_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 92,
            "method": "tools/call",
            "params": {
                "name": "pipeline_lsp_matrix_report",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    report_payload = report_response.to_dict()
    assert report_payload["result"]["isError"] is False
    items = report_payload["result"]["structuredContent"]["items"]
    assert isinstance(items, list)
    assert len(items) == 1
