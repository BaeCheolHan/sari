"""Request dispatch helpers for MCP server local method execution."""

from __future__ import annotations

import urllib.parse
from typing import Callable, Mapping

from sari.core.error_contract_metrics import note_unknown_tool_error


def _parse_pack1_error_line(text: str) -> tuple[str | None, str | None]:
    first_line = str(text or "").splitlines()[0].strip()
    if not first_line.startswith("PACK1 ") or " ok=false" not in first_line:
        return (None, None)

    code: str | None = None
    message: str | None = None
    for token in first_line.split():
        if token.startswith("code="):
            code = token.split("=", 1)[1].strip() or None
        elif token.startswith("msg="):
            raw = token.split("=", 1)[1]
            message = urllib.parse.unquote(raw).strip() or None
    return (code, message)


def _extract_error_fields(result: Mapping[str, object]) -> tuple[int, str, str | None]:
    err_obj = result.get("error")
    err_map = err_obj if isinstance(err_obj, Mapping) else {}
    raw_code = err_map.get("code")
    message = str(err_map.get("message") or "").strip()
    reason_code: str | None = None

    if isinstance(raw_code, int):
        return (raw_code, message or "Tool error", None)
    if raw_code is not None:
        reason_code = str(raw_code).strip() or None

    if not message:
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, Mapping):
                pack_code, pack_msg = _parse_pack1_error_line(str(first.get("text") or ""))
                if pack_code and not reason_code:
                    reason_code = pack_code
                if pack_msg:
                    message = pack_msg

    if not message:
        note_unknown_tool_error()
    return (-32000, message or "Tool error", reason_code)


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
            code, message, reason_code = _extract_error_fields(result)
            data: dict[str, object] = {"result": result}
            if reason_code:
                data["reason_code"] = reason_code
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": code,
                    "message": message,
                    "data": data,
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
