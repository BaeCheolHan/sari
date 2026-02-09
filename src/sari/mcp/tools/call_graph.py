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
from sari.core.services.call_graph_service import CallGraphService

def _render_tree(node: Dict[str, Any], depth: int, max_lines: int = 200) -> str:
    """Renders the hierarchical tree as a string for PACK1/Text output."""
    lines: List[str] = []
    
    def _walk(n: Dict[str, Any], d: int, prefix: str) -> None:
        if len(lines) >= max_lines: return
        name = n.get("name") or "(unknown)"
        path = n.get("path") or ""
        line = n.get("line") or 0
        
        # Simple deduplication
        children = n.get("children") or []
        grouped_children = []
        seen = set()
        for c in children:
            key = (c.get("name"), c.get("path"))
            if key not in seen:
                seen.add(key)
                grouped_children.append(c)

        meta = f" [{path}:{line}]" if path else ""
        lines.append(f"{prefix}{name}{meta}")
        
        if d <= 0: return
        for i, c in enumerate(grouped_children):
            branch = "└─ " if i == len(grouped_children) - 1 else "├─ "
            _walk(c, d - 1, prefix + branch)

    _walk(node, depth, "")
    return "\n".join(lines)

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