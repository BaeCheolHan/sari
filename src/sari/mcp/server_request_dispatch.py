"""Request dispatch helpers for MCP server local method execution."""

from __future__ import annotations

from typing import Callable, Mapping


def execute_local_method(
    *,
    method: object,
    params: Mapping[str, object],
    msg_id: object,
    handle_tools_call: Callable[[Mapping[str, object]], object],
    dispatch_methods: Mapping[str, Callable[[Mapping[str, object]], object]],
) -> dict[str, object]:
    if method == "tools/call":
        result = handle_tools_call(params)
        if isinstance(result, dict) and result.get("isError"):
            err = result.get("error", {})
            err_map = err if isinstance(err, Mapping) else {}
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": err_map.get("code", -32000),
                    "message": err_map.get("message", "Unknown tool error"),
                    "data": result,
                },
            }
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    handler = dispatch_methods.get(str(method))
    if handler is None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    result = handler(params)
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}
