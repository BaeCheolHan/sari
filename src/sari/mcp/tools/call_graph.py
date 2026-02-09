import json
import os
import importlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set, Callable
import fnmatch
import time

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
from sari.core.services.call_graph_service import CallGraphService

PLUGIN_API_VERSION = 1

class _GraphBudget:
    def __init__(self, max_nodes: int, max_edges: int, max_depth: int, max_time_ms: int) -> None:
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self.max_depth = max_depth
        self.max_time_ms = max_time_ms
        self.nodes = 0
        self.edges = 0
        self.truncated = False
        self.truncate_reason = ""
        self.start_ts = time.monotonic()

    def _check_time(self) -> bool:
        if self.max_time_ms <= 0:
            return True
        elapsed_ms = (time.monotonic() - self.start_ts) * 1000.0
        if elapsed_ms > self.max_time_ms:
            self.truncated = True
            self.truncate_reason = "time_budget_exceeded"
            return False
        return True

    def can_add_node(self) -> bool:
        if not self._check_time():
            return False
        if self.nodes >= self.max_nodes:
            self.truncated = True
            self.truncate_reason = "node_budget_exceeded"
            return False
        return True

    def can_add_edge(self) -> bool:
        if not self._check_time():
            return False
        if self.edges >= self.max_edges:
            self.truncated = True
            self.truncate_reason = "edge_budget_exceeded"
            return False
        return True

    def bump_node(self) -> None:
        self.nodes += 1

    def bump_edge(self) -> None:
        self.edges += 1


def _resolve_symbol(db: Any, name: str, path: Optional[str], symbol_id: Optional[str], root_ids: Optional[List[str]] = None, repo: Optional[str] = None) -> List[Dict[str, Any]]:
    params: List[Any] = []
    if symbol_id:
        sql = "SELECT path, name, kind, line, end_line, qualname, symbol_id FROM symbols WHERE symbol_id = ?"
        params.append(symbol_id)
    elif "." in name: # Handle qualified names
        sql = "SELECT path, name, kind, line, end_line, qualname, symbol_id FROM symbols WHERE qualname = ?"
        params.append(name)
    else:
        sql = "SELECT path, name, kind, line, end_line, qualname, symbol_id FROM symbols WHERE name = ?"
        params.append(name)
    
    if path:
        sql += " AND path = ?"
        params.append(path)
    if root_ids:
        placeholders = ",".join(["?"] * len(root_ids))
        sql += f" AND root_id IN ({placeholders})"
        params.extend(root_ids)
    if repo:
        sql += " AND path IN (SELECT path FROM files WHERE repo = ?)"
        params.append(repo)
    
    sql += " ORDER BY CASE WHEN qualname = ? THEN 0 ELSE 1 END, path, line LIMIT 50"
    params.append(name)
    
    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    rows = conn.execute(sql, params).fetchall()
    
    results = []
    for r in rows:
        results.append({
            "path": r[0], "name": r[1], "kind": r[2], "line": r[3], 
            "end_line": r[4], "qualname": r[5], "symbol_id": r[6]
        })
    return results

def _callers_for(db: Any, name: str, path: Optional[str], symbol_id: Optional[str], root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    params = []
    
    if symbol_id:
        sql = "SELECT from_path, from_symbol, from_symbol_id, line, rel_type FROM symbol_relations WHERE to_symbol_id = ?"
        params.append(symbol_id)
    else:
        sql = "SELECT from_path, from_symbol, from_symbol_id, line, rel_type FROM symbol_relations WHERE to_symbol = ?"
        params.append(name)
        if path:
            sql += " AND (to_path = ? OR to_path = '' OR to_path IS NULL)"
            params.append(path)

    if root_ids:
        placeholders = ",".join(["?"] * len(root_ids))
        sql += f" AND from_root_id IN ({placeholders})"
        params.extend(root_ids)
    
    sql += " ORDER BY from_path, line"
    try:
        rows = conn.execute(sql, params).fetchall()
        return [{"from_path": r[0], "from_symbol": r[1], "from_symbol_id": r[2], "line": r[3], "rel_type": r[4]} for r in rows]
    except Exception: return []

def _callees_for(db: Any, name: str, path: Optional[str], symbol_id: Optional[str], root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    params = []
    
    if symbol_id:
        sql = "SELECT to_path, to_symbol, to_symbol_id, line, rel_type FROM symbol_relations WHERE from_symbol_id = ?"
        params.append(symbol_id)
    else:
        sql = "SELECT to_path, to_symbol, to_symbol_id, line, rel_type FROM symbol_relations WHERE from_symbol = ?"
        params.append(name)
        if path:
            sql += " AND from_path = ?"
            params.append(path)

    if root_ids:
        placeholders = ",".join(["?"] * len(root_ids))
        sql += f" AND to_root_id IN ({placeholders})"
        params.extend(root_ids)
    
    sql += " ORDER BY to_path, line"
    try:
        rows = conn.execute(sql, params).fetchall()
        return [{"to_path": r[0], "to_symbol": r[1], "to_symbol_id": r[2], "line": r[3], "rel_type": r[4]} for r in rows]
    except Exception: return []

def _path_matches_repo(db: Any, path: str, repo: str, cache: Dict[str, str]) -> bool:
    if not path:
        return False
    if path in cache:
        return cache[path] == repo
    try:
        conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
        row = conn.execute("SELECT repo FROM files WHERE path = ? LIMIT 1", (path,)).fetchone()
        if not row:
            cache[path] = ""
            return False
        cache[path] = row[0] or ""
        return cache[path] == repo
    except Exception:
        return False


def _calculate_confidence(from_path: str, to_path: str, fan_in: int = 0) -> float:
    """Calculates a confidence score (0.0 to 1.0) for a relationship."""
    score = 0.5 # Base score
    
    # 1. Proximity Scoring (Spatial)
    if from_path and to_path:
        p1, p2 = from_path.split("/"), to_path.split("/")
        common = 0
        for i in range(min(len(p1), len(p2))):
            if p1[i] == p2[i]: common += 1
            else: break
        # Higher common prefix = higher score (Max bonus reduced to +0.15)
        score += (common / max(len(p1), len(p2))) * 0.15
        
    # 2. Entropy Suppression (Utility Noise) - EXTREME PENALTY
    if fan_in > 50:
        # Ubiquitous symbols like 'log' get extreme penalty to ensure they stay at the bottom
        score -= 0.8
    
    return min(1.0, max(0.1, score))

def _build_tree(
    db: Any,
    name: str,
    path: Optional[str],
    symbol_id: Optional[str],
    depth: int,
    direction: str,
    visited: Set[Tuple],
    budget: _GraphBudget,
    allow: Optional[Callable[[str], bool]] = None,
    root_ids: Optional[List[str]] = None,
    sort_by: str = "line",
) -> Dict[str, Any]:
    node = {"name": name, "path": path or "", "symbol_id": symbol_id or "", "children": []}
    if depth <= 0: return node
    key = (direction, symbol_id or name, path or "")
    if key in visited: return node
    visited.add(key)
    
    if not budget._check_time():
        return node

    neighbors = _callers_for(db, name, path, symbol_id, root_ids) if direction == "up" else _callees_for(db, name, path, symbol_id, root_ids)
    
    # Fetch Fan-in stats for noise suppression
    neighbor_names = list(set([n.get("from_symbol" if direction == "up" else "to_symbol") for n in neighbors]))
    fan_in_map = {}
    if hasattr(db, "get_symbol_fan_in_stats"):
        fan_in_map = db.get_symbol_fan_in_stats(neighbor_names)

    # Rank neighbors by algorithm (Confidence)
    for n in neighbors:
        n_name = n.get("from_symbol" if direction == "up" else "to_symbol") or ""
        n_path = n.get("from_path" if direction == "up" else "to_path") or ""
        n["confidence"] = _calculate_confidence(path or "", n_path, fan_in_map.get(n_name, 0))

    # Sort by confidence DESC, then by original sort_by
    if sort_by == "name":
        neighbors.sort(key=lambda r: (-r["confidence"], r.get("from_symbol" if direction == "up" else "to_symbol") or "", int(r.get("line") or 0)))
    else:
        neighbors.sort(key=lambda r: (-r["confidence"], r.get("from_path" if direction == "up" else "to_path") or "", int(r.get("line") or 0)))

    for n in neighbors:
        c_name = n.get("from_symbol" if direction == "up" else "to_symbol") or ""
        c_path = n.get("from_path" if direction == "up" else "to_path") or ""
        conf = n.get("confidence", 0.5)
        
        # Hard Filter Threshold - Set to 0.05 to allow showing even high-noise nodes (but at the bottom)
        if conf < 0.05: continue 
        
        if allow and not allow(c_path): continue
        
        if not budget.can_add_edge():
            break
        budget.bump_edge()
        if not budget.can_add_node():
            break
        budget.bump_node()
        child = _build_tree(
            db,
            c_name,
            c_path,
            n.get("from_symbol_id" if direction == "up" else "to_symbol_id") or "",
            depth - 1,
            direction,
            visited,
            budget,
            allow,
            root_ids,
            sort_by=sort_by,
        )
        child["line"] = int(n.get("line") or 0)
        child["rel_type"] = n.get("rel_type") or ""
        child["confidence"] = conf # Expose score to caller
        node["children"].append(child)
    return node

def _render_tree(node: Dict[str, Any], depth: int, max_lines: int = 200) -> str:
    lines: List[str] = []
    
    def _walk(n: Dict[str, Any], d: int, prefix: str) -> None:
        if len(lines) >= max_lines: return
        
        name = n.get("name") or "(unknown)"
        path = n.get("path") or ""
        line = n.get("line") or 0
        
        # Deduplicate multiple calls to the same target from the same path
        children = n.get("children") or []
        grouped_children = []
        seen = {} # (name, path) -> index
        
        for c in children:
            key = (c.get("name"), c.get("path"))
            if key in seen:
                idx = seen[key]
                if "extra_lines" not in grouped_children[idx]:
                    grouped_children[idx]["extra_lines"] = []
                grouped_children[idx]["extra_lines"].append(c.get("line"))
            else:
                seen[key] = len(grouped_children)
                grouped_children.append(c)

        label = name
        meta = f" [{path}:{line}]" if path else ""
        
        # Add call count if deduplicated
        if n.get("extra_lines"):
            count = len(n["extra_lines"]) + 1
            label = f"{label} (x{count})"
            
        lines.append(f"{prefix}{label}{meta}")
        
        if d <= 0: return
        
        for i, c in enumerate(grouped_children):
            if len(lines) >= max_lines: return
            branch = "└─ " if i == len(grouped_children) - 1 else "├─ "
            _walk(c, d - 1, prefix + branch)

    _walk(node, depth, "")
    return "\n".join(lines)

def build_call_graph(args: Dict[str, Any], db: Any, roots: List[str]) -> Dict[str, Any]:
    name = str(args.get("symbol") or args.get("name") or "").strip()
    symbol_id = str(args.get("symbol_id") or args.get("sid") or "").strip() or None
    path = str(args.get("path") or "").strip() or None
    depth = int(args.get("depth") or 2)
    max_nodes = int(args.get("max_nodes") or 400)
    max_edges = int(args.get("max_edges") or 1200)
    max_depth = int(args.get("max_depth") or depth)
    max_time_ms = int(args.get("max_time_ms") or 2000)
    sort_by = str(args.get("sort") or "line").strip().lower()
    repo = str(args.get("repo") or "").strip() or None

    include_paths = args.get("include_paths") or args.get("include_path") or []
    exclude_paths = args.get("exclude_paths") or args.get("exclude_path") or []
    include_paths = [str(p) for p in include_paths] if isinstance(include_paths, list) else []
    exclude_paths = [str(p) for p in exclude_paths] if isinstance(exclude_paths, list) else []

    if not name and not symbol_id: raise ValueError("symbol is required")

    raw_root_ids = args.get("root_ids") or []
    if isinstance(raw_root_ids, list) and raw_root_ids:
        raw_root_ids = [str(r) for r in raw_root_ids]
        if any("/" in r for r in raw_root_ids):
            root_ids = resolve_root_ids(raw_root_ids)
        else:
            root_ids = raw_root_ids
    else:
        root_ids = resolve_root_ids(roots or [])
    scope_limited = False
    scope_reason = ""
    if not root_ids and not repo:
        scope_limited = True
        max_nodes = min(max_nodes, 80)
        max_edges = min(max_edges, 200)
        scope_reason = "no root_ids/repo provided; limited sample mode"
    else:
        scope_reason = f"root_ids={root_ids or 'any'}; repo={repo or 'any'}"

    matches = _resolve_symbol(db, name, path, symbol_id, root_ids, repo)
    if not matches:
        return {
            "symbol": name or "",
            "symbol_id": symbol_id or "",
            "path": path or "",
            "upstream": {"children": []},
            "downstream": {"children": []},
            "tree": "",
            "scope_reason": scope_reason,
            "truncated": False,
            "truncate_reason": "",
            "graph_quality": "low",
            "meta": {
                "nodes": 0,
                "edges": 0,
                "depth": depth,
                "max_nodes": max_nodes,
                "max_edges": max_edges,
                "max_time_ms": max_time_ms,
                "scope_limited": scope_limited,
                "repo": repo or "",
                "root_ids": root_ids,
            },
            "summary": {
                "upstream_count": 0,
                "downstream_count": 0,
            },
        }
    
    target = matches[0]
    budget = _GraphBudget(max_nodes=max_nodes, max_edges=max_edges, max_depth=max_depth, max_time_ms=max_time_ms)
    budget.bump_node()
    repo_cache: Dict[str, str] = {}

    def _allow(p: str) -> bool:
        if not p:
            return False
        if root_ids:
            if not any(p == rid or p.startswith(rid + "/") for rid in root_ids):
                return False
        if repo and not _path_matches_repo(db, p, repo, repo_cache):
            return False
        if include_paths:
            if not any(fnmatch.fnmatchcase(p, pat) for pat in include_paths):
                return False
        if exclude_paths:
            if any(fnmatch.fnmatchcase(p, pat) for pat in exclude_paths):
                return False
        return True

    upstream = _build_tree(db, target["name"], target["path"], target.get("symbol_id"), min(depth, max_depth), "up", set(), budget, _allow, root_ids, sort_by=sort_by)
    downstream = _build_tree(db, target["name"], target["path"], target.get("symbol_id"), min(depth, max_depth), "down", set(), budget, _allow, root_ids, sort_by=sort_by)

    upstream_count = len(upstream.get("children") or [])
    downstream_count = len(downstream.get("children") or [])

    if budget.truncated:
        quality = "low"
    elif upstream_count == 0 and downstream_count == 0:
        quality = "low"
    elif budget.nodes < 10:
        quality = "med"
    else:
        quality = "high"

    tree_text = []
    tree_text.append("Upstream:")
    tree_text.append(_render_tree(upstream, min(2, depth), max_lines=120) or "(empty)")
    tree_text.append("")
    tree_text.append("Downstream:")
    tree_text.append(_render_tree(downstream, min(2, depth), max_lines=120) or "(empty)")
    if budget.truncated:
        tree_text.append("")
        tree_text.append(f"[truncated: {budget.truncate_reason}]")
    
    return {
        "symbol": target["name"],
        "symbol_id": target.get("symbol_id") or "",
        "path": target["path"],
        "upstream": upstream,
        "downstream": downstream,
        "tree": "\n".join(tree_text),
        "scope_reason": scope_reason,
        "truncated": budget.truncated,
        "truncate_reason": budget.truncate_reason,
        "graph_quality": quality,
        "meta": {
            "nodes": budget.nodes,
            "edges": budget.edges,
            "depth": depth,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "max_time_ms": max_time_ms,
            "scope_limited": scope_limited,
            "repo": repo or "",
            "root_ids": root_ids,
        },
        "summary": {
            "upstream_count": upstream_count,
            "downstream_count": downstream_count,
        },
    }

def execute_call_graph(args: Dict[str, Any], db: Any, logger: Any = None, roots: List[str] = None) -> Dict[str, Any]:
    if roots is None and isinstance(logger, list):
        roots = logger
        logger = None
    
    def build_pack(payload: Dict[str, Any]) -> str:
        d = str(int(args.get("depth") or 2)) if isinstance(args, dict) else "2"
        header = pack_header("call_graph", {
            "symbol": pack_encode_text(payload.get("symbol", "")),
            "depth": d,
            "quality": pack_encode_id(payload.get("graph_quality", "")),
            "truncated": str(bool(payload.get("truncated"))).lower(),
        }, returned=1)
        meta = payload.get("meta", {}) or {}
        lines = [
            header,
            "t:" + pack_encode_text(payload.get("tree", "")),
            pack_line("m", {"scope_reason": pack_encode_text(payload.get("scope_reason", ""))}),
            pack_line("m", {"truncate_reason": pack_encode_text(payload.get("truncate_reason", ""))}),
            pack_line("m", {"nodes": str(meta.get("nodes", 0)), "edges": str(meta.get("edges", 0))}),
            pack_line("m", {"max_nodes": str(meta.get("max_nodes", 0)), "max_edges": str(meta.get("max_edges", 0)), "max_time_ms": str(meta.get("max_time_ms", 0))}),
        ]
        return "\n".join(lines)

    try:
        svc = CallGraphService(db, roots or [])
        payload = svc.build(args)
    except Exception as e:
        import traceback
        stack = traceback.format_exc()
        code = ErrorCode.INVALID_ARGS
        msg = str(e)
        if "database" in msg.lower() or "db" in msg.lower():
            code = ErrorCode.DB_ERROR
        return mcp_response(
            "call_graph",
            lambda: pack_error("call_graph", code, f"{msg}: {stack}"),
            lambda: {"error": {"code": code.value, "message": msg}, "isError": True},
        )

    return mcp_response("call_graph", lambda: build_pack(payload), lambda: payload)
