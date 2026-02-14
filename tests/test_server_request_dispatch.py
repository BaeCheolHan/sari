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
    assert resp["error"]["data"]["result"]["isError"] is True


def test_execute_local_method_parses_pack1_error_message():
    resp = execute_local_method(
        method="tools/call",
        params={"name": "read"},
        msg_id=11,
        handle_tools_call=lambda _p: {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": "PACK1 tool=read ok=false code=SEARCH_REF_REQUIRED msg=Read%20requires%20candidate_id%20from%20SARI_NEXT.",
                }
            ],
        },
        dispatch_methods={},
    )
    assert resp["id"] == 11
    assert resp["error"]["code"] == -32000
    assert resp["error"]["message"] == "Read requires candidate_id from SARI_NEXT."
    assert resp["error"]["data"]["reason_code"] == "SEARCH_REF_REQUIRED"


def test_execute_local_method_string_error_code_falls_back_to_numeric_jsonrpc_code():
    resp = execute_local_method(
        method="tools/call",
        params={"name": "read"},
        msg_id=12,
        handle_tools_call=lambda _p: {
            "isError": True,
            "error": {
                "code": "SEARCH_REF_REQUIRED",
                "message": "Read requires candidate_id",
            },
        },
        dispatch_methods={},
    )
    assert resp["id"] == 12
    assert resp["error"]["code"] == -32000
    assert resp["error"]["message"] == "Read requires candidate_id"
    assert resp["error"]["data"]["reason_code"] == "SEARCH_REF_REQUIRED"


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
