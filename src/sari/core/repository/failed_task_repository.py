import sqlite3
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .base import BaseRepository


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return int(default)


def _row_to_named_dict(row: object, columns: List[str], fill: object = "") -> Dict[str, object]:
    if isinstance(row, Mapping):
        return {col: row.get(col, fill) for col in columns}
    values = list(row) if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)) else []
    padded = values + [fill] * max(0, len(columns) - len(values))
    return dict(zip(columns, padded, strict=False))


class FailedTaskRepository(BaseRepository):
    """
    분석이나 인덱싱 중 실패한 작업(Dead Letter Queue, DLQ)을 관리하는 저장소입니다.
    실패한 파일 경로, 시도 횟수, 오류 내용 및 다음 재시도 시간을 SQLite 'failed_tasks' 테이블에 저장합니다.
    """
    def upsert_failed_tasks_tx(self, cur: sqlite3.Cursor, rows: Iterable[object]) -> int:
        """
        실패한 작업 정보들을 트랜잭션 내에서 삽입하거나 업데이트합니다.
        이미 존재하는 경로의 경우 시도 횟수와 오류 정보를 갱신합니다.
        """
        rows_list = list(rows)
        if not rows_list:
            return 0
        columns = [
            "path",
            "root_id",
            "attempts",
            "error",
            "ts",
            "next_retry",
            "metadata_json",
        ]
        normalized = []
        for r in rows_list:
            data = _row_to_named_dict(r, columns, fill="")
            normalized.append(
                (
                    str(data.get("path", "")),
                    str(data.get("root_id", "")),
                    _to_int(data.get("attempts")),
                    str(data.get("error", "")),
                    _to_int(data.get("ts")),
                    _to_int(data.get("next_retry")),
                    str(data.get("metadata_json") or "{}"),
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
        """성공적으로 재처리된 파일 경로들을 실패 작업 목록에서 삭제합니다."""
        paths_list = [p for p in paths if p]
        if not paths_list:
            return 0
        cur.executemany("DELETE FROM failed_tasks WHERE path = ?", [(p,) for p in paths_list])
        return len(paths_list)

    def list_failed_tasks_ready(self, now_ts: int, limit: int = 50) -> List[Dict[str, object]]:
        """현재 시간 기준으로 재시도할 준비가 된(next_retry <= now) 실패 작업들을 조회합니다."""
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

    def get_failed_tasks(self, limit: int = 50) -> List[Dict[str, object]]:
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
        row = self.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN attempts >= 3 THEN 1 ELSE 0 END) AS high
            FROM failed_tasks
            """
        ).fetchone()
        total_raw = 0
        high_raw = 0
        if row:
            try:
                total_raw = row["total"]
            except Exception:
                total_raw = 0
            try:
                high_raw = row["high"]
            except Exception:
                high_raw = 0
        total = int(total_raw or 0)
        high = int(high_raw or 0)
        return total, high
