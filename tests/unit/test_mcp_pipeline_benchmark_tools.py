"""MCP 파이프라인 벤치마크 도구를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer
from sari.services.workspace_service import WorkspaceService


def test_mcp_pipeline_benchmark_run_and_report(tmp_path: Path) -> None:
    """pipeline_benchmark_run/report 도구가 성공 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    run_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 81,
            "method": "tools/call",
            "params": {
                "name": "pipeline_benchmark_run",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "target_files": 20,
                    "profile": "default",
                    "language_filter": ["python"],
                    "per_language_report": True,
                },
            },
        }
    )
    run_payload = run_response.to_dict()
    assert run_payload["result"]["isError"] is False
    run_item = run_payload["result"]["structuredContent"]["items"][0]
    assert run_item["language_filter"] == ["python"]
    assert run_item["per_language_report"] is True

    report_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 82,
            "method": "tools/call",
            "params": {
                "name": "pipeline_benchmark_report",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    report_payload = report_response.to_dict()
    assert report_payload["result"]["isError"] is False
    items = report_payload["result"]["structuredContent"]["items"]
    assert isinstance(items, list)
    assert len(items) == 1
