from __future__ import annotations

from sari.mcp.server_request_dispatch import execute_local_method


def test_execute_local_method_maps_tool_is_error_to_jsonrpc_error():
    resp = execute_local_method(
        method="tools/call",
        params={"name": "x"},
        msg_id=7,
        handle_tools_call=lambda _p: {
            "isError": True,
            "error": {"code": -32042, "message": "boom"},
        },
        dispatch_methods={},
    )
    assert resp["id"] == 7
    assert resp["error"]["code"] == -32042
    assert resp["error"]["message"] == "boom"


def test_execute_local_method_returns_method_not_found():
    resp = execute_local_method(
        method="unknown/method",
        params={},
        msg_id=9,
        handle_tools_call=lambda _p: {},
        dispatch_methods={},
    )
    assert resp["error"]["code"] == -32601


def test_execute_local_method_wraps_handler_result():
    resp = execute_local_method(
        method="ping",
        params={},
        msg_id=3,
        handle_tools_call=lambda _p: {},
        dispatch_methods={"ping": lambda _p: {"ok": True}},
    )
    assert resp == {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
