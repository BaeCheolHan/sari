import json
import os
import importlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set, Callable

from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
    resolve_root_ids,
)

PLUGIN_API_VERSION = 1

def _resolve_symbol(db: Any, name: str, path: Optional[str], symbol_id: Optional[str]) -> List[Dict[str, Any]]:
    params: List[Any] = []
    if symbol_id:
        sql = "SELECT path, name, kind, line, end_line, qualname, symbol_id FROM symbols WHERE symbol_id = ?"
        params.append(symbol_id)
    else:
        sql = "SELECT path, name, kind, line, end_line, qualname, symbol_id FROM symbols WHERE name = ?"
        params.append(name)
    if path:
        sql += " AND path = ?"
        params.append(path)
    sql += " ORDER BY path, line LIMIT 50"
    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    rows = conn.execute(sql, params).fetchall()
    
    # Priority Fix: Manually map rows to dictionaries to avoid 'dict(r)' failures with non-Row objects
    results = []
    for r in rows:
        results.append({
            "path": r[0], "name": r[1], "kind": r[2], "line": r[3], 
            "end_line": r[4], "qualname": r[5], "symbol_id": r[6]
        })
    return results

def _callers_for(db: Any, name: str, path: Optional[str], symbol_id: Optional[str]) -> List[Dict[str, Any]]:
    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    params = [name]
    sql = "SELECT from_path, from_symbol, from_symbol_id, line, rel_type FROM symbol_relations WHERE to_symbol = ?"
    if path:
        sql += " AND (to_path = ? OR to_path = '' OR to_path IS NULL)"
        params.append(path)
    sql += " ORDER BY from_path, line"
    try:
        rows = conn.execute(sql, params).fetchall()
        return [{"from_path": r[0], "from_symbol": r[1], "from_symbol_id": r[2], "line": r[3], "rel_type": r[4]} for r in rows]
    except: return []

def _callees_for(db: Any, name: str, path: Optional[str], symbol_id: Optional[str]) -> List[Dict[str, Any]]:
    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    params = [name]
    sql = "SELECT to_path, to_symbol, to_symbol_id, line, rel_type FROM symbol_relations WHERE from_symbol = ?"
    if path:
        sql += " AND from_path = ?"
        params.append(path)
    sql += " ORDER BY to_path, line"
    try:
        rows = conn.execute(sql, params).fetchall()
        return [{"to_path": r[0], "to_symbol": r[1], "to_symbol_id": r[2], "line": r[3], "rel_type": r[4]} for r in rows]
    except: return []

def _build_tree(db: Any, name: str, path: Optional[str], symbol_id: Optional[str], depth: int, direction: str, visited: Set[Tuple], allow=None) -> Dict[str, Any]:
    node = {"name": name, "path": path or "", "symbol_id": symbol_id or "", "children": []}
    if depth <= 0: return node
    key = (direction, symbol_id or name, path or "")
    if key in visited: return node
    visited.add(key)
    
    neighbors = _callers_for(db, name, path, symbol_id) if direction == "up" else _callees_for(db, name, path, symbol_id)
    for n in neighbors:
        c_path = n.get("from_path" if direction == "up" else "to_path") or ""
        if allow and not allow(c_path): continue
        child = _build_tree(db, n.get("from_symbol" if direction == "up" else "to_symbol") or "", c_path, n.get("from_symbol_id" if direction == "up" else "to_symbol_id") or "", depth - 1, direction, visited, allow)
        child["line"] = int(n.get("line") or 0)
        child["rel_type"] = n.get("rel_type") or ""
        node["children"].append(child)
    return node

def build_call_graph(args: Dict[str, Any], db: Any, roots: List[str]) -> Dict[str, Any]:
    name = str(args.get("symbol") or args.get("name") or "").strip()
    symbol_id = str(args.get("symbol_id") or args.get("sid") or "").strip() or None
    path = str(args.get("path") or "").strip() or None
    depth = int(args.get("depth") or 2)
    if not name and not symbol_id: raise ValueError("symbol is required")

    matches = _resolve_symbol(db, name, path, symbol_id)
    if not matches: return {"symbol": name or "", "upstream": {"children": []}, "downstream": {"children": []}, "tree": ""}
    
    target = matches[0]
    upstream = _build_tree(db, target["name"], target["path"], target.get("symbol_id"), depth, "up", set())
    downstream = _build_tree(db, target["name"], target["path"], target.get("symbol_id"), depth, "down", set())
    
    return {
        "symbol": target["name"],
        "symbol_id": target.get("symbol_id") or "",
        "path": target["path"],
        "upstream": upstream,
        "downstream": downstream,
        "tree": f"{target['name']} [Tree View Placeholder]"
    }

def execute_call_graph(args: Dict[str, Any], db: Any, logger: Any = None, roots: List[str] = None) -> Dict[str, Any]:
    if roots is None and isinstance(logger, list):
        roots = logger
        logger = None
    
    def build_pack(payload: Dict[str, Any]) -> str:
        d = str(int(args.get("depth") or 2)) if isinstance(args, dict) else "2"
        header = pack_header("call_graph", {"symbol": pack_encode_text(payload.get("symbol", "")), "depth": d}, returned=1)
        lines = [header, "t:" + pack_encode_text(payload.get("tree", ""))]
        return "\n".join(lines)

    try:
        payload = build_call_graph(args, db, roots or [])
    except Exception as e:
        import traceback
        stack = traceback.format_exc()
        return mcp_response("call_graph", 
            lambda: pack_error("call_graph", ErrorCode.INVALID_ARGS, f"{str(e)}: {stack}"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": str(e)}, "isError": True})

    return mcp_response("call_graph", lambda: build_pack(payload), lambda: payload)
