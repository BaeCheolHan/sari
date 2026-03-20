"""도구 준비 상태 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import ToolReadinessStateDTO
from sari.db.row_mapper import row_bool, row_int, row_str
from sari.db.schema import connect


class ToolReadinessRepository:
    """도구 준비 상태 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def upsert_state(self, state: ToolReadinessStateDTO) -> None:
        """도구 준비 상태를 업서트한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO tool_readiness_state(
                    repo_root, scope_repo_root, relative_path, content_hash,
                    list_files_ready, read_file_ready, search_symbol_ready, get_callers_ready,
                    consistency_ready, quality_ready, tool_ready, last_reason, updated_at
                )
                VALUES(
                    :repo_root, :scope_repo_root, :relative_path, :content_hash,
                    :list_files_ready, :read_file_ready, :search_symbol_ready, :get_callers_ready,
                    :consistency_ready, :quality_ready, :tool_ready, :last_reason, :updated_at
                )
                ON CONFLICT(repo_root, relative_path) DO UPDATE SET
                    scope_repo_root = excluded.scope_repo_root,
                    content_hash = excluded.content_hash,
                    list_files_ready = excluded.list_files_ready,
                    read_file_ready = excluded.read_file_ready,
                    search_symbol_ready = excluded.search_symbol_ready,
                    get_callers_ready = CASE
                        WHEN tool_readiness_state.content_hash = excluded.content_hash
                         AND tool_readiness_state.last_reason = 'ok'
                         AND tool_readiness_state.get_callers_ready = 1
                         AND excluded.get_callers_ready = 0
                         AND excluded.tool_ready = 1
                         AND excluded.last_reason LIKE 'l3_preprocess_%'
                        THEN tool_readiness_state.get_callers_ready
                        ELSE excluded.get_callers_ready
                    END,
                    consistency_ready = excluded.consistency_ready,
                    quality_ready = excluded.quality_ready,
                    tool_ready = CASE
                        WHEN tool_readiness_state.content_hash = excluded.content_hash
                         AND tool_readiness_state.last_reason = 'ok'
                         AND tool_readiness_state.get_callers_ready = 1
                         AND excluded.get_callers_ready = 0
                         AND excluded.tool_ready = 1
                         AND excluded.last_reason LIKE 'l3_preprocess_%'
                        THEN tool_readiness_state.tool_ready
                        ELSE excluded.tool_ready
                    END,
                    last_reason = CASE
                        WHEN tool_readiness_state.content_hash = excluded.content_hash
                         AND tool_readiness_state.last_reason = 'ok'
                         AND tool_readiness_state.get_callers_ready = 1
                         AND excluded.get_callers_ready = 0
                         AND excluded.tool_ready = 1
                         AND excluded.last_reason LIKE 'l3_preprocess_%'
                        THEN tool_readiness_state.last_reason
                        ELSE excluded.last_reason
                    END,
                    updated_at = CASE
                        WHEN tool_readiness_state.content_hash = excluded.content_hash
                         AND tool_readiness_state.last_reason = 'ok'
                         AND tool_readiness_state.get_callers_ready = 1
                         AND excluded.get_callers_ready = 0
                         AND excluded.tool_ready = 1
                         AND excluded.last_reason LIKE 'l3_preprocess_%'
                        THEN tool_readiness_state.updated_at
                        ELSE excluded.updated_at
                    END
                """,
                state.to_sql_params(),
            )
            conn.commit()

    def upsert_state_many(self, states: list[ToolReadinessStateDTO], *, conn=None) -> None:
        """도구 준비 상태를 배치 업서트한다."""
        if len(states) == 0:
            return
        owned_conn = conn is None
        if owned_conn:
            conn = connect(self._db_path)
        if conn is None:
            raise RuntimeError("conn must not be None when owned_conn is False")
        try:
            for state in states:
                conn.execute(
                    """
                    INSERT INTO tool_readiness_state(
                        repo_root, scope_repo_root, relative_path, content_hash,
                        list_files_ready, read_file_ready, search_symbol_ready, get_callers_ready,
                        consistency_ready, quality_ready, tool_ready, last_reason, updated_at
                    )
                    VALUES(
                        :repo_root, :scope_repo_root, :relative_path, :content_hash,
                        :list_files_ready, :read_file_ready, :search_symbol_ready, :get_callers_ready,
                        :consistency_ready, :quality_ready, :tool_ready, :last_reason, :updated_at
                    )
                    ON CONFLICT(repo_root, relative_path) DO UPDATE SET
                        scope_repo_root = excluded.scope_repo_root,
                        content_hash = excluded.content_hash,
                        list_files_ready = excluded.list_files_ready,
                        read_file_ready = excluded.read_file_ready,
                        search_symbol_ready = excluded.search_symbol_ready,
                        get_callers_ready = CASE
                            WHEN tool_readiness_state.content_hash = excluded.content_hash
                             AND tool_readiness_state.last_reason = 'ok'
                             AND tool_readiness_state.get_callers_ready = 1
                             AND excluded.get_callers_ready = 0
                             AND excluded.tool_ready = 1
                             AND excluded.last_reason LIKE 'l3_preprocess_%'
                            THEN tool_readiness_state.get_callers_ready
                            ELSE excluded.get_callers_ready
                        END,
                        consistency_ready = excluded.consistency_ready,
                        quality_ready = excluded.quality_ready,
                        tool_ready = CASE
                            WHEN tool_readiness_state.content_hash = excluded.content_hash
                             AND tool_readiness_state.last_reason = 'ok'
                             AND tool_readiness_state.get_callers_ready = 1
                             AND excluded.get_callers_ready = 0
                             AND excluded.tool_ready = 1
                             AND excluded.last_reason LIKE 'l3_preprocess_%'
                            THEN tool_readiness_state.tool_ready
                            ELSE excluded.tool_ready
                        END,
                        last_reason = CASE
                            WHEN tool_readiness_state.content_hash = excluded.content_hash
                             AND tool_readiness_state.last_reason = 'ok'
                             AND tool_readiness_state.get_callers_ready = 1
                             AND excluded.get_callers_ready = 0
                             AND excluded.tool_ready = 1
                             AND excluded.last_reason LIKE 'l3_preprocess_%'
                            THEN tool_readiness_state.last_reason
                            ELSE excluded.last_reason
                        END,
                        updated_at = CASE
                            WHEN tool_readiness_state.content_hash = excluded.content_hash
                             AND tool_readiness_state.last_reason = 'ok'
                             AND tool_readiness_state.get_callers_ready = 1
                             AND excluded.get_callers_ready = 0
                             AND excluded.tool_ready = 1
                             AND excluded.last_reason LIKE 'l3_preprocess_%'
                            THEN tool_readiness_state.updated_at
                            ELSE excluded.updated_at
                        END
                    """,
                    state.to_sql_params(),
                )
            if owned_conn:
                conn.commit()
        finally:
            if owned_conn:
                conn.close()

    def get_state(self, repo_root: str, relative_path: str) -> ToolReadinessStateDTO | None:
        """도구 준비 상태를 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT repo_root, scope_repo_root, relative_path, content_hash, list_files_ready, read_file_ready,
                       search_symbol_ready, get_callers_ready, consistency_ready, quality_ready,
                       tool_ready, last_reason, updated_at
                FROM tool_readiness_state
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                """,
                {"repo_root": repo_root, "relative_path": relative_path},
            ).fetchone()
        if row is None:
            return None
        return ToolReadinessStateDTO(
            repo_root=row_str(row, "repo_root"),
            scope_repo_root=row_str(row, "scope_repo_root"),
            relative_path=row_str(row, "relative_path"),
            content_hash=row_str(row, "content_hash"),
            list_files_ready=row_bool(row, "list_files_ready"),
            read_file_ready=row_bool(row, "read_file_ready"),
            search_symbol_ready=row_bool(row, "search_symbol_ready"),
            get_callers_ready=row_bool(row, "get_callers_ready"),
            consistency_ready=row_bool(row, "consistency_ready"),
            quality_ready=row_bool(row, "quality_ready"),
            tool_ready=row_bool(row, "tool_ready"),
            last_reason=row_str(row, "last_reason"),
            updated_at=row_str(row, "updated_at"),
        )

    def count_by_tool_ready(self) -> dict[str, int]:
        """tool_ready true/false 분포를 반환한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT tool_ready, COUNT(*) AS cnt
                FROM tool_readiness_state
                GROUP BY tool_ready
                """
            ).fetchall()
        true_count = 0
        false_count = 0
        for row in rows:
            if row_bool(row, "tool_ready"):
                true_count += row_int(row, "cnt")
            else:
                false_count += row_int(row, "cnt")
        return {"tool_ready_true": true_count, "tool_ready_false": false_count}
