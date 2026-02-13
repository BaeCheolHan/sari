import sqlite3
import re
import zlib
import hashlib
from typing import Dict, List, Optional, Tuple
from .models import SearchHit, SearchOptions
from .ranking import snippet_around, get_file_extension, match_path_pattern
from .scoring import ScoringPolicy
from .engine.tantivy_engine import TantivyEngine

from .db.storage import GlobalStorageManager


class SearchEngine:
    def __init__(self, db, scoring_policy: ScoringPolicy = None,
                 tantivy_engine: Optional[TantivyEngine] = None):
        import logging
        self.logger = logging.getLogger("sari.search_engine")
        self.db = db
        self.scoring_policy = scoring_policy or ScoringPolicy()
        self.tantivy_engine = tantivy_engine
        
        # Ensure tantivy_engine has a logger for error reporting
        if self.tantivy_engine and not getattr(self.tantivy_engine, "logger", None):
            self.tantivy_engine.logger = self.logger
            
        self._snippet_cache: Dict[str, str] = {}
        self._snippet_lru: List[str] = []
        self.storage = GlobalStorageManager.get_instance(db)

    def search_l2_only(
            self, opts: SearchOptions) -> Tuple[List[SearchHit], Dict[str, object]]:
        q = (opts.query or "").strip()
        if not q:
            return [], {"total": 0, "engine": "l2", "partial": True}
        from .workspace import WorkspaceManager
        root_ids = [WorkspaceManager.normalize_path(rid) for rid in opts.root_ids] if opts.root_ids else None
        meta: Dict[str, object] = {
            "engine": "l2",
            "partial": True,
            "db_health": "error",
            "coverage": "l2-only",
        }
        try:
            recent_rows = self.storage.get_recent_files(
                q, root_ids=root_ids, limit=opts.limit)
            hits = self._process_sqlite_rows(recent_rows, opts)
            for h in hits:
                h.hit_reason = "L2 Cache (Degraded)"
                h.score = 3.0
            return hits[:opts.limit], meta
        except Exception as e:
            meta["db_error"] = str(e)
            return [], meta

    def search(
            self, opts: SearchOptions) -> Tuple[List[SearchHit], Dict[str, object]]:
        """Enhanced search with Score Normalization and Merged Results."""
        if hasattr(self.db, "coordinator") and self.db.coordinator:
            self.db.coordinator.notify_search_start()

        try:
            q = (opts.query or "").strip()
            if not q:
                return [], {"total": 0}

            from .workspace import WorkspaceManager
            root_ids = [WorkspaceManager.normalize_path(rid) for rid in opts.root_ids] if opts.root_ids else None
            meta: Dict[str, object] = {
                "engine": "hybrid",
                "partial": False,
                "db_health": "ok",
                "db_error": "",
            }

            # 1. L2 Cache (Recent) - 최상위 우선순위
            try:
                recent_rows = self.storage.get_recent_files(
                    q, root_ids=root_ids, limit=opts.limit)
                recent_hits = self._process_sqlite_rows(recent_rows, opts)
                for h in recent_hits:
                    h.hit_reason = "L2 Cache (Recent)"
                    h.score = 100.0  # 매우 높은 고정 점수
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
                    hits = self.tantivy_engine.search(
                        q, root_ids=root_ids, limit=opts.limit)
                    if hits:
                        t_hits = self._process_tantivy_hits(hits, opts)
                        # Tantivy 점수 정규화
                        max_t = max((h.score for h in t_hits), default=1.0)
                        if max_t <= 0:
                            max_t = 1.0
                        for h in t_hits:
                            h.score = (h.score / max_t) * 10.0
                            if h.path not in seen_paths:
                                all_hits.append(h)
                                seen_paths.add(h.path)
                except Exception as te:
                    import logging
                    logging.getLogger("sari.search").debug(
                        "Tantivy search failed: %s", te)

            # 3. SQLite fallback through DB facade (engine boundary kept in
            # db/repository layer)
            if (not all_hits or len(all_hits) < opts.limit) and hasattr(self.db, "_search_sqlite"):
                try:
                    sqlite_hits, sqlite_meta = self.db._search_sqlite(opts)
                    if isinstance(sqlite_meta, dict):
                        if "total" in sqlite_meta:
                            meta["sqlite_total"] = sqlite_meta.get("total")
                        if "total_mode" in sqlite_meta:
                            meta["sqlite_total_mode"] = sqlite_meta.get(
                                "total_mode")
                    for sqlite_hit in sqlite_hits:
                        if opts.file_types:
                            allowed_types = {
                                str(file_type).lower().lstrip(".")
                                for file_type in (opts.file_types or [])
                            }
                            hit_type = get_file_extension(
                                sqlite_hit.path).lower().lstrip(".")
                            if hit_type not in allowed_types:
                                continue
                        if opts.path_pattern:
                            rel_path = sqlite_hit.path.split("/", 1)[1] if "/" in sqlite_hit.path else sqlite_hit.path
                            if not match_path_pattern(sqlite_hit.path, rel_path, opts.path_pattern):
                                continue
                        if sqlite_hit.path in seen_paths:
                            continue
                        all_hits.append(sqlite_hit)
                        seen_paths.add(sqlite_hit.path)
                except sqlite3.Error as e:
                    meta["db_health"] = "error"
                    meta["db_error"] = str(e)
                    meta["partial"] = True
                except Exception as e:
                    meta["db_health"] = "error"
                    meta["db_error"] = str(e)
                    meta["partial"] = True

            # Root 우선 가중치 (workspace root에 속한 결과를 앞쪽으로)
            if root_ids:
                for h in all_hits:
                    if h.path and any(h.path.startswith(rid + "/") for rid in root_ids):
                        h.score += 50.0

            # 스코프 매칭 근거 기록
            if root_ids or opts.repo:
                for h in all_hits:
                    h.scope_reason = f"root_ids={root_ids or 'any'}; repo={opts.repo or 'any'}"

            # 최종 정렬: 점수 내림차순 -> 시간 내림차순
            all_hits.sort(key=lambda x: (-x.score, -x.mtime))
            return all_hits[:opts.limit], meta
        finally:
            if hasattr(self.db, "coordinator") and self.db.coordinator:
                self.db.coordinator.notify_search_end()

    def _process_sqlite_rows(
            self,
            rows: List[object],
            opts: SearchOptions) -> List[SearchHit]:
        hits: List[SearchHit] = []
        for r in rows:
            row = self._row_to_mapping(r)
            if row:
                path = str(row.get("path", ""))
                rel_path = str(row.get("rel_path", path))
                repo = str(row.get("repo", ""))
                mtime = int(row.get("mtime", 0) or 0)
                size = int(row.get("size", 0) or 0)
                content = row.get("content", "")
            else:
                # Flexible tuple unpacking fallback
                if isinstance(r, (list, tuple)) and len(r) >= 7:
                    # FTS shape: (path, rel_path, root_id, repo, mtime, size, content)
                    path, rel_path, _, repo, mtime, size, content, *_ = r
                elif isinstance(r, (list, tuple)) and len(r) == 6:
                    # Legacy shape: (path, root_id, repo, mtime, size, content)
                    path, _, repo, mtime, size, content = r
                    rel_path = path
                else:
                    continue

            # Strict Filtering
            if opts.repo and repo != opts.repo:
                continue

            # File Type Filter
            if opts.file_types:
                ext = get_file_extension(path).lower().lstrip(".")
                allowed = [t.lower().lstrip(".") for t in opts.file_types]
                if ext not in allowed:
                    continue

            # Path Pattern Filter (Glob)
            if opts.path_pattern:
                if not match_path_pattern(path, rel_path, opts.path_pattern):
                    continue

            hits.append(SearchHit(
                repo=repo,
                path=path,
                score=1.0,
                snippet=self._snippet_for(path, opts.query, content, case_sensitive=bool(opts.case_sensitive)),
                mtime=mtime,
                size=size,
                match_count=1,
                file_type=get_file_extension(path),
                hit_reason="SQLite Fallback",
            ))
        return hits

    def _process_tantivy_hits(
            self,
            hits: List[Dict[str, object]],
            opts: SearchOptions) -> List[SearchHit]:
        results: List[SearchHit] = []
        for h in hits:
            path = h.get("path", "")
            repo = h.get("repo", "")
            rel_path = h.get("rel_path", path)

            # Strict Filtering
            if opts.repo and repo != opts.repo:
                continue

            # File Type Filter
            if opts.file_types:
                ext = get_file_extension(path).lower().lstrip(".")
                if ext not in [t.lower().lstrip(".") for t in opts.file_types]:
                    continue

            # Path Pattern Filter
            if opts.path_pattern:
                if not match_path_pattern(path, rel_path, opts.path_pattern):
                    continue

            # Double check with DB to filter out deleted files not yet purged
            # from Tantivy
            is_deleted = False
            try:
                row = self.db._get_conn().execute(
                    "SELECT deleted_ts FROM files WHERE path=?", (path,)).fetchone()
                if int(self._row_get(row, "deleted_ts", 0, 0) or 0) > 0:
                    is_deleted = True
            except Exception:
                pass

            if is_deleted:
                continue

            snippet = f"Match in {path}"
            try:
                content = self.db.read_file(path)
                if content:
                    snippet = self._snippet_for(path, opts.query, content, case_sensitive=bool(opts.case_sensitive))
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
        # Support for special characters in tokens (e.g. $, @, #)
        tokens = re.findall(
            r'\(|\)|"[^"]+"|\bAND\b|\bOR\b|\bNOT\b|\bNEAR/\d+\b|\bNEAR\b|[^()"\s]+',
            raw,
            flags=re.IGNORECASE)
        if not tokens:
            return raw.replace('"', " ")
        out = []
        for idx, t in enumerate(tokens):
            upper = t.upper()
            if upper.startswith("NEAR"):
                # NEAR must have term on both sides.
                if out and idx < len(tokens) - 1:
                    out.append(upper)
                continue
            if upper in {"AND", "OR", "NOT"}:
                if out and idx < len(tokens) - 1:
                    out.append(upper)
                continue
            if t in {"(", ")"}:
                out.append(t)
                continue
            if t.startswith('"') and t.endswith('"'):
                t = t[1:-1]
            out.append(f'"{t}"')
        if out and out[-1].upper() in {"AND", "OR", "NOT", "NEAR"}:
            out.pop()
        return " ".join(out)

    def _snippet_for(self, path: str, query: str, content: str, *, case_sensitive: bool = False) -> str:
        # Use stable content digest to avoid process-dependent cache keys.
        if content:
            if isinstance(content, (bytes, bytearray)):
                content_bytes = bytes(content)
            else:
                content_bytes = str(content).encode("utf-8", errors="ignore")
            content_tag = hashlib.blake2b(content_bytes, digest_size=8).hexdigest()
        else:
            content_tag = "0"
        cache_key = f"{path}\0{query}\0{content_tag}\0{case_sensitive}"
        cached = self._snippet_cache.get(cache_key)
        if cached is not None:
            return cached
        max_bytes = 200_000
        try:
            if getattr(self.db, "settings", None):
                max_bytes = int(
                    getattr(
                        self.db.settings,
                        "SNIPPET_MAX_BYTES",
                        max_bytes))
        except Exception:
            pass
        if isinstance(content, (bytes, bytearray)):
            raw = bytes(content)
            try:
                content = zlib.decompress(raw).decode("utf-8", errors="ignore")
            except Exception:
                content = raw.decode("utf-8", errors="ignore")
        elif not isinstance(content, str):
            content = str(content)

        if len(content) > max_bytes:
            q = str(query or "")
            if q:
                lower = content.lower()
                q_lower = q.lower()
                idx = lower.find(q_lower)
                if idx >= 0:
                    half = max_bytes // 2
                    start = max(0, idx - half)
                    end = min(len(content), start + max_bytes)
                    if end - start < max_bytes:
                        start = max(0, end - max_bytes)
                    content = content[start:end]
                else:
                    content = content[:max_bytes]
            else:
                content = content[:max_bytes]
        snippet = snippet_around(content, [query], 3, highlight=True, case_sensitive=case_sensitive)
        cache_size = 128
        try:
            if getattr(self.db, "settings", None):
                cache_size = int(
                    getattr(
                        self.db.settings,
                        "SNIPPET_CACHE_SIZE",
                        cache_size))
        except Exception:
            pass
        if cache_size > 0:
            if cache_key in self._snippet_cache:
                try:
                    self._snippet_lru.remove(cache_key)
                except ValueError:
                    pass
            self._snippet_cache[cache_key] = snippet
            self._snippet_lru.append(cache_key)
            while len(self._snippet_lru) > cache_size:
                old = self._snippet_lru.pop(0)
                self._snippet_cache.pop(old, None)
        return snippet

    @staticmethod
    def _row_to_mapping(row: object) -> Optional[Dict[str, object]]:
        if row is None:
            return None
        try:
            if hasattr(row, "keys"):
                return {k: row[k] for k in row.keys()}
        except Exception:
            return None
        return None

    @staticmethod
    def _row_get(row: object, key: str, index: int, default: object = None) -> object:
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

    def repo_candidates(self, q: str, limit: int = 3,
                        root_ids: Optional[List[str]] = None) -> List[Dict[str, object]]:
        q = (q or "").strip()
        if not q:
            return []
        if hasattr(self.db, "repo_candidates_sqlite"):
            rows = self.db.repo_candidates_sqlite(
                q, limit=limit, root_ids=root_ids)
            return [
                {
                    "repo": row.get("repo", ""),
                    "score": int(row.get("score", 0)),
                    "evidence": row.get("evidence", ""),
                }
                for row in rows
            ]
        return []
