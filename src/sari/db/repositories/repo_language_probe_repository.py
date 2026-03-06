"""repo_root + language probe 상태 영속 저장소."""

from __future__ import annotations

from pathlib import Path

from sari.db.row_mapper import row_int, row_optional_str_normalized, row_str
from sari.db.schema import connect


class RepoLanguageProbeRepository:
    """repo-language probe 상태 스냅샷을 영속화한다."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def upsert_state(
        self,
        *,
        repo_root: str,
        language: str,
        status: str,
        fail_count: int,
        inflight_phase: str | None,
        next_retry_at: str | None,
        last_error_code: str | None,
        last_error_message: str | None,
        last_trigger: str | None,
        last_seen_at: str | None,
        updated_at: str,
    ) -> None:
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO repo_language_probe_state(
                    repo_root, language, status, fail_count, inflight_phase, next_retry_at,
                    last_error_code, last_error_message, last_trigger, last_seen_at, updated_at
                ) VALUES(
                    :repo_root, :language, :status, :fail_count, :inflight_phase, :next_retry_at,
                    :last_error_code, :last_error_message, :last_trigger, :last_seen_at, :updated_at
                )
                ON CONFLICT(repo_root, language) DO UPDATE SET
                    status = excluded.status,
                    fail_count = excluded.fail_count,
                    inflight_phase = excluded.inflight_phase,
                    next_retry_at = excluded.next_retry_at,
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message,
                    last_trigger = excluded.last_trigger,
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at
                """,
                {
                    "repo_root": repo_root,
                    "language": language,
                    "status": status,
                    "fail_count": int(fail_count),
                    "inflight_phase": inflight_phase,
                    "next_retry_at": next_retry_at,
                    "last_error_code": last_error_code,
                    "last_error_message": last_error_message,
                    "last_trigger": last_trigger,
                    "last_seen_at": last_seen_at,
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def list_by_repo_root(self, repo_root: str) -> list[dict[str, object]]:
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT repo_root, language, status, fail_count, inflight_phase, next_retry_at,
                       last_error_code, last_error_message, last_trigger, last_seen_at, updated_at
                FROM repo_language_probe_state
                WHERE repo_root = :repo_root
                ORDER BY language ASC
                """,
                {"repo_root": repo_root},
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def clear_states(self, *, repo_root: str | None = None, language: str | None = None) -> int:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if repo_root is not None:
            clauses.append("repo_root = :repo_root")
            params["repo_root"] = repo_root
        if language is not None:
            clauses.append("language = :language")
            params["language"] = language
        where = " AND ".join(clauses) if clauses else "1=1"
        with connect(self._db_path) as conn:
            cursor = conn.execute(f"DELETE FROM repo_language_probe_state WHERE {where}", params)
            conn.commit()
        return int(cursor.rowcount)

    def _row_to_dict(self, row: object) -> dict[str, object]:
        return {
            "repo_root": row_str(row, "repo_root"),  # type: ignore[arg-type]
            "language": row_str(row, "language"),  # type: ignore[arg-type]
            "status": row_str(row, "status"),  # type: ignore[arg-type]
            "fail_count": row_int(row, "fail_count"),  # type: ignore[arg-type]
            "inflight_phase": row_optional_str_normalized(row, "inflight_phase"),  # type: ignore[arg-type]
            "next_retry_at": row_optional_str_normalized(row, "next_retry_at"),  # type: ignore[arg-type]
            "last_error_code": row_optional_str_normalized(row, "last_error_code"),  # type: ignore[arg-type]
            "last_error_message": row_optional_str_normalized(row, "last_error_message"),  # type: ignore[arg-type]
            "last_trigger": row_optional_str_normalized(row, "last_trigger"),  # type: ignore[arg-type]
            "last_seen_at": row_optional_str_normalized(row, "last_seen_at"),  # type: ignore[arg-type]
            "updated_at": row_str(row, "updated_at"),  # type: ignore[arg-type]
        }
