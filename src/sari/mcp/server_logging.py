"""Structured MCP debug logging helpers."""

from __future__ import annotations

from typing import Callable, Mapping


def log_debug_message(debug_enabled: bool, struct_logger: object, message: str) -> None:
    if not debug_enabled:
        return
    struct_logger.debug("mcp_debug_log", message=message)


def log_debug_request(
    debug_enabled: bool,
    struct_logger: object,
    mode: str,
    req: Mapping[str, object],
    sanitize_value: Callable[[object, str], object],
) -> None:
    if not debug_enabled:
        return

    summary: dict[str, object] = {
        "id": req.get("id"),
        "method": req.get("method"),
        "mode": mode,
        "keys": sorted([k for k in req.keys() if not str(k).startswith("_")]),
    }
    params = req.get("params") or {}
    if req.get("method") == "tools/call" and isinstance(params, dict):
        args = params.get("arguments") or {}
        summary["tool"] = params.get("name")
        if isinstance(args, dict):
            summary["argument_keys"] = sorted(list(args.keys()))
            summary["arguments"] = {k: sanitize_value(v, str(k)) for k, v in args.items()}

    struct_logger.debug("mcp_request", **summary)


def log_debug_response(
    debug_enabled: bool,
    struct_logger: object,
    mode: str,
    resp: Mapping[str, object],
    sanitize_value: Callable[[object, str], object],
) -> None:
    if not debug_enabled:
        return

    summary: dict[str, object] = {
        "id": resp.get("id"),
        "mode": mode,
        "has_result": "result" in resp,
        "has_error": "error" in resp,
    }
    if "error" in resp and isinstance(resp["error"], dict):
        summary["error"] = sanitize_value(resp["error"])

    struct_logger.debug("mcp_response", **summary)
