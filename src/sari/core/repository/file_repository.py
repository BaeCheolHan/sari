import sqlite3
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Optional, Tuple
from .base import BaseRepository
from ..utils.compression import _compress
from ..models import FILE_COLUMNS

FileInputRow = Sequence[object] | Mapping[str, object]


class FileRepository(BaseRepository):
    """
    파일 시스템 메타데이터와 파일 내용을 관리하는 저장소입니다.
    파일의 경로, 수정 시간(mtime), 크기, 압축된 내용 및 상태 정보를 SQLite 'files' 테이블에 저장합니다.
    """

    def upsert_files_tx(
            self,
            cur: sqlite3.Cursor,
            rows: Iterable[FileInputRow]) -> int:
        """
        파일 정보들을 트랜잭션 내에서 한꺼번에 삽입하거나 업데이트(Upsert)합니다.
        mtime이 기존보다 크거나 같은 경우에만 업데이트하며, 관련 심볼 정보를 초기화합니다.
        """
        processed_rows = []
        now = int(time.time())

        for r in rows:
            # Robust mapping: pad tuple if too short to match FILE_COLUMNS
            # length
            if isinstance(r, Mapping):
                data = {col: r.get(col) for col in FILE_COLUMNS}
            else:
                r_list = list(r)
                while len(r_list) < len(FILE_COLUMNS):
                    r_list.append(None)
                data = dict(zip(FILE_COLUMNS, r_list, strict=False))

            path = data.get("path")
            if not path:
                continue

            # Ensure safe values for every column to prevent KeyError and
            # IntegrityError
            row_dict = {
                "path": str(path),
                "rel_path": str(data.get("rel_path") or ""),
                "root_id": str(data.get("root_id") or "root"),
                "repo": str(data.get("repo") or ""),
                "mtime": int(data.get("mtime") or now),
                "size": int(data.get("size") or 0),
                "content": _compress(data.get("content") or b""),
                "hash": str(data.get("hash") or ""),
                "fts_content": str(data.get("fts_content") or ""),
                "last_seen_ts": int(data.get("last_seen_ts") or now),
                "deleted_ts": int(data.get("deleted_ts") or 0),
                "status": str(data.get("status") or "ok"),
                "error": data.get("error"),
                "parse_status": str(data.get("parse_status") or "ok"),
                "parse_error": data.get("parse_error"),
                "ast_status": str(data.get("ast_status") or "none"),
                "ast_reason": str(data.get("ast_reason") or "none"),
                "is_binary": int(data.get("is_binary") or 0),
                "is_minified": int(data.get("is_minified") or 0),
                "metadata_json": str(data.get("metadata_json") or "{}")
            }
            processed_rows.append(tuple(row_dict[col] for col in FILE_COLUMNS))

        if not processed_rows:
            return 0

        col_names = ", ".join(FILE_COLUMNS)
        placeholders = ", ".join(["?"] * len(FILE_COLUMNS))
        update_set = ", ".join(
            [f"{col}=excluded.{col}" for col in FILE_COLUMNS if col != "path"])

        sql = f"INSERT INTO files({col_names}) VALUES({placeholders}) ON CONFLICT(path) DO UPDATE SET {update_set} WHERE excluded.mtime >= files.mtime;"
        cur.executemany(sql, processed_rows)
        
        # Only delete symbols for files that were actually processed and meet the mtime update condition.
        # This prevents accidental symbol loss when the file itself wasn't updated.
        cur.executemany(
            """
            DELETE FROM symbols 
            WHERE path = ? 
              AND EXISTS (
                  SELECT 1 FROM files 
                  WHERE files.path = symbols.path 
                    AND files.mtime = ?
              )
            """, 
            [
                (row_dict["path"], row_dict["mtime"]) for row_dict in (
                    dict(zip(FILE_COLUMNS, row_vals, strict=False)) for row_vals in processed_rows
                )
            ],
        )
        return len(processed_rows)

    def delete_path_tx(self, cur: sqlite3.Cursor, path: str) -> None:
        """파일 정보와 그에 딸린 심볼, 관계 정보를 트랜잭션 내에서 모두 삭제합니다."""
        cur.execute("DELETE FROM files WHERE path = ?", (path,))
        cur.execute("DELETE FROM symbols WHERE path = ?", (path,))
        cur.execute(
            "DELETE FROM symbol_relations WHERE from_path = ? OR to_path = ?", (path, path))

    def update_last_seen_tx(
            self,
            cur: sqlite3.Cursor,
            paths: list[str],
            ts: int) -> None:
        if not paths:
            return
        unique_paths = list(dict.fromkeys(str(p) for p in paths if p))
        if not unique_paths:
            return
        # Batch update via temp table to reduce per-row UPDATE overhead.
        cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS temp_seen_paths(path TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        cur.execute("DELETE FROM temp_seen_paths")
        cur.executemany(
            "INSERT OR IGNORE INTO temp_seen_paths(path) VALUES (?)",
            [(p,) for p in unique_paths],
        )
        cur.execute(
            "UPDATE files SET last_seen_ts = ? WHERE path IN (SELECT path FROM temp_seen_paths)",
            (int(ts),),
        )
        cur.execute("DELETE FROM temp_seen_paths")

    def get_file_meta(self, path: str) -> Optional[Tuple[int, int, str]]:
        """특정 경로 파일의 mtime, 크기, 그리고 메타데이터에 저장된 내용 해시값을 반환합니다."""
        try:
            row = self.execute(
                "SELECT mtime, size, metadata_json FROM files WHERE path = ?",
                (path,
                 )).fetchone()
            if not row:
                return None
            ch = json.loads(
                row["metadata_json"]).get(
                "content_hash",
                "") if row["metadata_json"] else ""
            return (row["mtime"], row["size"], ch)
        except Exception:
            return None

    def is_payload_deferred(self, path: str) -> bool:
        try:
            row = self.execute(
                "SELECT metadata_json FROM files WHERE path = ? LIMIT 1",
                (path,),
            ).fetchone()
            if not row:
                return False
            meta_raw = row["metadata_json"] if hasattr(row, "keys") else row[0]
            if not meta_raw:
                return False
            meta = json.loads(meta_raw)
            return bool(meta.get("deferred_payload", False))
        except Exception:
            return False

    def get_unseen_paths(self, ts: int) -> list[str]:
        rows = self.execute(
            "SELECT path FROM files WHERE last_seen_ts < ?", (ts,)).fetchall()
        return [r["path"] for r in rows]

    def list_files(self,
                   limit: int = 50,
                   repo: Optional[str] = None,
                   root_ids: Optional[list[str]] = None) -> list[dict[str, object]]:
        sql = "SELECT path, size, repo FROM files WHERE deleted_ts = 0"
        params: list[object] = []
        if repo:
            sql += " AND repo = ?"
            params.append(repo)
        if root_ids:
            sql += f" AND root_id IN ({','.join(['?']*len(root_ids))})"
            params.extend(root_ids)
        sql += " LIMIT ?"
        params.append(limit)
        return [{"path": r["path"], "size": r["size"], "repo": r["repo"]}
                for r in self.execute(sql, params).fetchall()]

    def get_repo_stats(
            self, root_ids: Optional[list[str]] = None) -> dict[str, int]:
        sql = "SELECT repo, COUNT(path) AS c FROM files WHERE deleted_ts = 0"
        params = []
        if root_ids:
            sql += f" AND root_id IN ({','.join(['?']*len(root_ids))})"
            params.extend(root_ids)
        sql += " GROUP BY repo"
        return {r["repo"]: r["c"]
                for r in self.execute(sql, params).fetchall()}
