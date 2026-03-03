"""MCP 파이프라인 운영 도구 숨김 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.mcp.server import McpServer


def test_mcp_pipeline_policy_get_is_hidden(tmp_path: Path) -> None:
    """pipeline_policy_get 도구는 MCP에서 숨김 처리되어야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 70,
            "method": "tools/call",
            "params": {
                "name": "pipeline_policy_get",
                "arguments": {"repo": str(tmp_path.resolve())},
            },
        }
    )
    payload = response.to_dict()
    assert payload["error"]["code"] == -32601


def test_mcp_pipeline_dead_requeue_is_hidden(tmp_path: Path) -> None:
    """pipeline_dead_requeue 도구는 MCP에서 숨김 처리되어야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 71,
            "method": "tools/call",
            "params": {
                "name": "pipeline_dead_requeue",
                "arguments": {},
            },
        }
    )
    payload = response.to_dict()
    assert payload["error"]["code"] == -32601


def test_mcp_pipeline_auto_status_is_hidden(tmp_path: Path) -> None:
    """pipeline_auto_status 도구는 MCP에서 숨김 처리되어야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 72,
            "method": "tools/call",
            "params": {
                "name": "pipeline_auto_status",
                "arguments": {"repo": str(tmp_path.resolve())},
            },
        }
    )
    payload = response.to_dict()
    assert payload["error"]["code"] == -32601


def test_mcp_pipeline_dead_requeue_all_is_hidden(tmp_path: Path) -> None:
    """pipeline_dead_requeue all 플래그 경로도 MCP에서 차단되어야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 73,
            "method": "tools/call",
            "params": {
                "name": "pipeline_dead_requeue",
                "arguments": {"repo": str(tmp_path.resolve()), "all": True, "limit": 1},
            },
        }
    )
    payload = response.to_dict()
    assert payload["error"]["code"] == -32601
