"""파이프라인 작업 이벤트 저장소를 구현한다."""

from __future__ import annotations

import uuid
from pathlib import Path

from sari.db.row_mapper import row_int, row_str
from sari.db.schema import connect


class PipelineJobEventRepository:
    """파이프라인 지연/상태 이벤트 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def record_event(self, job_id: str, status: str, latency_ms: int, created_at: str) -> str:
        """작업 이벤트를 저장한다."""
        event_id = str(uuid.uuid4())
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pipeline_job_events(event_id, job_id, status, latency_ms, created_at)
                VALUES(:event_id, :job_id, :status, :latency_ms, :created_at)
                """,
                {
                    "event_id": event_id,
                    "job_id": job_id,
                    "status": status,
                    "latency_ms": latency_ms,
                    "created_at": created_at,
                },
            )
            conn.commit()
        return event_id

    def list_window_events(self, from_iso: str) -> list[dict[str, object]]:
        """지정 시각 이후 이벤트를 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT event_id, job_id, status, latency_ms, created_at
                FROM pipeline_job_events
                WHERE created_at >= :from_iso
                ORDER BY created_at ASC
                """,
                {"from_iso": from_iso},
            ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            items.append(
                {
                    "event_id": row_str(row, "event_id"),
                    "job_id": row_str(row, "job_id"),
                    "status": row_str(row, "status"),
                    "latency_ms": row_int(row, "latency_ms"),
                    "created_at": row_str(row, "created_at"),
                }
            )
        return items
