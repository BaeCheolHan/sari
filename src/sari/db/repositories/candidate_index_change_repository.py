"""후보 인덱스 변경 로그 저장소를 구현한다."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sari.core.models import CandidateIndexChangeDTO, CandidateIndexChangeLogDTO
from sari.db.row_mapper import row_int, row_optional_str, row_str
from sari.db.schema import connect


class CandidateIndexChangeRepository:
    """후보 인덱스 변경 이벤트 큐 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def enqueue_upsert(self, change: CandidateIndexChangeDTO) -> int:
        """동일 파일 pending 변경을 최신 upsert로 coalesce한다."""
        resolved_repo_id = (
            change.repo_id
            if change.repo_id.strip() != ""
            else f"r_{hashlib.sha1(change.repo_root.encode('utf-8')).hexdigest()[:20]}"
        )
        with connect(self._db_path) as conn:
            existing = conn.execute(
                """
                SELECT change_id
                FROM candidate_index_changes
                WHERE repo_id = :repo_id
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND status = 'PENDING'
                ORDER BY change_id DESC
                LIMIT 1
                """,
                {
                    "repo_id": resolved_repo_id,
                    "repo_root": change.repo_root,
                    "relative_path": change.relative_path,
                },
            ).fetchone()
            if existing is not None:
                change_id = row_int(existing, "change_id")
                conn.execute(
                    """
                    UPDATE candidate_index_changes
                    SET change_type = 'UPSERT',
                        absolute_path = :absolute_path,
                        content_hash = :content_hash,
                        mtime_ns = :mtime_ns,
                        size_bytes = :size_bytes,
                        event_source = :event_source,
                        reason = NULL,
                        status = 'PENDING',
                        updated_at = :recorded_at
                    WHERE change_id = :change_id
                    """,
                    {
                        "change_id": change_id,
                        "absolute_path": change.absolute_path,
                        "content_hash": change.content_hash,
                        "mtime_ns": change.mtime_ns,
                        "size_bytes": change.size_bytes,
                        "event_source": change.event_source,
                        "recorded_at": change.recorded_at,
                    },
                )
                conn.commit()
                return change_id
            cursor = conn.execute(
                """
                INSERT INTO candidate_index_changes(
                    change_type, status, repo_id, repo_root, scope_repo_root, relative_path, absolute_path,
                    content_hash, mtime_ns, size_bytes, event_source, reason, created_at, updated_at
                )
                VALUES(
                    'UPSERT', 'PENDING', :repo_id, :repo_root, :scope_repo_root, :relative_path, :absolute_path,
                    :content_hash, :mtime_ns, :size_bytes, :event_source, NULL, :recorded_at, :recorded_at
                )
                """,
                {
                    **change.to_sql_params(),
                    "repo_id": resolved_repo_id,
                },
            )
            conn.commit()
            return _extract_lastrowid(conn=conn, raw_lastrowid=cursor.lastrowid)

    def enqueue_delete(self, repo_root: str, relative_path: str, event_source: str, recorded_at: str, repo_id: str | None = None) -> int:
        """동일 파일 pending 변경을 delete 이벤트로 coalesce한다."""
        resolved_repo_id = (
            repo_id
            if repo_id is not None and repo_id.strip() != ""
            else f"r_{hashlib.sha1(repo_root.encode('utf-8')).hexdigest()[:20]}"
        )
        with connect(self._db_path) as conn:
            existing = conn.execute(
                """
                SELECT change_id
                FROM candidate_index_changes
                WHERE repo_id = :repo_id
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND status = 'PENDING'
                ORDER BY change_id DESC
                LIMIT 1
                """,
                {"repo_id": resolved_repo_id, "repo_root": repo_root, "relative_path": relative_path},
            ).fetchone()
            if existing is not None:
                change_id = row_int(existing, "change_id")
                conn.execute(
                    """
                    UPDATE candidate_index_changes
                    SET change_type = 'DELETE',
                        absolute_path = NULL,
                        content_hash = NULL,
                        mtime_ns = NULL,
                        size_bytes = NULL,
                        event_source = :event_source,
                        reason = 'deleted',
                        status = 'PENDING',
                        updated_at = :recorded_at
                    WHERE change_id = :change_id
                    """,
                    {"change_id": change_id, "event_source": event_source, "recorded_at": recorded_at},
                )
                conn.commit()
                return change_id
            cursor = conn.execute(
                """
                INSERT INTO candidate_index_changes(
                    change_type, status, repo_id, repo_root, scope_repo_root, relative_path, absolute_path,
                    content_hash, mtime_ns, size_bytes, event_source, reason, created_at, updated_at
                )
                VALUES(
                    'DELETE', 'PENDING', :repo_id, :repo_root, :scope_repo_root, :relative_path, NULL,
                    NULL, NULL, NULL, :event_source, 'deleted', :recorded_at, :recorded_at
                )
                """,
                {
                    "repo_id": resolved_repo_id,
                    "repo_root": repo_root,
                    "scope_repo_root": repo_root,
                    "relative_path": relative_path,
                    "event_source": event_source,
                    "recorded_at": recorded_at,
                },
            )
            conn.commit()
            return _extract_lastrowid(conn=conn, raw_lastrowid=cursor.lastrowid)

    def acquire_pending(self, limit: int) -> list[CandidateIndexChangeLogDTO]:
        """pending 변경 로그를 배치로 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT change_id, change_type, status, repo_id, repo_root, scope_repo_root, relative_path, absolute_path,
                       content_hash, mtime_ns, size_bytes, event_source, reason, created_at, updated_at
                FROM candidate_index_changes
                WHERE status = 'PENDING'
                ORDER BY change_id ASC
                LIMIT :limit
                """,
                {"limit": limit},
            ).fetchall()
        items: list[CandidateIndexChangeLogDTO] = []
        for row in rows:
            raw_mtime_ns = row["mtime_ns"]
            mtime_ns = raw_mtime_ns if isinstance(raw_mtime_ns, int) else None
            raw_size_bytes = row["size_bytes"]
            size_bytes = raw_size_bytes if isinstance(raw_size_bytes, int) else None
            items.append(
                CandidateIndexChangeLogDTO(
                    change_id=row_int(row, "change_id"),
                    change_type=row_str(row, "change_type"),
                    status=row_str(row, "status"),
                    repo_id=row_str(row, "repo_id"),
                    repo_root=row_str(row, "repo_root"),
                    scope_repo_root=row_optional_str(row, "scope_repo_root"),
                    relative_path=row_str(row, "relative_path"),
                    absolute_path=row_optional_str(row, "absolute_path"),
                    content_hash=row_optional_str(row, "content_hash"),
                    mtime_ns=mtime_ns,
                    size_bytes=size_bytes,
                    event_source=row_str(row, "event_source"),
                    reason=row_optional_str(row, "reason"),
                    created_at=row_str(row, "created_at"),
                    updated_at=row_str(row, "updated_at"),
                )
            )
        return items

    def has_pending_changes(self) -> bool:
        """pending 변경 로그 존재 여부를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT change_id
                FROM candidate_index_changes
                WHERE status = 'PENDING'
                LIMIT 1
                """
            ).fetchone()
        return row is not None

    def count_pending_changes(self) -> int:
        """pending 변경 로그 개수를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total_count
                FROM candidate_index_changes
                WHERE status = 'PENDING'
                """
            ).fetchone()
        if row is None:
            return 0
        raw = row["total_count"]
        if isinstance(raw, int):
            return raw
        return int(raw)

    def mark_applied(self, change_id: int, updated_at: str) -> None:
        """변경 로그를 적용 완료 상태로 전환한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE candidate_index_changes
                SET status = 'APPLIED',
                    updated_at = :updated_at
                WHERE change_id = :change_id
                """,
                {"change_id": change_id, "updated_at": updated_at},
            )
            conn.commit()

    def mark_failed(self, change_id: int, error_message: str, updated_at: str) -> None:
        """변경 로그를 실패 상태로 전환한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE candidate_index_changes
                SET status = 'FAILED',
                    reason = :error_message,
                    updated_at = :updated_at
                WHERE change_id = :change_id
                """,
                {
                    "change_id": change_id,
                    "error_message": error_message,
                    "updated_at": updated_at,
                },
            )
            conn.commit()


def _extract_lastrowid(*, conn: Any, raw_lastrowid: object) -> int:
    """INSERT 직후 안전한 lastrowid를 계산한다."""
    resolved = _to_optional_int(raw_lastrowid)
    if resolved is not None:
        return resolved
    fallback_row = conn.execute("SELECT last_insert_rowid() AS lastrowid").fetchone()
    if fallback_row is not None:
        fallback_value = fallback_row["lastrowid"]
        resolved_fallback = _to_optional_int(fallback_value)
        if resolved_fallback is not None:
            return resolved_fallback
    raise RuntimeError("failed to resolve last inserted change_id")


def _to_optional_int(value: object) -> int | None:
    """임의 값을 안전하게 int로 변환한다."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None
