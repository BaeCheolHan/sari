import os
from typing import Any, Dict, List, Optional, Tuple

from sari.core.models import SearchHit

from .base import BaseRepository


class SearchRepository(BaseRepository):
    def repo_candidates(self, q: str, limit: int = 3, root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
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

    def search_semantic(self, query_vector: List[float], limit: int = 10, **kwargs) -> List[SearchHit]:
        """Find meaningful code blocks using Optimized Vector Cosine Similarity."""
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
        if not rows: return []

        # Convert query_vector to numpy for speed
        if has_numpy:
            q_vec = np.array(query_vector, dtype=np.float32)
            q_norm = np.linalg.norm(q_vec)
            if q_norm > 0: q_vec /= q_norm # Normalize
        else:
            q_vec = query_vector
            q_norm = math.sqrt(sum(x*x for x in q_vec))

        scored_hits = []
        for entity_id, entity_type, vec_blob, root_id in rows:
            if not vec_blob: continue
            
            # 1. Faster Unpacking
            vec = struct.unpack(f"{len(vec_blob)//4}f", vec_blob)
            
            # 2. Advanced Similarity Calculation
            if has_numpy:
                v = np.array(vec, dtype=np.float32)
                v_norm = np.linalg.norm(v)
                if v_norm == 0: continue
                # Cosine Similarity via Dot Product of normalized vectors
                score = np.dot(q_vec, v) / v_norm 
            else:
                dot = sum(a * b for a, b in zip(q_vec, vec))
                v_norm = math.sqrt(sum(x*x for x in vec))
                if v_norm == 0: continue
                score = dot / (q_norm * v_norm)
            
            if score > 0.4: # Slightly lower threshold for semantic nuances
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
        query = str(getattr(opts, "query", "") or "").strip()
        if not query:
            return [], {"total": 0, "total_mode": getattr(opts, "total_mode", "exact")}

        limit = int(getattr(opts, "limit", 50) or 50)
        offset = int(getattr(opts, "offset", 0) or 0)
        repo = getattr(opts, "repo", None)
        root_ids = getattr(opts, "root_ids", None) or []
        total_mode = getattr(opts, "total_mode", "exact")

        lq = f"%{query}%"
        # JOIN with symbols to get importance_score (if it exists)
        sql = """
            SELECT f.path, f.repo, f.mtime, f.size, f.fts_content, f.rel_path,
                   IFNULL((SELECT MAX(importance_score) FROM symbols s WHERE s.path = f.path), 0.0) as importance
            FROM files f 
            WHERE f.deleted_ts = 0 AND (f.path LIKE ? OR f.rel_path LIKE ? OR f.fts_content LIKE ?)
        """
        params: List[Any] = [lq, lq, lq]
        if repo:
            sql += " AND f.repo = ?"
            params.append(repo)
        if root_ids:
            placeholders = ",".join(["?"] * len(root_ids))
            sql += f" AND f.root_id IN ({placeholders})"
            params.extend(root_ids)
            
        # Order by Importance DESC first, then by recency
        sql += " ORDER BY importance DESC, f.mtime DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.execute(sql, params).fetchall()
        hits: List[SearchHit] = []
        for r in rows:
            # Flexible row unpacking
            path, repo_name, mtime, size, fts_content, rel_path, importance = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
            
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
            
            hits.append(
                SearchHit(
                    repo=repo_name or "",
                    path=path or "",
                    score=1.0 + importance, # Combine Importance into score
                    snippet=snippet,
                    mtime=int(mtime or 0),
                    size=int(size or 0),
                    match_count=max(1, match_count),
                    file_type=os.path.splitext(str(path))[1] if "." in str(path) else "",
                    hit_reason=f"Keyword (importance={importance:.1f})",
                )
            )

        total = len(hits)
        if total_mode == "exact":
            try:
                count_sql = (
                    "SELECT COUNT(1) FROM files WHERE deleted_ts = 0 AND (path LIKE ? OR rel_path LIKE ? OR fts_content LIKE ?)"
                )
                count_params: List[Any] = [lq, lq, lq]
                if repo:
                    count_sql += " AND repo = ?"
                    count_params.append(repo)
                if root_ids:
                    placeholders = ",".join(["?"] * len(root_ids))
                    count_sql += f" AND root_id IN ({placeholders})"
                    count_params.extend(root_ids)
                total = int(self.execute(count_sql, count_params).fetchone()[0])
            except Exception:
                total = len(hits)

        return hits, {"total": total, "total_mode": total_mode}
