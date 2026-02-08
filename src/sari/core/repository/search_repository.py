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
        sql = (
            "SELECT path, repo, mtime, size, fts_content, rel_path "
            "FROM files WHERE deleted_ts = 0 AND (path LIKE ? OR rel_path LIKE ? OR fts_content LIKE ?)"
        )
        params: List[Any] = [lq, lq, lq]
        if repo:
            sql += " AND repo = ?"
            params.append(repo)
        if root_ids:
            placeholders = ",".join(["?"] * len(root_ids))
            sql += f" AND root_id IN ({placeholders})"
            params.extend(root_ids)
        sql += " ORDER BY mtime DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.execute(sql, params).fetchall()
        hits: List[SearchHit] = []
        for path, repo_name, mtime, size, fts_content, rel_path in rows:
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
            file_type = ""
            try:
                file_type = os.path.splitext(str(path))[1]
            except Exception:
                file_type = ""
            hits.append(
                SearchHit(
                    repo=repo_name or "",
                    path=path or "",
                    score=1.0,
                    snippet=snippet,
                    mtime=int(mtime or 0),
                    size=int(size or 0),
                    match_count=max(1, match_count),
                    file_type=file_type,
                    hit_reason="fts" if fts_content else "path",
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
