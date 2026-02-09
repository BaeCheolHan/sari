import sqlite3
import re
import time
from typing import List, Tuple, Optional, Any, Dict
from .models import SearchHit, SearchOptions
from .ranking import snippet_around, get_file_extension
from .scoring import ScoringPolicy
from .engine.tantivy_engine import TantivyEngine

from .db.storage import GlobalStorageManager

class SearchEngine:
    def __init__(self, db, scoring_policy: ScoringPolicy = None, tantivy_engine: Optional[TantivyEngine] = None):
        self.db = db
        self.scoring_policy = scoring_policy or ScoringPolicy()
        self.tantivy_engine = tantivy_engine
        self._snippet_cache: Dict[tuple, str] = {}
        self._snippet_lru: List[tuple] = []
        self.storage = GlobalStorageManager.get_instance(db)

    def search_l2_only(self, opts: SearchOptions) -> Tuple[List[SearchHit], Dict[str, Any]]:
        q = (opts.query or "").strip()
        if not q:
            return [], {"total": 0, "engine": "l2", "partial": True}
        from .workspace import WorkspaceManager
        root_id = WorkspaceManager.normalize_path(list(opts.root_ids)[0]) if opts.root_ids else None
        meta: Dict[str, Any] = {"engine": "l2", "partial": True, "db_health": "error", "coverage": "l2-only"}
        try:
            recent_rows = self.storage.get_recent_files(q, root_id=root_id, limit=opts.limit)
            hits = self._process_sqlite_rows(recent_rows, opts)
            for h in hits:
                h.hit_reason = "L2 Cache (Degraded)"
                h.score = 3.0
            return hits[:opts.limit], meta
        except Exception as e:
            meta["db_error"] = str(e)
            return [], meta

    def search_v2(self, opts: SearchOptions) -> Tuple[List[SearchHit], Dict[str, Any]]:
        """Enhanced search with Score Normalization and Merged Results."""
        if hasattr(self.db, "coordinator") and self.db.coordinator:
            self.db.coordinator.notify_search_start()

        try:
            q = (opts.query or "").strip()
            if not q: return [], {"total": 0}

            from .workspace import WorkspaceManager
            root_id = WorkspaceManager.normalize_path(list(opts.root_ids)[0]) if opts.root_ids else None
            meta: Dict[str, Any] = {"engine": "hybrid", "partial": False, "db_health": "ok", "db_error": ""}

            # 1. L2 Cache (Recent) - 최상위 우선순위
            try:
                recent_rows = self.storage.get_recent_files(q, root_id=root_id, limit=opts.limit)
                recent_hits = self._process_sqlite_rows(recent_rows, opts)
                for h in recent_hits:
                    h.hit_reason = "L2 Cache (Recent)"
                    h.score = 100.0 # 매우 높은 고정 점수
            except Exception as e:
                recent_hits = []
                meta["db_health"] = "error"
                meta["db_error"] = str(e)
                meta["partial"] = True
            
            seen_paths = {h.path for h in recent_hits}
            all_hits = recent_hits

            # 2. Tantivy (Primary DB Search)
            if self.tantivy_engine and not opts.use_regex:
                try:
                    hits = self.tantivy_engine.search(q, root_id=root_id, limit=opts.limit)
                    if hits:
                        t_hits = self._process_tantivy_hits(hits, opts)
                        # Tantivy 점수 정규화
                        max_t = max((h.score for h in t_hits), default=1.0)
                        for h in t_hits:
                            h.score = (h.score / max_t) * 10.0
                            if h.path not in seen_paths:
                                all_hits.append(h)
                                seen_paths.add(h.path)
                except Exception: pass

            # 3. SQLite (Fallback/Secondary DB Search)
            if not all_hits or len(all_hits) < opts.limit:
                try:
                    cur = self.db._get_conn().cursor()
                    # Try FTS first
                    is_fts = False
                    if getattr(self.db, "settings", None) and getattr(self.db.settings, "ENABLE_FTS", False):
                        try:
                            fts_q = self._fts_query(q)
                            sql = ("SELECT f.path, f.rel_path, f.root_id, f.repo, f.mtime, f.size, f.content "
                                   "FROM files_fts JOIN files f ON files_fts.rowid = f.rowid "
                                   "WHERE files_fts MATCH ? AND f.deleted_ts = 0")
                            params = [fts_q]
                            if root_id: sql += " AND f.root_id = ?"; params.append(root_id)
                            if opts.repo: sql += " AND f.repo = ?"; params.append(opts.repo)
                            sql += " LIMIT ?"
                            params.append(opts.limit)
                            cur.execute(sql, params)
                            db_hits = self._process_sqlite_rows(cur.fetchall(), opts)
                            for h in db_hits:
                                h.score = 5.0
                                if h.path not in seen_paths:
                                    all_hits.append(h)
                                    seen_paths.add(h.path)
                            is_fts = True
                        except Exception: pass
                    
                    # Broad LIKE fallback if still no results or FTS skipped
                    if not all_hits or len(all_hits) < opts.limit:
                        like_q = f"%{q}%"
                        sql = (
                            "SELECT path, rel_path, root_id, repo, mtime, size, content FROM files "
                            "WHERE (path LIKE ? OR rel_path LIKE ? OR fts_content LIKE ?) AND deleted_ts = 0"
                        )
                        params = [like_q, like_q, like_q]
                        if root_id:
                            sql += " AND root_id = ?"
                            params.append(root_id)
                        if opts.repo:
                            sql += " AND repo = ?"
                            params.append(opts.repo)
                        sql += " LIMIT ?"
                        params.append(opts.limit)
                        cur.execute(sql, params)
                        db_hits = self._process_sqlite_rows(cur.fetchall(), opts)
                        for h in db_hits:
                            h.score = 4.0
                            if h.path not in seen_paths:
                                all_hits.append(h)
                                seen_paths.add(h.path)
                except sqlite3.Error as e:
                    meta["db_health"] = "error"
                    meta["db_error"] = str(e)
                    meta["partial"] = True

            # Root 우선 가중치 (workspace root에 속한 결과를 앞쪽으로)
            if root_id:
                for h in all_hits:
                    if h.path and h.path.startswith(root_id + "/"):
                        h.score += 50.0

            # 스코프 매칭 근거 기록
            if root_id or opts.repo:
                for h in all_hits:
                    h.scope_reason = f"root_id={root_id or 'any'}; repo={opts.repo or 'any'}"

            # 최종 정렬: 점수 내림차순 -> 시간 내림차순
            all_hits.sort(key=lambda x: (-x.score, -x.mtime))
            return all_hits[:opts.limit], meta
        finally:
            if hasattr(self.db, "coordinator") and self.db.coordinator:
                self.db.coordinator.notify_search_end()

    def _process_sqlite_rows(self, rows: list, opts: SearchOptions) -> List[SearchHit]:
        import fnmatch
        hits: List[SearchHit] = []
        for r in rows:
            # Legacy/normalized row: (path, root_id, repo, mtime, size, content)
            # FTS row: (path, rel_path, root_id, repo, mtime, size, content)
            if len(r) >= 7:
                path, rel_path, root_id, repo, mtime, size, content = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
            elif len(r) >= 6:
                path, rel_path, root_id, repo, mtime, size, content = r[0], r[1], r[2], r[3], r[4], r[5], "" # Missing content
            else:
                continue
            
            # Strict Filtering
            if opts.repo and repo != opts.repo: continue
            
            # File Type Filter
            if opts.file_types:
                ext = get_file_extension(path).lower().lstrip(".")
                allowed = [t.lower().lstrip(".") for t in opts.file_types]
                if ext not in allowed:
                    continue
            
            # Path Pattern Filter (Glob)
            if opts.path_pattern:
                pat = opts.path_pattern
                # Match against rel_path or path
                if not fnmatch.fnmatch(rel_path, pat) and not fnmatch.fnmatch(path, pat):
                    continue

            hits.append(SearchHit(
                repo=repo,
                path=path,
                score=1.0,
                snippet=self._snippet_for(path, opts.query, content),
                mtime=mtime,
                size=size,
                match_count=1,
                file_type=get_file_extension(path),
                hit_reason="SQLite Fallback",
            ))
        return hits

    def _process_tantivy_hits(self, hits: list, opts: SearchOptions) -> List[SearchHit]:
        import fnmatch
        results: List[SearchHit] = []
        for h in hits:
            path = h.get("path", "")
            repo = h.get("repo", "")
            rel_path = h.get("rel_path", path)
            
            # Strict Filtering
            if opts.repo and repo != opts.repo: continue

            # File Type Filter
            if opts.file_types:
                ext = get_file_extension(path).lower()
                if ext not in [t.lower().lstrip(".") for t in opts.file_types]:
                    continue
            
            # Path Pattern Filter
            if opts.path_pattern:
                if not fnmatch.fnmatch(rel_path, opts.path_pattern) and not fnmatch.fnmatch(path, opts.path_pattern):
                    continue

            # Double check with DB to filter out deleted files not yet purged from Tantivy
            is_deleted = False
            try:
                row = self.db._get_conn().execute("SELECT deleted_ts FROM files WHERE path=?", (path,)).fetchone()
                if row and row[0] > 0:
                    is_deleted = True
            except Exception:
                pass
            
            if is_deleted:
                continue

            snippet = f"Match in {path}"
            try:
                content = self.db.read_file(path)
                if content:
                    snippet = self._snippet_for(path, opts.query, content)
            except Exception:
                pass
            results.append(SearchHit(
                repo=h.get("repo", ""),
                path=path,
                score=h.get("score", 0.0),
                snippet=snippet,
                mtime=h.get("mtime", 0),
                size=h.get("size", 0),
                match_count=1,
                file_type=get_file_extension(path),
                hit_reason="Tantivy Search",
            ))
        return results

    def _fts_query(self, q: str) -> str:
        raw = (q or "").strip()
        if not raw:
            return raw
        # Normalize common OR separators
        raw = raw.replace("||", " OR ").replace("|", " OR ")
        tokens = re.findall(r'\(|\)|"[^"]+"|\bAND\b|\bOR\b|\bNOT\b|\bNEAR/\d+\b|\bNEAR\b|[0-9A-Za-z_\u00A1-\uFFFF]+', raw, flags=re.IGNORECASE)
        if not tokens:
            return raw.replace('"', " ")
        out = []
        for t in tokens:
            upper = t.upper()
            if upper in {"AND", "OR", "NOT"} or upper.startswith("NEAR"):
                out.append(upper)
                continue
            if t in {"(", ")"}:
                out.append(t)
                continue
            if t.startswith('"') and t.endswith('"'):
                t = t[1:-1]
            out.append(f'"{t}"')
        return " ".join(out)

    def _snippet_for(self, path: str, query: str, content: str) -> str:
        cache_key = (path, query)
        cached = self._snippet_cache.get(cache_key)
        if cached is not None:
            return cached
        max_bytes = 200_000
        try:
            if getattr(self.db, "settings", None):
                max_bytes = int(getattr(self.db.settings, "SNIPPET_MAX_BYTES", max_bytes))
        except Exception:
            pass
        if len(content) > max_bytes:
            content = content[:max_bytes]
        snippet = snippet_around(content, [query], 3, highlight=True)
        cache_size = 128
        try:
            if getattr(self.db, "settings", None):
                cache_size = int(getattr(self.db.settings, "SNIPPET_CACHE_SIZE", cache_size))
        except Exception:
            pass
        if cache_size > 0:
            self._snippet_cache[cache_key] = snippet
            self._snippet_lru.append(cache_key)
            while len(self._snippet_lru) > cache_size:
                old = self._snippet_lru.pop(0)
                self._snippet_cache.pop(old, None)
        return snippet

    def repo_candidates(self, q: str, limit: int = 3, root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        q = (q or "").strip()
        if not q:
            return []
        root_id = list(root_ids)[0] if root_ids else None
        sql = "SELECT repo, COUNT(1) as c FROM files"
        sql, params = self.db.apply_root_filter(sql, root_id)
        sql += " AND (path LIKE ? OR rel_path LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
        sql += " GROUP BY repo ORDER BY c DESC LIMIT ?"
        params.append(limit)
        cur = self.db._get_conn().cursor()
        cur.execute(sql, params)
        return [{"repo": r[0], "score": int(r[1]), "evidence": ""} for r in cur.fetchall()]
