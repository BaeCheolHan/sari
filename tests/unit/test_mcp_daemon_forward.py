"""MCP daemon forward 경로를 검증한다."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from sari.mcp.server import McpServer


def test_mcp_tools_call_forwards_to_daemon_when_enabled(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """환경변수 활성화 시 tools/call은 daemon forward 응답을 사용해야 한다."""
    monkeypatch.setenv("SARI_MCP_FORWARD_TO_DAEMON", "1")
    server = McpServer(db_path=tmp_path / "state.db")

    monkeypatch.setattr("sari.mcp.server.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr(
        "sari.mcp.server.forward_once",
        lambda request, host, port, timeout_sec: {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"isError": False, "content": [], "structuredContent": {"items": [], "meta": {"errors": []}}},
        },
    )

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"repo": "/repo", "query": "abc", "limit": 5}},
        }
    )
    payload = response.to_dict()
    assert "error" not in payload
    assert payload["result"]["isError"] is False


def test_mcp_forward_failure_returns_jsonrpc_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """daemon forward 실패 시 JSON-RPC error를 반환해야 한다."""
    monkeypatch.setenv("SARI_MCP_FORWARD_TO_DAEMON", "1")
    server = McpServer(db_path=tmp_path / "state.db")
    monkeypatch.setattr("sari.mcp.server.resolve_target", lambda *_: ("127.0.0.1", 47777))

    def _raise_error(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        del request, host, port, timeout_sec
        raise OSError("connection refused")

    monkeypatch.setattr("sari.mcp.server.forward_once", _raise_error)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"repo": "/repo", "query": "abc", "limit": 5}},
        }
    )
    payload = response.to_dict()
    assert payload["error"]["code"] == -32002
    assert str(payload["error"]["message"]).startswith("ERR_DAEMON_FORWARD_")


def test_mcp_tools_list_forwards_to_daemon_when_enabled(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """환경변수 활성화 시 tools/list도 daemon forward 경로를 사용해야 한다."""
    monkeypatch.setenv("SARI_MCP_FORWARD_TO_DAEMON", "1")
    server = McpServer(db_path=tmp_path / "state.db")

    monkeypatch.setattr("sari.mcp.server.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr(
        "sari.mcp.server.forward_once",
        lambda request, host, port, timeout_sec: {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"tools": [{"name": "search"}]},
        },
    )

    response = server.handle_request({"jsonrpc": "2.0", "id": 102, "method": "tools/list"})
    payload = response.to_dict()
    assert "error" not in payload
    assert payload["result"]["tools"][0]["name"] == "search"


def test_mcp_forward_auto_start_retries_once(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """forward 1차 실패 시 자동기동 후 1회 재시도해야 한다."""
    monkeypatch.setenv("SARI_MCP_FORWARD_TO_DAEMON", "1")
    server = McpServer(db_path=tmp_path / "state.db")
    monkeypatch.setattr("sari.mcp.server.resolve_target", lambda *_: ("127.0.0.1", 47777))
    state: dict[str, int] = {"calls": 0}

    def _fake_forward(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (host, port, timeout_sec)
        state["calls"] += 1
        if state["calls"] == 1:
            raise OSError("connection refused")
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"isError": False}}

    monkeypatch.setattr("sari.mcp.server.forward_once", _fake_forward)
    server._daemon_start_fn = lambda db_path, workspace_root: True  # type: ignore[assignment]

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"repo": "/repo", "query": "abc", "limit": 5}},
        }
    )
    payload = response.to_dict()
    assert "error" not in payload
    assert payload["result"]["isError"] is False
    assert state["calls"] == 2
