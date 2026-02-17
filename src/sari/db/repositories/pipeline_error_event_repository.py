"""파이프라인 오류 상세 이벤트 저장소를 구현한다."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

from sari.core.models import PipelineErrorEventDTO
from sari.db.row_mapper import row_bool, row_int, row_optional_str, row_str
from sari.db.schema import connect


class PipelineErrorEventRepository:
    """오류 상세 이벤트 영속화/조회/정리를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def record_event(
        self,
        occurred_at: str,
        component: str,
        phase: str,
        severity: str,
        repo_root: str | None,
        relative_path: str | None,
        job_id: str | None,
        attempt_count: int,
        error_code: str,
        error_message: str,
        error_type: str,
        stacktrace_text: str,
        context_data: dict[str, object],
        worker_name: str,
        run_mode: str,
    ) -> str:
        """오류 이벤트를 저장하고 event_id를 반환한다."""
        event_id = str(uuid.uuid4())
        context_json = json.dumps(context_data, ensure_ascii=False)
        scope_type = "GLOBAL" if repo_root is None or str(repo_root).strip() == "" else "REPO"
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pipeline_error_events(
                    event_id, occurred_at, component, phase, severity,
                    scope_type,
                    repo_root, relative_path, job_id, attempt_count,
                    error_code, error_message, error_type, stacktrace_text, context_json,
                    worker_name, run_mode, resolved, resolved_at
                )
                VALUES(
                    :event_id, :occurred_at, :component, :phase, :severity,
                    :scope_type,
                    :repo_root, :relative_path, :job_id, :attempt_count,
                    :error_code, :error_message, :error_type, :stacktrace_text, :context_json,
                    :worker_name, :run_mode, 0, NULL
                )
                """,
                {
                    "event_id": event_id,
                    "occurred_at": occurred_at,
                    "component": component,
                    "phase": phase,
                    "severity": severity,
                    "scope_type": scope_type,
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "job_id": job_id,
                    "attempt_count": attempt_count,
                    "error_code": error_code,
                    "error_message": error_message,
                    "error_type": error_type,
                    "stacktrace_text": stacktrace_text,
                    "context_json": context_json,
                    "worker_name": worker_name,
                    "run_mode": run_mode,
                },
            )
            conn.commit()
        return event_id

    def list_events(self, limit: int, offset: int = 0, repo_root: str | None = None, error_code: str | None = None) -> list[PipelineErrorEventDTO]:
        """최신순 오류 이벤트 목록을 반환한다."""
        clauses: list[str] = []
        params: dict[str, object] = {"limit": limit, "offset": offset}
        if repo_root is not None and repo_root.strip() != "":
            clauses.append("repo_root = :repo_root")
            params["repo_root"] = repo_root
        if error_code is not None and error_code.strip() != "":
            clauses.append("error_code = :error_code")
            params["error_code"] = error_code
        where_sql = ""
        if len(clauses) > 0:
            where_sql = "WHERE " + " AND ".join(clauses)
        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, occurred_at, component, phase, severity, scope_type,
                       repo_root, relative_path, job_id, attempt_count,
                       error_code, error_message, error_type, stacktrace_text, context_json,
                       worker_name, run_mode, resolved, resolved_at
                FROM pipeline_error_events
                {where_sql}
                ORDER BY occurred_at DESC
                LIMIT :limit OFFSET :offset
                """,
                params,
            ).fetchall()
        return [_row_to_dto(row) for row in rows]

    def get_event(self, event_id: str) -> PipelineErrorEventDTO | None:
        """단일 오류 이벤트를 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT event_id, occurred_at, component, phase, severity, scope_type,
                       repo_root, relative_path, job_id, attempt_count,
                       error_code, error_message, error_type, stacktrace_text, context_json,
                       worker_name, run_mode, resolved, resolved_at
                FROM pipeline_error_events
                WHERE event_id = :event_id
                """,
                {"event_id": event_id},
            ).fetchone()
        if row is None:
            return None
        return _row_to_dto(row)

    def prune(self, cutoff_iso: str, max_rows: int) -> int:
        """보존 정책에 따라 오래된 이벤트를 정리하고 삭제 건수를 반환한다."""
        deleted = 0
        with connect(self._db_path) as conn:
            first = conn.execute(
                """
                DELETE FROM pipeline_error_events
                WHERE occurred_at < :cutoff_iso
                """,
                {"cutoff_iso": cutoff_iso},
            )
            deleted += int(first.rowcount)

            count_row = conn.execute("SELECT COUNT(*) AS cnt FROM pipeline_error_events").fetchone()
            total = 0 if count_row is None else row_int(count_row, "cnt")
            overflow = total - max_rows
            if overflow > 0:
                second = conn.execute(
                    """
                    DELETE FROM pipeline_error_events
                    WHERE event_id IN (
                        SELECT event_id
                        FROM pipeline_error_events
                        ORDER BY occurred_at ASC
                        LIMIT :overflow
                    )
                    """,
                    {"overflow": overflow},
                )
                deleted += int(second.rowcount)
            conn.commit()
        return deleted


def _row_to_dto(row) -> PipelineErrorEventDTO:  # type: ignore[no-untyped-def]
    """sqlite row를 DTO로 변환한다."""
    return PipelineErrorEventDTO(
        event_id=row_str(row, "event_id"),
        occurred_at=row_str(row, "occurred_at"),
        component=row_str(row, "component"),
        phase=row_str(row, "phase"),
        severity=row_str(row, "severity"),
        scope_type=row_str(row, "scope_type"),
        repo_root=row_optional_str(row, "repo_root"),
        relative_path=row_optional_str(row, "relative_path"),
        job_id=row_optional_str(row, "job_id"),
        attempt_count=row_int(row, "attempt_count"),
        error_code=row_str(row, "error_code"),
        error_message=row_str(row, "error_message"),
        error_type=row_str(row, "error_type"),
        stacktrace_text=row_str(row, "stacktrace_text"),
        context_json=row_str(row, "context_json"),
        worker_name=row_str(row, "worker_name"),
        run_mode=row_str(row, "run_mode"),
        resolved=row_bool(row, "resolved"),
        resolved_at=row_optional_str(row, "resolved_at"),
    )
