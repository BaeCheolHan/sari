"""MCP 파이프라인 벤치마크 도구 숨김 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.mcp.server import McpServer


def test_mcp_pipeline_benchmark_run_and_report_are_removed(tmp_path: Path) -> None:
    """pipeline_benchmark_run/report 도구는 MCP 목록과 호출 경로에서 제거되어야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    list_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 80,
            "method": "tools/list",
            "params": {},
        }
    )
    list_payload = list_response.to_dict()
    tool_names = {tool.get("name") for tool in list_payload["result"].get("tools", [])}
    assert "pipeline_benchmark_run" not in tool_names
    assert "pipeline_benchmark_report" not in tool_names

    run_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 81,
            "method": "tools/call",
            "params": {
                "name": "pipeline_benchmark_run",
                "arguments": {
                    "repo": str(tmp_path.resolve()),
                    "target_files": 20,
                    "profile": "default",
                    "language_filter": ["python"],
                    "per_language_report": True,
                },
            },
        }
    )
    run_payload = run_response.to_dict()
    assert run_payload["error"]["code"] == -32601

    report_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 82,
            "method": "tools/call",
            "params": {
                "name": "pipeline_benchmark_report",
                "arguments": {"repo": str(tmp_path.resolve())},
            },
        }
    )
    report_payload = report_response.to_dict()
    assert report_payload["error"]["code"] == -32601
