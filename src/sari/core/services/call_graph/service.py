
import time
import fnmatch
import os
from typing import Any, Dict, List, Optional, Tuple, Set, Callable, Union

from sari.core.workspace import WorkspaceManager
from .budget import GraphBudget
from .render import render_tree

class CallGraphService:
    """
    심볼 간의 호출 그래프(Call Graph)를 생성하는 핵심 서비스입니다.
    데이터베이스에서 심볼 관계를 조회하여 계층적 트리 구조를 만듭니다.
    """
    
    def __init__(self, db: Any, roots: List[str]):
        """
        Args:
            db: 데이터베이스 접근 객체
            roots: 워크스페이스 루트 경로 목록
        """
        self.db = db
        self.roots = roots
        self._repo_cache: Dict[str, str] = {}

    def build(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        호출 그래프 생성의 핵심 비즈니스 로직입니다.
        인자 검증, 심볼 식별, 상/하류 트리 구성을 수행합니다.
        """
        try:
            params = self._parse_args(args)
        except ValueError as e:
            raise e

        # 1. 대상 심볼 식별
        matches = self._resolve_symbol(
            params["name"], params["path"], params["symbol_id"], 
            params["root_ids"], params["repo"]
        )
        scope_reason = f"root_ids={params['root_ids'] or 'any'}; repo={params['repo'] or 'any'}"
        
        # 정확한 일치가 없으면 퍼지 검색 시도
        if not matches and params["name"]:
            matches, scope_reason = self._try_fuzzy_fallback(params["name"], scope_reason)

        if not matches:
            return self._empty_result(params, scope_reason)
        
        target = matches[0]
        
        # 2. 그래프 탐색 버짓 초기화 (무한 루프 방지 및 성능 제어)
        budget = GraphBudget(
            params["max_nodes"], params["max_edges"], 
            params["max_depth"], params["max_time_ms"]
        )
        budget.bump_node()

        allow_fn = self._create_allow_filter(params)

        # 3. 트리 구축 (Upstream: 호출자, Downstream: 피호출자)
        upstream = self._build_tree(
            target["name"], target["path"], target.get("symbol_id"), 
            min(params["depth"], params["max_depth"]), "up", 
            set(), budget, allow_fn, params["root_ids"], params["sort_by"]
        )
        downstream = self._build_tree(
            target["name"], target["path"], target.get("symbol_id"), 
            min(params["depth"], params["max_depth"]), "down", 
            set(), budget, allow_fn, params["root_ids"], params["sort_by"]
        )

        # 4. 결과 조립
        return self._assemble_result(
            target, upstream, downstream, budget, params, scope_reason
        )

    def _parse_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """입력 인자를 파싱하고 기본값을 설정합니다."""
        name = str(args.get("symbol") or args.get("name") or "").strip()
        symbol_id = str(args.get("symbol_id") or args.get("sid") or "").strip() or None
        
        if not name and not symbol_id: 
            raise ValueError("symbol is required")

        depth = int(args.get("depth") or 2)
        return {
            "name": name,
            "symbol_id": symbol_id,
            "path": str(args.get("path") or "").strip() or None,
            "depth": depth,
            "max_nodes": int(args.get("max_nodes") or 400),
            "max_edges": int(args.get("max_edges") or 1200),
            "max_depth": int(args.get("max_depth") or depth),
            "max_time_ms": int(args.get("max_time_ms") or 2000),
            "sort_by": str(args.get("sort") or "line").strip().lower(),
            "repo": str(args.get("repo") or "").strip() or None,
            "include_paths": [str(p) for p in (args.get("include_paths") or [])],
            "exclude_paths": [str(p) for p in (args.get("exclude_paths") or [])],
            "root_ids": self._resolve_root_ids(args.get("root_ids") or [])
        }

    def _resolve_root_ids(self, req_root_ids: List[Any]) -> List[str]:
        """요청된 root_id를 실제 활성 워크스페이스 ID로 변환 및 검증합니다."""
        known_roots = []
        allow_legacy = str(os.environ.get("SARI_ALLOW_LEGACY", "")).strip().lower() in {"1", "true", "yes", "on"}
        
        if self.roots:
            for r in self.roots:
                try:
                    known_roots.append(WorkspaceManager.root_id_for_workspace(r))
                    if allow_legacy:
                        known_roots.append(WorkspaceManager.root_id(r))
                except Exception:
                    pass
        
        known_roots = list(dict.fromkeys(known_roots))

        if req_root_ids:
            req_ids = [str(r) for r in req_root_ids if r]
            return [r for r in known_roots if r in req_ids] if known_roots else req_ids
        return known_roots

    def _try_fuzzy_fallback(self, name: str, scope_reason: str) -> Tuple[List[Dict], str]:
        """정확한 이름 매칭 실패 시 유사 심볼을 찾습니다."""
        fuzzy = self.db.symbols.fuzzy_search_symbols(name, limit=3)
        if fuzzy:
            target_cand = fuzzy[0]
            matches = [target_cand.model_dump() if hasattr(target_cand, "model_dump") else target_cand]
            scope_reason += f" (exact match failed, using fuzzy match for '{target_cand.name}')"
            return matches, scope_reason
        return [], scope_reason

    def _create_allow_filter(self, params: Dict[str, Any]) -> Callable[[str], bool]:
        """파일 경로 필터링 함수를 생성합니다 (include/exclude/repo 등)."""
        root_ids = params["root_ids"]
        repo = params["repo"]
        includes = params["include_paths"]
        excludes = params["exclude_paths"]

        def _allow(p: str) -> bool:
            if not p: return False
            if root_ids and not any(p == rid or p.startswith(rid + "/") for rid in root_ids): return False
            if repo and not self._path_matches_repo(p, repo): return False
            if includes and not any(fnmatch.fnmatchcase(p, pat) for pat in includes): return False
            if excludes and any(fnmatch.fnmatchcase(p, pat) for pat in excludes): return False
            return True
        return _allow

    def _resolve_symbol(self, name: str, path: Optional[str], symbol_id: Optional[str], root_ids: List[str], repo: Optional[str]) -> List[Dict]:
        """DB에서 대상 심볼을 조회합니다."""
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
        
        try:
            rows = self.db.get_read_connection().execute(sql, params).fetchall()
            return [{"path": r[0], "name": r[1], "kind": r[2], "line": r[3], "end_line": r[4], "qualname": r[5], "symbol_id": r[6]} for r in rows]
        except Exception:
            if hasattr(self.db, "execute"):
                rows = self.db.execute(sql, params).fetchall()
                return [{"path": r[0], "name": r[1], "kind": r[2], "line": r[3], "end_line": r[4], "qualname": r[5], "symbol_id": r[6]} for r in rows]
            return []

    def _build_tree(self, name: str, path: Optional[str], sid: Optional[str], depth: int, direction: str, visited: Set[Tuple], budget: GraphBudget, allow: Callable, root_ids: List[str], sort_by: str) -> Dict:
        """재귀적으로 호출 그래프 트리를 구축합니다."""
        node = {"name": name, "path": path or "", "symbol_id": sid or "", "children": []}
        if depth <= 0: return node
        key = (direction, sid or name, path or "")
        if key in visited: return node
        visited.add(key)
        
        if not budget.check_time(): return node

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
            c_sid = n.get("from_symbol_id" if direction == "up" else "to_symbol_id")

            conf = n.get("confidence", 0.5)
            if conf < 0.05 or not allow(c_path): continue 
            
            if not budget.can_add_edge() or not budget.can_add_node(): break
            budget.bump_edge(); budget.bump_node()
            
            child = self._build_tree(c_name, c_path, c_sid, depth - 1, direction, visited, budget, allow, root_ids, sort_by)
            child.update({"line": int(n.get("line") or 0), "rel_type": n.get("rel_type") or "", "confidence": conf})
            node["children"].append(child)
        return node

    def _get_neighbors(self, name: str, path: Optional[str], sid: Optional[str], direction: str, root_ids: List[str]) -> List[Dict]:
        """DB에서 지정된 방향(up: 호출자, down: 피호출자)의 이웃 노드를 찾습니다."""
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
            conn = getattr(self.db, "_read", None)
            if not conn and hasattr(self.db, "get_read_connection"):
                conn = self.db.get_read_connection()
            if not conn:
                conn = self.db

            rows = conn.execute(sql, params).fetchall()
            key_p, key_s, key_sid = ("from_path", "from_symbol", "from_symbol_id") if direction == "up" else ("to_path", "to_symbol", "to_symbol_id")
            return [{key_p: r[0], key_s: r[1], key_sid: r[2], "line": r[3], "rel_type": r[4]} for r in rows]
        except Exception: return []

    def _calculate_confidence(self, from_path: str, to_path: str, fan_in: int = 0) -> float:
        """호출 관계의 신뢰도를 계산합니다. 경로 유사도와 팬인(Fan-in) 수치를 기반으로 합니다."""
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
        """파일 경로가 특정 레포지토리에 속하는지 확인합니다."""
        if not path: return False
        if path in self._repo_cache: return self._repo_cache[path] == repo
        try:
            conn = getattr(self.db, "_read", getattr(self.db, "db", self.db))
            row = conn.execute("SELECT repo FROM files WHERE path = ? LIMIT 1", (path,)).fetchone()
            self._repo_cache[path] = row[0] or "" if row else ""
            return self._repo_cache[path] == repo
        except Exception: return False

    def _assemble_result(self, target, upstream, downstream, budget, params, scope_reason) -> Dict:
        """최종 결과 딕셔너리를 조립하고 렌더링된 트리 텍스트를 포함시킵니다."""
        u_count, d_count = len(upstream.get("children") or []), len(downstream.get("children") or [])
        quality = "low" if budget.truncated or (u_count == 0 and d_count == 0) else ("med" if budget.nodes < 10 else "high")
        
        tree_text = ["Upstream:", render_tree(upstream, min(2, params["depth"])), "", "Downstream:", render_tree(downstream, min(2, params["depth"]))]
        if budget.truncated: tree_text.append(f"\n[truncated: {budget.truncate_reason}]")

        return {
            "symbol": target["name"], "symbol_id": target.get("symbol_id") or "", "path": target["path"],
            "upstream": upstream, "downstream": downstream, "tree": "\n".join(tree_text),
            "truncated": budget.truncated, "truncate_reason": budget.truncate_reason, "graph_quality": quality,
            "scope_reason": scope_reason,
            "meta": {
                "nodes": budget.nodes, "edges": budget.edges, "depth": params["depth"], 
                "max_nodes": params["max_nodes"], "max_edges": params["max_edges"], 
                "max_time_ms": params["max_time_ms"], "repo": params["repo"] or "", 
                "root_ids": params["root_ids"]
            },
            "summary": {"upstream_count": u_count, "downstream_count": d_count}
        }

    def _empty_result(self, params: Dict[str, Any], scope_reason: str) -> Dict:
        """결과가 없을 때의 빈 응답 객체를 반환합니다."""
        return {
            "symbol": params["name"] or "", "symbol_id": params["symbol_id"] or "", 
            "path": params["path"] or "", "upstream": {"children": []}, "downstream": {"children": []}, "tree": "",
            "graph_quality": "low", "scope_reason": scope_reason,
            "meta": {
                "nodes": 0, "edges": 0, "depth": params["depth"], 
                "max_nodes": params["max_nodes"], "max_edges": params["max_edges"], 
                "max_time_ms": params["max_time_ms"], "repo": params["repo"] or "", 
                "root_ids": params["root_ids"]
            },
            "summary": {"upstream_count": 0, "downstream_count": 0}
        }
