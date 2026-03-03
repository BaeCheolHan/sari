"""파이프라인 성능 실행 저장소를 구현한다."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sari.db.row_mapper import row_int, row_optional_str, row_str
from sari.db.schema import connect


class PipelinePerfRepository:
    """성능 실측 실행 이력을 영속화한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소 DB 경로를 저장한다."""
        self._db_path = db_path

    def create_run(self, repo_root: str, target_files: int, profile: str, started_at: str) -> str:
        """신규 성능 실측 실행을 생성한다."""
        run_id = str(uuid.uuid4())
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pipeline_perf_runs(
                    run_id, repo_root, scope_repo_root, target_files, profile, started_at, finished_at, status, summary_json
                )
                VALUES(
                    :run_id, :repo_root, :scope_repo_root, :target_files, :profile, :started_at, NULL, 'RUNNING', NULL
                )
                """,
                {
                    "run_id": run_id,
                    "repo_root": repo_root,
                    "scope_repo_root": repo_root,
                    "target_files": target_files,
                    "profile": profile,
                    "started_at": started_at,
                },
            )
            conn.commit()
        return run_id

    def complete_run(self, run_id: str, finished_at: str, status: str, summary: dict[str, object]) -> None:
        """성능 실측 실행을 완료 상태로 갱신한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE pipeline_perf_runs
                SET finished_at = :finished_at,
                    status = :status,
                    summary_json = :summary_json
                WHERE run_id = :run_id
                """,
                {
                    "run_id": run_id,
                    "finished_at": finished_at,
                    "status": status,
                    "summary_json": json.dumps(summary, ensure_ascii=False),
                },
            )
            conn.commit()

    def get_latest_run(self) -> dict[str, object] | None:
        """최신 성능 실측 실행 결과를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT run_id, repo_root, target_files, profile, started_at, finished_at, status, summary_json
                FROM pipeline_perf_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        summary_raw = row_optional_str(row, "summary_json")
        summary: dict[str, object] = {}
        if summary_raw is not None:
            parsed = json.loads(summary_raw)
            if isinstance(parsed, dict):
                summary = parsed
        return {
            "run_id": row_str(row, "run_id"),
            "repo_root": row_str(row, "repo_root"),
            "target_files": row_int(row, "target_files"),
            "profile": row_str(row, "profile"),
            "started_at": row_str(row, "started_at"),
            "finished_at": row_optional_str(row, "finished_at"),
            "status": row_str(row, "status"),
            "summary": summary,
        }

    def get_latest_run_for_repo(self, repo_root: str) -> dict[str, object] | None:
        """특정 저장소 기준 최신 성능 실측 실행 결과를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT run_id, repo_root, target_files, profile, started_at, finished_at, status, summary_json
                FROM pipeline_perf_runs
                WHERE repo_root = :repo_root
                ORDER BY started_at DESC
                LIMIT 1
                """,
                {"repo_root": repo_root},
            ).fetchone()
        if row is None:
            return None
        summary_raw = row_optional_str(row, "summary_json")
        summary: dict[str, object] = {}
        if summary_raw is not None:
            parsed = json.loads(summary_raw)
            if isinstance(parsed, dict):
                summary = parsed
        return {
            "run_id": row_str(row, "run_id"),
            "repo_root": row_str(row, "repo_root"),
            "target_files": row_int(row, "target_files"),
            "profile": row_str(row, "profile"),
            "started_at": row_str(row, "started_at"),
            "finished_at": row_optional_str(row, "finished_at"),
            "status": row_str(row, "status"),
            "summary": summary,
        }
