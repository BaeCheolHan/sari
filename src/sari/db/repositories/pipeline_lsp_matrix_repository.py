"""LSP 매트릭스 실행 저장소를 구현한다."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sari.db.row_mapper import row_bool, row_optional_str, row_str
from sari.db.schema import connect


class PipelineLspMatrixRepository:
    """LSP 매트릭스 실행 이력을 영속화한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소 DB 경로를 저장한다."""
        self._db_path = db_path

    def create_run(
        self,
        repo_root: str,
        required_languages: tuple[str, ...],
        fail_on_unavailable: bool,
        strict_symbol_gate: bool,
        started_at: str,
    ) -> str:
        """신규 LSP 매트릭스 실행을 생성한다."""
        run_id = str(uuid.uuid4())
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pipeline_lsp_matrix_runs(
                    run_id, repo_root, scope_repo_root, required_languages_json, fail_on_unavailable, strict_symbol_gate, started_at, finished_at, status, summary_json
                )
                VALUES(
                    :run_id, :repo_root, :scope_repo_root, :required_languages_json, :fail_on_unavailable, :strict_symbol_gate, :started_at, NULL, 'RUNNING', NULL
                )
                """,
                {
                    "run_id": run_id,
                    "repo_root": repo_root,
                    "scope_repo_root": repo_root,
                    "required_languages_json": json.dumps(list(required_languages), ensure_ascii=False),
                    "fail_on_unavailable": 1 if fail_on_unavailable else 0,
                    "strict_symbol_gate": 1 if strict_symbol_gate else 0,
                    "started_at": started_at,
                },
            )
            conn.commit()
        return run_id

    def complete_run(self, run_id: str, finished_at: str, status: str, summary: dict[str, object]) -> None:
        """LSP 매트릭스 실행을 완료 상태로 갱신한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE pipeline_lsp_matrix_runs
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
        """최신 LSP 매트릭스 실행 결과를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    run_id,
                    repo_root,
                    required_languages_json,
                    fail_on_unavailable,
                    strict_symbol_gate,
                    started_at,
                    finished_at,
                    status,
                    summary_json
                FROM pipeline_lsp_matrix_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        summary_raw = row_optional_str(row, "summary_json")
        summary: dict[str, object] = {}
        if summary_raw is not None:
            parsed_summary = json.loads(summary_raw)
            if isinstance(parsed_summary, dict):
                summary = parsed_summary
        required_raw = row_str(row, "required_languages_json")
        required_languages: list[str] = []
        parsed_required = json.loads(required_raw)
        if isinstance(parsed_required, list):
            for item in parsed_required:
                if isinstance(item, str):
                    required_languages.append(item)
        return {
            "run_id": row_str(row, "run_id"),
            "repo_root": row_str(row, "repo_root"),
            "required_languages": required_languages,
            "fail_on_unavailable": row_bool(row, "fail_on_unavailable"),
            "strict_symbol_gate": row_bool(row, "strict_symbol_gate"),
            "started_at": row_str(row, "started_at"),
            "finished_at": row_optional_str(row, "finished_at"),
            "status": row_str(row, "status"),
            "summary": summary,
        }
