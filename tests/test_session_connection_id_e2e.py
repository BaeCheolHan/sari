from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sari.mcp.server import LocalSearchMCPServer
from sari.mcp.session import Session


@pytest.mark.asyncio
async def test_session_tools_call_injects_server_connection_id():
    reader = MagicMock()
    writer = MagicMock()
    session = Session(reader, writer)
    session.workspace_root = "/tmp/ws"
    session.registry.touch_workspace = MagicMock()

    seen: dict[str, object] = {}

    def _handle_request(req):
        seen["request"] = req
        return {"jsonrpc": "2.0", "id": req.get("id"), "result": {"ok": True}}

    session.shared_state = SimpleNamespace(server=SimpleNamespace(handle_request=_handle_request))
    session.send_json = AsyncMock()

    await session.process_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "abc"}},
        }
    )

    forwarded = seen["request"]
    assert forwarded["params"]["arguments"]["connection_id"] == session.connection_id
    session.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_tools_call_overwrites_client_connection_id():
    reader = MagicMock()
    writer = MagicMock()
    session = Session(reader, writer)
    session.workspace_root = "/tmp/ws"
    session.registry.touch_workspace = MagicMock()

    seen: dict[str, object] = {}

    def _handle_request(req):
        seen["request"] = req
        return {"jsonrpc": "2.0", "id": req.get("id"), "result": {"ok": True}}

    session.shared_state = SimpleNamespace(server=SimpleNamespace(handle_request=_handle_request))
    session.send_json = AsyncMock()

    await session.process_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {"query": "abc", "connection_id": "spoofed"},
            },
        }
    )

    forwarded = seen["request"]
    assert forwarded["params"]["arguments"]["connection_id"] == session.connection_id


def test_server_handle_tools_call_overwrites_connection_id():
    cfg = SimpleNamespace(workspace_roots=["/tmp/ws"])
    server = LocalSearchMCPServer(
        "/tmp/ws",
        cfg=cfg,
        db=MagicMock(),
        indexer=MagicMock(),
        start_worker=False,
    )
    server._middlewares = []

    def _execute(_name, _ctx, args):
        return {"ok": True, "args": dict(args)}

    server._tool_registry.execute = _execute
    result = server.handle_tools_call(
        {
            "name": "search",
            "arguments": {"query": "abc", "connection_id": "spoofed"},
        }
    )
    assert result["ok"] is True
    assert result["args"]["connection_id"] == server._server_connection_id
    server.shutdown()
