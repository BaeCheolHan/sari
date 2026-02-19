"""MCP 파이프라인 성능 도구 숨김 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.mcp.server import McpServer


def test_mcp_pipeline_perf_run_and_report_are_hidden(tmp_path: Path) -> None:
    """pipeline_perf_run/report 도구는 MCP에서 숨김 처리되어야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")
    run_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 91,
            "method": "tools/call",
            "params": {
                "name": "pipeline_perf_run",
                "arguments": {
                    "repo": str(tmp_path.resolve()),
                    "target_files": 2000,
                    "profile": "realistic_v1",
                },
            },
        }
    )
    run_payload = run_response.to_dict()
    assert run_payload["error"]["code"] == -32601

    report_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 92,
            "method": "tools/call",
            "params": {
                "name": "pipeline_perf_report",
                "arguments": {"repo": str(tmp_path.resolve())},
            },
        }
    )
    report_payload = report_response.to_dict()
    assert report_payload["error"]["code"] == -32601
