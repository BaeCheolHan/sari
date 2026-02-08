import sqlite3
from typing import Iterable, List, Optional, Tuple, Dict, Any
from .base import BaseRepository
from ..utils.compression import _compress
from ..models import FileDTO

class FileRepository(BaseRepository):
    def upsert_files_tx(self, cur: sqlite3.Cursor, rows: Iterable[tuple]) -> int:
        rows_list = []
        for r in rows:
            r_list = list(r)
            while len(r_list) < 20:
                r_list.append(None)

            path = r_list[0] or ""
            rel_path = r_list[1] or ""
            root_id = r_list[2] or ""
            repo = r_list[3] or ""
            mtime = int(r_list[4] or 0)
            size = int(r_list[5] or 0)
            raw_content = r_list[6] or ""
            content_hash = r_list[7] or ""
            fts_content = r_list[8] or ""
            last_seen_ts = int(r_list[9] or 0)
            deleted_ts = int(r_list[10] or 0)
            parse_status = str(r_list[11] or "none")
            parse_reason = str(r_list[12] or "none")
            ast_status = str(r_list[13] or "none")
            ast_reason = str(r_list[14] or "none")
            is_binary = int(r_list[15] or 0)
            is_minified = int(r_list[16] or 0)
            sampled = int(r_list[17] or 0)
            content_bytes = int(r_list[18] or 0)
            metadata_json = r_list[19] or "{}"

            compressed_content = _compress(raw_content)
            if not fts_content and parse_status == "ok" and not is_binary:
                fts_content = raw_content or ""

            rows_list.append(
                (
                    path,
                    rel_path,
                    root_id,
                    repo,
                    mtime,
                    size,
                    compressed_content,
                    content_hash,
                    fts_content,
                    last_seen_ts,
                    deleted_ts,
                    parse_status,
                    parse_reason,
                    ast_status,
                    ast_reason,
                    is_binary,
                    is_minified,
                    sampled,
                    content_bytes,
                    metadata_json,
                )
            )

        if not rows_list:
            return 0

        cur.executemany(
            """
            INSERT INTO files(
                path, rel_path, root_id, repo, mtime, size, content, content_hash, fts_content,
                last_seen_ts, deleted_ts, parse_status, parse_reason, ast_status, ast_reason,
                is_binary, is_minified, sampled, content_bytes, metadata_json
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              rel_path=excluded.rel_path,
              root_id=excluded.root_id,
              repo=excluded.repo,
              mtime=excluded.mtime,
              size=excluded.size,
              content=excluded.content,
              content_hash=excluded.content_hash,
              fts_content=excluded.fts_content,
              last_seen_ts=excluded.last_seen_ts,
              deleted_ts=excluded.deleted_ts,
              parse_status=excluded.parse_status,
              parse_reason=excluded.parse_reason,
              ast_status=excluded.ast_status,
              ast_reason=excluded.ast_reason,
              is_binary=excluded.is_binary,
              is_minified=excluded.is_minified,
              sampled=excluded.sampled,
              content_bytes=excluded.content_bytes,
              metadata_json=excluded.metadata_json
            WHERE excluded.mtime >= files.mtime;
            """,
            rows_list,
        )
        # Clear old symbols for updated paths to ensure consistency
        cur.executemany("DELETE FROM symbols WHERE path = ?", [(r[0],) for r in rows_list])
        return len(rows_list)

    def delete_path_tx(self, cur: sqlite3.Cursor, path: str) -> None:
        cur.execute("DELETE FROM files WHERE path = ?", (path,))
        cur.execute("DELETE FROM symbols WHERE path = ?", (path,))
        cur.execute("DELETE FROM symbol_relations WHERE from_path = ? OR to_path = ?", (path, path))
        cur.execute("DELETE FROM failed_tasks WHERE path = ?", (path,))

    def update_last_seen_tx(self, cur: sqlite3.Cursor, paths: List[str], ts: int) -> None:
        if not paths:
            return
        cur.executemany("UPDATE files SET last_seen_ts = ? WHERE path = ?", [(ts, p) for p in paths])

    def get_file_meta(self, path: str) -> Optional[Tuple[int, int, str]]:
        row = self.execute("SELECT mtime, size, metadata_json FROM files WHERE path = ?", (path,)).fetchone()
        if not row:
            return None
        ch = ""
        try:
            import json
            ch = json.loads(row["metadata_json"]).get("content_hash", "")
        except:
            pass
        return (row["mtime"], row["size"], ch)

    def get_unseen_paths(self, ts: int) -> List[str]:
        rows = self.execute("SELECT path FROM files WHERE last_seen_ts < ?", (ts,)).fetchall()
        return [r["path"] for r in rows]

    def upsert_repo_meta_tx(self, cur: sqlite3.Cursor, repo_name: str, tags: str = "", domain: str = "", description: str = "", priority: int = 0) -> None:
        cur.execute(
            """
            INSERT OR REPLACE INTO repo_meta (repo_name, tags, domain, description, priority)
            VALUES (?, ?, ?, ?, ?)
            """,
            (repo_name, tags, domain, description, priority)
        )

    def upsert_failed_tasks_tx(self, cur: sqlite3.Cursor, rows: Iterable[tuple]) -> int:
        rows_list = [list(r) for r in rows]
        if not rows_list:
            return 0
        normalized = []
        for r in rows_list:
            while len(r) < 7:
                r.append(0)
            normalized.append(tuple(r[:7]))
        cur.executemany(
            """
            INSERT INTO failed_tasks(path, root_id, attempts, error, ts, next_retry, metadata_json)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              attempts=excluded.attempts,
              error=excluded.error,
              ts=excluded.ts,
              next_retry=excluded.next_retry,
              metadata_json=excluded.metadata_json;
            """,
            normalized,
        )
        return len(normalized)

    def clear_failed_tasks_tx(self, cur: sqlite3.Cursor, paths: Iterable[str]) -> int:
        paths_list = [p for p in paths if p]
        if not paths_list:
            return 0
        cur.executemany("DELETE FROM failed_tasks WHERE path = ?", [(p,) for p in paths_list])
        return len(paths_list)

    def list_failed_tasks_ready(self, now_ts: int, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.execute(
            """
            SELECT path, root_id, attempts, error, ts, next_retry, metadata_json
            FROM failed_tasks
            WHERE next_retry <= ?
            ORDER BY next_retry ASC
            LIMIT ?;
            """,
            (int(now_ts), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_failed_tasks(self) -> Tuple[int, int]:
        row = self.execute("SELECT COUNT(*) AS c FROM failed_tasks").fetchone()
        total = int(row["c"]) if row else 0
        row2 = self.execute("SELECT COUNT(*) AS c FROM failed_tasks WHERE attempts >= 3").fetchone()
        high = int(row2["c"]) if row2 else 0
        return total, high

    def list_files(self, limit: int = 50, repo: Optional[str] = None, root_ids: Optional[List[str]] = None) -> List[Dict]:
        sql = "SELECT path, size, repo FROM files WHERE deleted_ts = 0"
        params: List[Any] = []
        if repo:
            sql += " AND repo = ?"
            params.append(repo)
        if root_ids:
            placeholders = ",".join(["?"] * len(root_ids))
            sql += f" AND root_id IN ({placeholders})"
            params.extend(root_ids)
        sql += " LIMIT ?"
        params.append(limit)
        cursor = self.execute(sql, params)
        return [{"path": r["path"], "size": r["size"], "repo": r["repo"]} for r in cursor.fetchall()]

    def get_repo_stats(self, root_ids: Optional[List[str]] = None) -> Dict[str, int]:
        sql = "SELECT repo, COUNT(path) AS c FROM files WHERE deleted_ts = 0"
        params = []
        if root_ids:
            placeholders = ",".join(["?"] * len(root_ids))
            sql += f" AND root_id IN ({placeholders})"
            params.extend(root_ids)
        sql += " GROUP BY repo"
        cursor = self.execute(sql, params)
        return {r["repo"]: r["c"] for r in cursor.fetchall()}
