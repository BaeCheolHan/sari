import sqlite3
from typing import Iterable, List, Optional, Tuple, Dict, Any
from .base import BaseRepository
from ..utils.compression import _compress

class FileRepository(BaseRepository):
    def upsert_files_tx(self, cur: sqlite3.Cursor, rows: Iterable[tuple]) -> int:
        rows_list = []
        for r in rows:
            r_list = list(r)
            if len(r_list) < 14:
                while len(r_list) < 6:
                    r_list.append(0)
                defaults = ["none", "none", "none", "none", 0, 0, 0, 0]
                r_list.extend(defaults[: (14 - len(r_list))])
            
            raw_content = r_list[4]
            compressed_content = _compress(raw_content)
            parse_status = str(r_list[6]) if len(r_list) > 6 else "ok"
            is_binary = int(r_list[10]) if len(r_list) > 10 else 0
            fts_content = ""
            if parse_status == "ok" and not is_binary:
                fts_content = raw_content or ""
                
            rows_list.append((
                r_list[0], r_list[1], r_list[2], r_list[3], compressed_content, fts_content,
                r_list[5], r_list[6], r_list[7], r_list[8], r_list[9],
                r_list[10], r_list[11], r_list[12], r_list[13]
            ))
            
        if not rows_list:
            return 0
            
        cur.executemany(
            """
            INSERT INTO files(path, repo, mtime, size, content, fts_content, last_seen, parse_status, parse_reason, ast_status, ast_reason, is_binary, is_minified, sampled, content_bytes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              repo=excluded.repo,
              mtime=excluded.mtime,
              size=excluded.size,
              content=excluded.content,
              fts_content=excluded.fts_content,
              last_seen=excluded.last_seen,
              parse_status=excluded.parse_status,
              parse_reason=excluded.parse_reason,
              ast_status=excluded.ast_status,
              ast_reason=excluded.ast_reason,
              is_binary=excluded.is_binary,
              is_minified=excluded.is_minified,
              sampled=excluded.sampled,
              content_bytes=excluded.content_bytes
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
        cur.executemany("UPDATE files SET last_seen = ? WHERE path = ?", [(ts, p) for p in paths])

    def get_file_meta(self, path: str) -> Optional[Tuple[int, int]]:
        row = self.execute("SELECT mtime, size FROM files WHERE path = ?", (path,)).fetchone()
        return (row["mtime"], row["size"]) if row else None

    def get_unseen_paths(self, ts: int) -> List[str]:
        rows = self.execute("SELECT path FROM files WHERE last_seen < ?", (ts,)).fetchall()
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
            while len(r) < 5:
                r.append(0)
            normalized.append(tuple(r[:5]))
        cur.executemany(
            """
            INSERT INTO failed_tasks(path, attempts, last_error, last_error_ts, next_retry_ts)
            VALUES(?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              attempts=excluded.attempts,
              last_error=excluded.last_error,
              last_error_ts=excluded.last_error_ts,
              next_retry_ts=excluded.next_retry_ts;
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
            SELECT path, attempts, last_error, last_error_ts, next_retry_ts
            FROM failed_tasks
            WHERE next_retry_ts <= ?
            ORDER BY next_retry_ts ASC
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
