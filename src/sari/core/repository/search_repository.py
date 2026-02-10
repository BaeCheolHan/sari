import os
from typing import Any, Dict, List, Optional, Tuple

from sari.core.models import SearchHit
from sari.core.ranking import glob_to_like

from .base import BaseRepository


class SearchRepository(BaseRepository):
    """
    파일 및 심볼에 대한 고도화된 검색 기능을 제공하는 저장소입니다.
    키워드 검색, 시맨틱(벡터) 검색, 그리고 저장소 후보 탐색 기능을 포함합니다.
    """

    def repo_candidates(self, q: str, limit: int = 3,
                        root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        사용자의 쿼리에 가장 부합하는(매칭되는 내용이 많은) 상위 N개의 저장소(repo) 후보를 반환합니다.
        """
        if not q:
            return []
        lq = f"%{q}%"
        sql = "SELECT repo, COUNT(*) AS score FROM files WHERE deleted_ts = 0 AND (path LIKE ? OR rel_path LIKE ? OR fts_content LIKE ?)"
        params: List[Any] = [lq, lq, lq]
        if root_ids:
            placeholders = ",".join(["?"] * len(root_ids))
            sql += f" AND root_id IN ({placeholders})"
            params.extend(root_ids)
        sql += " GROUP BY repo ORDER BY score DESC LIMIT ?"
        params.append(int(limit))
        rows = self.execute(sql, params).fetchall()
        return [{"repo": r[0], "score": int(r[1])} for r in rows]

    def search_semantic(
            self,
            query_vector: List[float],
            limit: int = 10,
            **kwargs) -> List[SearchHit]:
        """
        입력된 쿼리 벡터와 DB에 저장된 벡터 간의 코사인 유사도를 계산하여 시맨틱 검색을 수행합니다.
        최적화를 위해 numpy가 있는 경우 활용하며, 유사도가 높은 순으로 결과를 반환합니다.
        """
        import struct
        import math

        try:
            import numpy as np
            has_numpy = True
        except ImportError:
            has_numpy = False

        sql = "SELECT entity_id, entity_type, vector, root_id FROM embeddings"
        if kwargs.get("root_ids"):
            rs = kwargs["root_ids"]
            placeholders = ",".join(["?"] * len(rs))
            sql += f" WHERE root_id IN ({placeholders})"
            params = rs
        else:
            params = []

        rows = self.execute(sql, params).fetchall()
        if not rows:
            return []

        # Convert query_vector to numpy for speed
        if has_numpy:
            q_vec = np.array(query_vector, dtype=np.float32)
            q_norm = np.linalg.norm(q_vec)
            if q_norm > 0:
                q_vec /= q_norm  # Normalize
        else:
            q_vec = query_vector
            q_norm = math.sqrt(sum(x * x for x in q_vec))

        scored_hits = []
        for entity_id, entity_type, vec_blob, root_id in rows:
            if not vec_blob:
                continue

            # 1. Faster Unpacking
            vec = struct.unpack(f"{len(vec_blob)//4}f", vec_blob)

            # 2. Advanced Similarity Calculation
            if has_numpy:
                v = np.array(vec, dtype=np.float32)
                v_norm = np.linalg.norm(v)
                if v_norm == 0:
                    continue
                # Cosine Similarity via Dot Product of normalized vectors
                score = np.dot(q_vec, v) / v_norm
            else:
                dot = sum(a * b for a, b in zip(q_vec, vec))
                v_norm = math.sqrt(sum(x * x for x in vec))
                if v_norm == 0:
                    continue
                score = dot / (q_norm * v_norm)

            if score > 0.4:  # Slightly lower threshold for semantic nuances
                scored_hits.append((score, entity_id, entity_type, root_id))

        # 3. Intelligent Ranking
        scored_hits.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, eid, etype, rid in scored_hits[:limit]:
            results.append(SearchHit(
                path=eid,
                repo=rid,
                score=float(score * 100.0),
                hit_reason=f"Semantic ({etype})",
                file_type=os.path.splitext(eid)[1] if "." in eid else "symbol"
            ))
        return results

    def search_v2(self, opts: Any) -> Tuple[List[SearchHit], Dict[str, Any]]:
        """
        키워드 검색, 중요도 가점, 페이지네이션을 결합한 통합 검색(v2)을 실행합니다.
        """
        query = str(getattr(opts, "query", "") or "").strip()
        if not query:
            return [], {
                "total": 0, "total_mode": getattr(
                    opts, "total_mode", "exact")}

        # Extract search parameters
        params = self._extract_search_params(opts, query)

        # Build and execute SQL query
        rows = self._execute_search_query(params)

        # Process results into SearchHits
        hits = self._process_search_results(rows, query)

        # Calculate total count if requested
        total = self._calculate_total_count(params, hits)

        return hits, {"total": total, "total_mode": params["total_mode"]}

    def _extract_search_params(self, opts: Any, query: str) -> Dict[str, Any]:
        """Extract and validate search parameters from options."""
        raw_file_types = getattr(opts, "file_types", None) or []
        file_types = [str(value).strip().lower().lstrip(".")
                      for value in raw_file_types if str(value).strip()]
        raw_excludes = getattr(opts, "exclude_patterns", None) or []
        exclude_patterns = [str(value).strip()
                            for value in raw_excludes if str(value).strip()]
        return {
            "query": query,
            "like_query": f"%{query}%",
            "limit": int(getattr(opts, "limit", 50) or 50),
            "offset": int(getattr(opts, "offset", 0) or 0),
            "repo": getattr(opts, "repo", None),
            "root_ids": getattr(opts, "root_ids", None) or [],
            "file_types": file_types,
            "path_pattern": str(getattr(opts, "path_pattern", "") or "").strip(),
            "exclude_patterns": exclude_patterns,
            "total_mode": getattr(opts, "total_mode", "exact"),
        }

    def _build_where_clause(
            self, params: Dict[str, Any]) -> Tuple[str, List[Any]]:
        conditions: List[str] = [
            "f.deleted_ts = 0",
            "(f.path LIKE ? OR f.rel_path LIKE ? OR f.fts_content LIKE ?)",
        ]
        sql_params: List[Any] = [
            params["like_query"],
            params["like_query"],
            params["like_query"]]

        if params["repo"]:
            conditions.append("f.repo = ?")
            sql_params.append(params["repo"])

        if params["root_ids"]:
            placeholders = ",".join(["?"] * len(params["root_ids"]))
            conditions.append(f"f.root_id IN ({placeholders})")
            sql_params.extend(params["root_ids"])

        if params["file_types"]:
            type_clauses: List[str] = []
            for file_type in params["file_types"]:
                type_clauses.append("LOWER(f.path) LIKE ?")
                sql_params.append(f"%.{file_type}")
            conditions.append("(" + " OR ".join(type_clauses) + ")")

        if params["path_pattern"]:
            like_pattern = glob_to_like(params["path_pattern"])
            conditions.append("(f.rel_path LIKE ? OR f.path LIKE ?)")
            sql_params.extend([like_pattern, like_pattern])

        if params["exclude_patterns"]:
            for pattern in params["exclude_patterns"]:
                excluded = glob_to_like(pattern)
                conditions.append("f.rel_path NOT LIKE ?")
                conditions.append("f.path NOT LIKE ?")
                sql_params.extend([excluded, excluded])

        return " AND ".join(conditions), sql_params

    def _execute_search_query(self, params: Dict[str, Any]) -> List[Tuple]:
        """Build and execute search SQL query with importance scoring."""
        where_clause, sql_params = self._build_where_clause(params)
        select_sql = """
            SELECT f.path, f.repo, f.mtime, f.size, f.fts_content, f.rel_path,
                   IFNULL((
                       SELECT MAX(importance_score)
                       FROM symbols s
                       WHERE s.path = f.path
                   ), 0.0) as importance
            FROM files f
        """
        paging_params = [params["limit"], params["offset"]]
        sql = f"{select_sql} WHERE {where_clause} ORDER BY importance DESC, f.mtime DESC LIMIT ? OFFSET ?"
        try:
            return self.execute(sql, sql_params + paging_params).fetchall()
        except Exception:
            fallback_sql = (
                "SELECT f.path, f.repo, f.mtime, f.size, f.fts_content, f.rel_path, 0.0 as importance "
                "FROM files f "
                f"WHERE {where_clause} "
                "ORDER BY f.mtime DESC LIMIT ? OFFSET ?")
            return self.execute(
                fallback_sql,
                sql_params +
                paging_params).fetchall()

    def _process_search_results(
            self,
            rows: List[Tuple],
            query: str) -> List[SearchHit]:
        """Process database rows into SearchHit objects with snippets."""
        hits: List[SearchHit] = []

        for r in rows:
            # Flexible row unpacking
            path, repo_name, mtime, size, fts_content, _rel_path, importance = r[
                0], r[1], r[2], r[3], r[4], r[5], r[6]

            # Extract snippet and count matches
            snippet, match_count = self._extract_snippet(fts_content, query)

            hits.append(
                SearchHit(
                    repo=repo_name or "",
                    path=path or "",
                    score=1.0 + importance,  # Combine Importance into score
                    snippet=snippet,
                    mtime=int(mtime or 0),
                    size=int(size or 0),
                    match_count=max(1, match_count),
                    file_type=os.path.splitext(
                        str(path))[1] if "." in str(path) else "",
                    hit_reason=f"Keyword (importance={importance:.1f})",
                )
            )

        return hits

    def _extract_snippet(self, fts_content: str,
                         query: str) -> Tuple[str, int]:
        """Extract context snippet around query match."""
        snippet = ""
        match_count = 0

        if fts_content:
            try:
                text = str(fts_content)
                lower = text.lower()
                q_lower = query.lower()
                match_count = lower.count(q_lower)
                idx = lower.find(q_lower)
                if idx >= 0:
                    start = max(0, idx - 120)
                    end = min(len(text), idx + 120)
                    snippet = text[start:end]
            except Exception:
                snippet = ""

        return snippet, match_count

    def _calculate_total_count(
            self, params: Dict[str, Any], hits: List[SearchHit]) -> int:
        """Calculate total result count based on total_mode."""
        total = len(hits)

        if params["total_mode"] == "exact":
            try:
                where_clause, count_params = self._build_where_clause(params)
                count_sql = f"SELECT COUNT(1) FROM files f WHERE {where_clause}"
                total = int(
                    self.execute(
                        count_sql,
                        count_params).fetchone()[0])
            except Exception:
                total = len(hits)

        return total
