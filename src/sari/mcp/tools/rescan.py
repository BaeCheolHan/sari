#!/usr/bin/env python3
"""
Rescan tool for Local Search MCP Server.
"""
from typing import Any, Dict
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_error, ErrorCode
from sari.core.indexer import Indexer
from sari.core.services.index_service import IndexService


def execute_rescan(args: Dict[str, Any], indexer: Indexer) -> Dict[str, Any]:
    """Trigger async rescan on indexer."""
    svc = IndexService(indexer)
    result = svc.rescan()
    if not result.get("ok"):
        code = result.get("code", ErrorCode.INTERNAL)
        message = result.get("message", "indexer not available")
        data = result.get("data")
        return mcp_response(
            "rescan",
            lambda: pack_error("rescan", code, message, fields=data),
            lambda: {"error": {"code": code.value, "message": message, "data": data}, "isError": True},
        )

    def build_json() -> Dict[str, Any]:
        return {"requested": True}

    def build_pack() -> str:
        lines = [pack_header("rescan", {}, returned=1)]
        lines.append(pack_line("m", kv={"requested": "true"}))
        return "\n".join(lines)

    return mcp_response("rescan", build_pack, build_json)
