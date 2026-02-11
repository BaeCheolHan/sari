import os
import sqlite3
import logging
import zlib
from typing import Dict, List, Optional, Tuple

from sari.core.models import SearchHit, SearchOptions
from sari.core.ranking import glob_to_like

from .base import BaseRepository

logger = logging.getLogger("sari.repository.search")


class SearchRepository(BaseRepository):
    """
    파일 및 심볼에 대한 고도화된 검색 기능을 제공하는 저장소입니다.
    키워드 검색, 시맨틱(벡터) 검색, 그리고 저장소 후보 탐색 기능을 포함합니다.
    """

    def repo_candidates(self, q: str, limit: int = 3,
                        root_ids: Optional[List[str]] = None) -> List[Dict[str, object]]:
        """
        사용자의 쿼리에 가장 부합하는(매칭되는 내용이 많은) 상위 N개의 저장소(repo) 후보를 반환합니다.
        """
        if not q:
            return []
        # Escape wildcards for LIKE
        escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        lq = f"%{escaped_q}%"
        sql = "SELECT repo, COUNT(*) AS score FROM files WHERE deleted_ts = 0 AND (path LIKE ? ESCAPE '\\' OR rel_path LIKE ? ESCAPE '\\')"
        params: List[object] = [lq, lq]
        if root_ids:
            placeholders = ",".join(["?"] * len(root_ids))
            sql += f" AND root_id IN ({placeholders})"
            params.extend(root_ids)
        sql += " GROUP BY repo ORDER BY score DESC LIMIT ?"
        params.append(int(limit))
        rows = self.execute(sql, params).fetchall()
        return [{"repo": self._row_val(r, "repo", 0, ""), "score": int(self._row_val(r, "score", 1, 0) or 0)} for r in rows]

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

        sql = (
            "SELECT e.entity_id, e.entity_type, e.vector, e.root_id, "
            "COALESCE(r.label, e.root_id) AS repo_label "
            "FROM embeddings e "
            "LEFT JOIN roots r ON r.root_id = e.root_id"
        )
        if kwargs.get("root_ids"):
            rs = kwargs["root_ids"]
            placeholders = ",".join(["?"] * len(rs))
            sql += f" WHERE e.root_id IN ({placeholders})"
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
            if q_norm == 0:
                return []

        scored_hits = []
        for row in rows:
            entity_id = self._row_val(row, "entity_id", 0, "")
            entity_type = self._row_val(row, "entity_type", 1, "")
            vec_blob = self._row_val(row, "vector", 2, None)
            root_id = self._row_val(row, "root_id", 3, "")
            repo_label = str(self._row_val(row, "repo_label", 4, root_id) or root_id)
            if not vec_blob:
                continue

            # Skip truncated/corrupted blobs instead of failing the entire search.
            if len(vec_blob) % 4 != 0:
                logger.warning(
                    "Skipping invalid embedding blob length for entity_id=%s (len=%s)",
                    entity_id,
                    len(vec_blob),
                )
                continue
            if has_numpy:
                vec = np.frombuffer(vec_blob, dtype=np.float32)
            else:
                try:
                    vec = struct.unpack(f"{len(vec_blob)//4}f", vec_blob)
                except struct.error:
                    logger.warning("Skipping unparsable embedding blob for entity_id=%s", entity_id)
                    continue

            # 2. Advanced Similarity Calculation
            if has_numpy:
                if vec.shape[0] != q_vec.shape[0]:
                    continue
                v_norm = np.linalg.norm(vec)
                if v_norm == 0:
                    continue
                # Cosine Similarity via Dot Product of normalized vectors
                score = np.dot(q_vec, vec) / v_norm
            else:
                if len(vec) != len(q_vec):
                    continue
                dot = sum(a * b for a, b in zip(q_vec, vec))
                v_norm = math.sqrt(sum(x * x for x in vec))
                if v_norm == 0 or q_norm == 0:
                    continue
                score = dot / (q_norm * v_norm)

            if score > 0.4:  # Slightly lower threshold for semantic nuances
                scored_hits.append((score, entity_id, entity_type, repo_label))

        # 3. Intelligent Ranking
        scored_hits.sort(key=lambda x: x[0], reverse=True)
        results = []
        for s_score, s_eid, s_etype, s_repo_label in scored_hits[:limit]:
            results.append(SearchHit(
                path=s_eid,
                repo=s_repo_label,
                score=float(s_score * 100.0),
                hit_reason=f"Semantic ({s_etype})",
                file_type=os.path.splitext(s_eid)[1] if "." in s_eid else "symbol"
            ))
        return results

    def search(self, opts: SearchOptions) -> Tuple[List[SearchHit], Dict[str, object]]:
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

    def _extract_search_params(self, opts: SearchOptions, query: str) -> Dict[str, object]:
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
            self, params: Dict[str, object]) -> Tuple[str, List[object]]:
        conditions: List[str] = [
            "f.deleted_ts = 0",
            "(f.path LIKE ? OR f.rel_path LIKE ? OR f.fts_content LIKE ?)",
        ]
        sql_params: List[object] = [
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
            conditions.append(
                "(f.rel_path LIKE ? OR f.path LIKE ? OR "
                "(CASE WHEN instr(f.rel_path, '/') > 0 "
                "THEN substr(f.rel_path, instr(f.rel_path, '/') + 1) "
                "ELSE f.rel_path END) LIKE ?)"
            )
            sql_params.extend([like_pattern, like_pattern, like_pattern])

        if params["exclude_patterns"]:
            for pattern in params["exclude_patterns"]:
                excluded = glob_to_like(pattern)
                conditions.append("f.rel_path NOT LIKE ?")
                conditions.append("f.path NOT LIKE ?")
                sql_params.extend([excluded, excluded])

        return " AND ".join(conditions), sql_params

    def _execute_search_query(self, params: Dict[str, object]) -> List[Tuple]:
        """Build and execute search SQL query with importance scoring."""
        where_clause, sql_params = self._build_where_clause(params)
        select_sql = """
            SELECT f.path, f.repo, f.mtime, f.size, f.fts_content, f.rel_path, f.content,
                   IFNULL(smax.importance, 0.0) as importance
            FROM files f
            LEFT JOIN (
                SELECT path, MAX(importance_score) AS importance
                FROM symbols
                GROUP BY path
            ) smax ON smax.path = f.path
        """
        paging_params = [params["limit"], params["offset"]]
        sql = f"{select_sql} WHERE {where_clause} ORDER BY importance DESC, f.mtime DESC LIMIT ? OFFSET ?"
        try:
            return self.execute(sql, sql_params + paging_params).fetchall()
        except sqlite3.OperationalError as e:
            if not self._can_fallback_to_simple_query(e):
                raise
            logger.debug("Falling back to non-importance query due to operational error: %s", e)
            fallback_sql = (
                "SELECT f.path, f.repo, f.mtime, f.size, f.fts_content, f.rel_path, f.content, 0.0 as importance "
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
            path = self._row_val(r, "path", 0, "")
            repo_name = self._row_val(r, "repo", 1, "")
            mtime = self._row_val(r, "mtime", 2, 0)
            raw_size = self._row_val(r, "size", 3, 0)
            fts_content = self._row_val(r, "fts_content", 4, "")
            content_blob = self._row_val(r, "content", 6, "")
            importance = self._row_val(r, "importance", 7, 0.0)

            # Safe integer conversion for size
            try:
                size = int(raw_size or 0)
            except (ValueError, TypeError):
                size = 0

            # Extract snippet and count matches
            snippet, match_count = self._extract_snippet(fts_content, query, content_blob)

            hits.append(
                SearchHit(
                    repo=repo_name or "",
                    path=path or "",
                    score=1.0 + importance,  # Combine Importance into score
                    snippet=snippet,
                    mtime=int(mtime or 0),
                    size=size,
                    match_count=max(1, match_count),
                    file_type=os.path.splitext(
                        str(path))[1] if "." in str(path) else "",
                    hit_reason=f"Keyword (importance={importance:.1f})",
                )
            )

        return hits

    def _extract_snippet(self, fts_content: str, query: str, content_blob: object = "") -> Tuple[str, int]:
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

        if snippet or not content_blob:
            return snippet, match_count

        try:
            blob = content_blob
            if isinstance(blob, (bytes, bytearray)):
                raw = bytes(blob)
                if raw.startswith(b"ZLIB\0"):
                    raw = zlib.decompress(raw[5:])
                text = raw.decode("utf-8", errors="ignore")
            else:
                text = str(blob)
            if not text:
                return snippet, match_count
            lower = text.lower()
            q_lower = query.lower()
            fallback_count = lower.count(q_lower)
            idx = lower.find(q_lower)
            if idx >= 0:
                start = max(0, idx - 120)
                end = min(len(text), idx + 120)
                snippet = text[start:end]
                match_count = max(match_count, fallback_count)
        except Exception:
            pass
        return snippet, match_count

    def _calculate_total_count(
            self, params: Dict[str, object], hits: List[SearchHit]) -> int:
        """Calculate total result count based on total_mode."""
        total = len(hits)

        if params["total_mode"] == "exact":
            try:
                where_clause, count_params = self._build_where_clause(params)
                count_sql = f"SELECT COUNT(1) FROM files f WHERE {where_clause}"
                count_row = self.execute(count_sql, count_params).fetchone()
                total = int(self._row_val(count_row, "COUNT(1)", 0, 0) if count_row else 0)
            except Exception:
                total = len(hits)

        return total

    def _can_fallback_to_simple_query(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return "no such table: symbols" in msg or "no such column:" in msg

    @staticmethod
    def _row_val(row: object, key: str, index: int, default: object = None) -> object:
        if row is None:
            return default
        try:
            if hasattr(row, "keys"):
                return row[key]
        except Exception:
            pass
        if isinstance(row, (list, tuple)) and len(row) > index:
            return row[index]
        return default
