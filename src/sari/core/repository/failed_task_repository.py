import sqlite3
from typing import Any, Dict, Iterable, List, Tuple

from .base import BaseRepository


class FailedTaskRepository(BaseRepository):
    def upsert_failed_tasks_tx(self, cur: sqlite3.Cursor, rows: Iterable[tuple]) -> int:
        rows_list = [list(r) for r in rows]
        if not rows_list:
            return 0
        normalized = []
        for r in rows_list:
            while len(r) < 7:
                r.append("")
            normalized.append(
                (
                    str(r[0]),
                    str(r[1]),
                    int(r[2] or 0),
                    str(r[3]),
                    int(r[4] or 0),
                    int(r[5] or 0),
                    str(r[6] or "{}"),
                )
            )
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

    def get_failed_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.execute(
            """
            SELECT path, root_id, attempts, error, ts, next_retry, metadata_json
            FROM failed_tasks
            ORDER BY ts DESC
            LIMIT ?;
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_failed_tasks(self) -> Tuple[int, int]:
        row = self.execute("SELECT COUNT(*) AS c FROM failed_tasks").fetchone()
        total = int(row["c"]) if row else 0
        row2 = self.execute("SELECT COUNT(*) AS c FROM failed_tasks WHERE attempts >= 3").fetchone()
        high = int(row2["c"]) if row2 else 0
        return total, high
