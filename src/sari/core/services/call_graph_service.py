import time
import fnmatch
import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set, Callable
from sari.core.models import SymbolDTO
from sari.core.workspace import WorkspaceManager

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
        if self.max_time_ms <= 0: return True
        elapsed_ms = (time.monotonic() - self.start_ts) * 1000.0
        if elapsed_ms > self.max_time_ms:
            self.truncated, self.truncate_reason = True, "time_budget_exceeded"
            return False
        return True

    def can_add_node(self) -> bool:
        if not self._check_time(): return False
        if self.nodes >= self.max_nodes:
            self.truncated, self.truncate_reason = True, "node_budget_exceeded"
            return False
        return True

    def can_add_edge(self) -> bool:
        if not self._check_time(): return False
        if self.edges >= self.max_edges:
            self.truncated, self.truncate_reason = True, "edge_budget_exceeded"
            return False
        return True

    def bump_node(self): self.nodes += 1
    def bump_edge(self): self.edges += 1


class CallGraphService:
    def __init__(self, db: Any, roots: List[str]):
        self.db = db
        self.roots = roots
        self._repo_cache: Dict[str, str] = {}

    def build(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """The core business logic for building call graphs."""
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

        include_paths = [str(p) for p in (args.get("include_paths") or [])]
        exclude_paths = [str(p) for p in (args.get("exclude_paths") or [])]

        if not name and not symbol_id: raise ValueError("symbol is required")

        root_ids = self._resolve_root_ids(args.get("root_ids") or [])
        matches = self._resolve_symbol(name, path, symbol_id, root_ids, repo)
        scope_reason = f"root_ids={root_ids or 'any'}; repo={repo or 'any'}"
        
        if not matches and name:
            fuzzy = self.db.symbols.fuzzy_search_symbols(name, limit=3)
            if fuzzy:
                target_cand = fuzzy[0]
                matches = [target_cand.model_dump() if hasattr(target_cand, "model_dump") else target_cand]
                scope_reason += f" (exact match failed, using fuzzy match for '{target_cand.name}')"

        if not matches:
            return self._empty_result(name, symbol_id, path, root_ids, repo, depth, max_nodes, max_edges, max_time_ms, scope_reason)
        
        target = matches[0]
        budget = _GraphBudget(max_nodes=max_nodes, max_edges=max_edges, max_depth=max_depth, max_time_ms=max_time_ms)
        budget.bump_node()

        def _allow(p: str) -> bool:
            if not p: return False
            if root_ids and not any(p == rid or p.startswith(rid + "/") for rid in root_ids): return False
            if repo and not self._path_matches_repo(p, repo): return False
            if include_paths and not any(fnmatch.fnmatchcase(p, pat) for pat in include_paths): return False
            if exclude_paths and any(fnmatch.fnmatchcase(p, pat) for pat in exclude_paths): return False
            return True

        upstream = self._build_tree(target["name"], target["path"], target.get("symbol_id"), min(depth, max_depth), "up", set(), budget, _allow, root_ids, sort_by)
        downstream = self._build_tree(target["name"], target["path"], target.get("symbol_id"), min(depth, max_depth), "down", set(), budget, _allow, root_ids, sort_by)

        return self._assemble_result(target, upstream, downstream, budget, depth, max_nodes, max_edges, max_time_ms, repo, root_ids, scope_reason)

    def _resolve_root_ids(self, req_root_ids: List[Any]) -> List[str]:
        from sari.mcp.tools._util import resolve_root_ids
        root_ids = resolve_root_ids(self.roots)
        if req_root_ids:
            req_ids = [str(r) for r in req_root_ids if r]
            return [r for r in root_ids if r in req_ids] if root_ids else req_ids
        return root_ids

    def _resolve_symbol(self, name: str, path: Optional[str], symbol_id: Optional[str], root_ids: List[str], repo: Optional[str]) -> List[Dict]:
        params: List[Any] = []
        if symbol_id:
            sql = "SELECT path, name, kind, line, end_line, qualname, symbol_id FROM symbols WHERE symbol_id = ?"
            params.append(symbol_id)
        else:
            sql = "SELECT path, name, kind, line, end_line, qualname, symbol_id FROM symbols WHERE (qualname = ? OR name = ?)"
            params.extend([name, name])
        
        if path: sql += " AND path = ?"; params.append(path)
        if root_ids: sql += f" AND root_id IN ({','.join(['?']*len(root_ids))})"; params.extend(root_ids)
        if repo: sql += " AND path IN (SELECT path FROM files WHERE repo = ?)"; params.append(repo)
        
        sql += " ORDER BY CASE WHEN qualname = ? THEN 0 ELSE 1 END, path, line LIMIT 50"
        params.append(name)
        
        rows = self.db._read.execute(sql, params).fetchall()
        return [{"path": r[0], "name": r[1], "kind": r[2], "line": r[3], "end_line": r[4], "qualname": r[5], "symbol_id": r[6]} for r in rows]

    def _build_tree(self, name: str, path: Optional[str], sid: Optional[str], depth: int, direction: str, visited: Set[Tuple], budget: _GraphBudget, allow: Callable, root_ids: List[str], sort_by: str) -> Dict:
        node = {"name": name, "path": path or "", "symbol_id": sid or "", "children": []}
        if depth <= 0: return node
        key = (direction, sid or name, path or "")
        if key in visited: return node
        visited.add(key)
        
        if not budget._check_time(): return node

        neighbors = self._get_neighbors(name, path, sid, direction, root_ids)
        neighbor_names = list(set([n.get("from_symbol" if direction == "up" else "to_symbol") for n in neighbors]))
        fan_in_map = self.db.get_symbol_fan_in_stats(neighbor_names)

        for n in neighbors:
            n_name = n.get("from_symbol" if direction == "up" else "to_symbol") or ""
            n_path = n.get("from_path" if direction == "up" else "to_path") or ""
            n["confidence"] = self._calculate_confidence(path or "", n_path, fan_in_map.get(n_name, 0))

        neighbors.sort(key=lambda r: (-r["confidence"], r.get("from_path" if direction == "up" else "to_path") or "", int(r.get("line") or 0)))

        for n in neighbors:
            c_name = n.get("from_symbol" if direction == "up" else "to_symbol") or ""
            c_path = n.get("from_path" if direction == "up" else "to_path") or ""
            conf = n.get("confidence", 0.5)
            if conf < 0.05 or not allow(c_path): continue 
            
            if not budget.can_add_edge() or not budget.can_add_node(): break
            budget.bump_edge(); budget.bump_node()
            
            child = self._build_tree(c_name, c_path, n.get("from_symbol_id" if direction == "up" else "to_symbol_id"), depth - 1, direction, visited, budget, allow, root_ids, sort_by)
            child.update({"line": int(n.get("line") or 0), "rel_type": n.get("rel_type") or "", "confidence": conf})
            node["children"].append(child)
        return node

    def _get_neighbors(self, name: str, path: Optional[str], sid: Optional[str], direction: str, root_ids: List[str]) -> List[Dict]:
        params = []
        if direction == "up":
            if sid:
                sql = "SELECT from_path, from_symbol, from_symbol_id, line, rel_type FROM symbol_relations WHERE to_symbol_id = ?"
                params.append(sid)
            else:
                sql = "SELECT from_path, from_symbol, from_symbol_id, line, rel_type FROM symbol_relations WHERE to_symbol = ?"
                params.append(name)
                if path: sql += " AND (to_path = ? OR to_path = '' OR to_path IS NULL)"; params.append(path)
            if root_ids: sql += f" AND from_root_id IN ({','.join(['?']*len(root_ids))})"; params.extend(root_ids)
        else:
            if sid:
                sql = "SELECT to_path, to_symbol, to_symbol_id, line, rel_type FROM symbol_relations WHERE from_symbol_id = ?"
                params.append(sid)
            else:
                sql = "SELECT to_path, to_symbol, to_symbol_id, line, rel_type FROM symbol_relations WHERE from_symbol = ?"
                params.append(name)
                if path: sql += " AND from_path = ?"; params.append(path)
            if root_ids: sql += f" AND to_root_id IN ({','.join(['?']*len(root_ids))})"; params.extend(root_ids)
        
        try:
            rows = self.db._read.execute(sql, params).fetchall()
            key_p, key_s, key_sid = ("from_path", "from_symbol", "from_symbol_id") if direction == "up" else ("to_path", "to_symbol", "to_symbol_id")
            return [{key_p: r[0], key_s: r[1], key_sid: r[2], "line": r[3], "rel_type": r[4]} for r in rows]
        except Exception: return []

    def _calculate_confidence(self, from_path: str, to_path: str, fan_in: int = 0) -> float:
        score = 0.5
        if from_path and to_path:
            p1, p2 = from_path.split("/"), to_path.split("/")
            common = 0
            for i in range(min(len(p1), len(p2))):
                if p1[i] == p2[i]: common += 1
                else: break
            score += (common / max(len(p1), len(p2))) * 0.15
        if fan_in > 50: score -= 0.8
        return min(1.0, max(0.1, score))

    def _path_matches_repo(self, path: str, repo: str) -> bool:
        if not path: return False
        if path in self._repo_cache: return self._repo_cache[path] == repo
        try:
            row = self.db._read.execute("SELECT repo FROM files WHERE path = ? LIMIT 1", (path,)).fetchone()
            self._repo_cache[path] = row[0] or "" if row else ""
            return self._repo_cache[path] == repo
        except Exception: return False

    def _assemble_result(self, target, upstream, downstream, budget, depth, max_nodes, max_edges, max_time_ms, repo, root_ids, scope_reason) -> Dict:
        u_count, d_count = len(upstream.get("children") or []), len(downstream.get("children") or [])
        quality = "low" if budget.truncated or (u_count == 0 and d_count == 0) else ("med" if budget.nodes < 10 else "high")
        
        from sari.mcp.tools.call_graph import _render_tree
        tree_text = ["Upstream:", _render_tree(upstream, min(2, depth)), "", "Downstream:", _render_tree(downstream, min(2, depth))]
        if budget.truncated: tree_text.append(f"\n[truncated: {budget.truncate_reason}]")

        return {
            "symbol": target["name"], "symbol_id": target.get("symbol_id") or "", "path": target["path"],
            "upstream": upstream, "downstream": downstream, "tree": "\n".join(tree_text),
            "truncated": budget.truncated, "truncate_reason": budget.truncate_reason, "graph_quality": quality,
            "scope_reason": scope_reason,
            "meta": {"nodes": budget.nodes, "edges": budget.edges, "depth": depth, "max_nodes": max_nodes, "max_edges": max_edges, "max_time_ms": max_time_ms, "repo": repo or "", "root_ids": root_ids},
            "summary": {"upstream_count": u_count, "downstream_count": d_count}
        }

    def _empty_result(self, name, sid, path, root_ids, repo, depth, max_nodes, max_edges, max_time_ms, scope_reason) -> Dict:
        return {
            "symbol": name or "", "symbol_id": sid or "", "path": path or "", "upstream": {"children": []}, "downstream": {"children": []}, "tree": "",
            "graph_quality": "low", "scope_reason": scope_reason,
            "meta": {"nodes": 0, "edges": 0, "depth": depth, "max_nodes": max_nodes, "max_edges": max_edges, "max_time_ms": max_time_ms, "repo": repo or "", "root_ids": root_ids},
            "summary": {"upstream_count": 0, "downstream_count": 0}
        }