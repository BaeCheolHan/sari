import json
import os
import time
from typing import Any, Dict, List, Optional
from ._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
)
from sari.core.services.call_graph.service import CallGraphService

def build_call_graph(args: Dict[str, Any], db: Any, roots: List[str]) -> Dict[str, Any]:
    """Legacy entry point, redirects to CallGraphService."""
    svc = CallGraphService(db, roots)
    return svc.build(args)

def execute_call_graph(args: Dict[str, Any], db: Any, logger: Any = None, roots: List[str] = None) -> Dict[str, Any]:
    """MCP tool execution entry point."""
    if roots is None and isinstance(logger, list):
        roots, logger = logger, None
    
    def build_pack(payload: Dict[str, Any]) -> str:
        d = str(int(args.get("depth") or 2))
        header = pack_header("call_graph", {
            "symbol": pack_encode_text(payload.get("symbol", "")),
            "depth": d,
            "quality": pack_encode_id(payload.get("graph_quality", "")),
            "truncated": str(bool(payload.get("truncated"))).lower(),
        }, returned=1)
        meta = payload.get("meta", {})
        lines = [
            header,
            "t:" + pack_encode_text(payload.get("tree", "")),
            pack_line("m", {"scope_reason": pack_encode_text(payload.get("scope_reason", ""))}),
            pack_line("m", {"nodes": str(meta.get("nodes", 0)), "edges": str(meta.get("edges", 0))}),
        ]
        return "\n".join(lines)

    try:
        svc = CallGraphService(db, roots or [])
        payload = svc.build(args)
        return mcp_response("call_graph", lambda: build_pack(payload), lambda: payload)
    except Exception as e:
        import traceback
        stack = traceback.format_exc()
        msg = str(e)
        code = ErrorCode.DB_ERROR if "db" in msg.lower() else ErrorCode.INVALID_ARGS
        return mcp_response(
            "call_graph",
            lambda: pack_error("call_graph", code, f"{msg}: {stack}"),
            lambda: {"error": {"code": code.value, "message": msg}, "isError": True},
        )