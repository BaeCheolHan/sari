"""데몬 레지스트리 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import DaemonRegistryEntryDTO
from sari.db.row_mapper import row_bool, row_int, row_optional_str, row_str
from sari.db.schema import connect


class DaemonRegistryRepository:
    """다중 데몬 엔드포인트 레지스트리를 영속화한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소 DB 경로를 보관한다."""
        self._db_path = db_path

    def upsert(self, entry: DaemonRegistryEntryDTO) -> None:
        """데몬 엔트리를 upsert한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO daemon_registry(
                    daemon_id, host, port, pid, workspace_root, protocol, started_at, last_seen_at, is_draining,
                    deployment_state, health_fail_streak, last_health_error, last_health_at
                )
                VALUES(
                    :daemon_id, :host, :port, :pid, :workspace_root, :protocol, :started_at, :last_seen_at, :is_draining,
                    :deployment_state, :health_fail_streak, :last_health_error, :last_health_at
                )
                ON CONFLICT(daemon_id) DO UPDATE SET
                    host=excluded.host,
                    port=excluded.port,
                    pid=excluded.pid,
                    workspace_root=excluded.workspace_root,
                    protocol=excluded.protocol,
                    started_at=excluded.started_at,
                    last_seen_at=excluded.last_seen_at,
                    is_draining=excluded.is_draining,
                    deployment_state=excluded.deployment_state,
                    health_fail_streak=excluded.health_fail_streak,
                    last_health_error=excluded.last_health_error,
                    last_health_at=excluded.last_health_at
                """,
                entry.to_sql_params(),
            )
            conn.commit()

    def touch(self, daemon_id: str, seen_at: str) -> None:
        """지정 데몬의 last_seen 시각을 갱신한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE daemon_registry
                SET last_seen_at = :seen_at
                WHERE daemon_id = :daemon_id
                """,
                {"daemon_id": daemon_id, "seen_at": seen_at},
            )
            conn.commit()

    def record_health_result(self, daemon_id: str, ok: bool, health_at: str, error_message: str | None = None) -> None:
        """헬스체크 결과를 반영한다."""
        with connect(self._db_path) as conn:
            if ok:
                conn.execute(
                    """
                    UPDATE daemon_registry
                    SET health_fail_streak = 0,
                        last_health_error = NULL,
                        last_health_at = :health_at,
                        deployment_state = 'ACTIVE'
                    WHERE daemon_id = :daemon_id
                    """,
                    {"daemon_id": daemon_id, "health_at": health_at},
                )
            else:
                conn.execute(
                    """
                    UPDATE daemon_registry
                    SET health_fail_streak = health_fail_streak + 1,
                        last_health_error = :error_message,
                        last_health_at = :health_at,
                        deployment_state = CASE
                            WHEN health_fail_streak + 1 >= 3 THEN 'DEGRADED'
                            ELSE deployment_state
                        END
                    WHERE daemon_id = :daemon_id
                    """,
                    {"daemon_id": daemon_id, "health_at": health_at, "error_message": error_message},
                )
            conn.commit()

    def remove_by_id(self, daemon_id: str) -> None:
        """지정 데몬 엔트리를 삭제한다."""
        with connect(self._db_path) as conn:
            conn.execute("DELETE FROM daemon_registry WHERE daemon_id = :daemon_id", {"daemon_id": daemon_id})
            conn.commit()

    def remove_by_pid(self, pid: int) -> None:
        """지정 PID 엔트리를 삭제한다."""
        with connect(self._db_path) as conn:
            conn.execute("DELETE FROM daemon_registry WHERE pid = :pid", {"pid": pid})
            conn.commit()

    def set_draining(self, daemon_id: str, is_draining: bool) -> None:
        """지정 데몬 draining 상태를 변경한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE daemon_registry
                SET is_draining = :is_draining
                WHERE daemon_id = :daemon_id
                """,
                {"daemon_id": daemon_id, "is_draining": 1 if is_draining else 0},
            )
            conn.commit()

    def list_all(self) -> list[DaemonRegistryEntryDTO]:
        """등록된 데몬 엔트리 전체를 반환한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT daemon_id, host, port, pid, workspace_root, protocol, started_at, last_seen_at, is_draining,
                       deployment_state, health_fail_streak, last_health_error, last_health_at
                FROM daemon_registry
                ORDER BY last_seen_at DESC, daemon_id ASC
                """
            ).fetchall()
        items: list[DaemonRegistryEntryDTO] = []
        for row in rows:
            items.append(
                DaemonRegistryEntryDTO(
                    daemon_id=row_str(row, "daemon_id"),
                    host=row_str(row, "host"),
                    port=row_int(row, "port"),
                    pid=row_int(row, "pid"),
                    workspace_root=row_str(row, "workspace_root"),
                    protocol=row_str(row, "protocol"),
                    started_at=row_str(row, "started_at"),
                    last_seen_at=row_str(row, "last_seen_at"),
                    is_draining=row_bool(row, "is_draining"),
                    deployment_state=row_str(row, "deployment_state"),
                    health_fail_streak=row_int(row, "health_fail_streak"),
                    last_health_error=row_optional_str(row, "last_health_error"),
                    last_health_at=row_optional_str(row, "last_health_at"),
                )
            )
        return items

    def resolve_latest(self, workspace_root: str) -> DaemonRegistryEntryDTO | None:
        """워크스페이스 기준 최신 non-draining 데몬을 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT daemon_id, host, port, pid, workspace_root, protocol, started_at, last_seen_at, is_draining,
                       deployment_state, health_fail_streak, last_health_error, last_health_at
                FROM daemon_registry
                WHERE workspace_root = :workspace_root
                  AND is_draining = 0
                  AND deployment_state = 'ACTIVE'
                ORDER BY last_seen_at DESC, started_at DESC
                LIMIT 1
                """,
                {"workspace_root": workspace_root},
            ).fetchone()
        if row is None:
            return None
        return DaemonRegistryEntryDTO(
            daemon_id=row_str(row, "daemon_id"),
            host=row_str(row, "host"),
            port=row_int(row, "port"),
            pid=row_int(row, "pid"),
            workspace_root=row_str(row, "workspace_root"),
            protocol=row_str(row, "protocol"),
            started_at=row_str(row, "started_at"),
            last_seen_at=row_str(row, "last_seen_at"),
            is_draining=row_bool(row, "is_draining"),
            deployment_state=row_str(row, "deployment_state"),
            health_fail_streak=row_int(row, "health_fail_streak"),
            last_health_error=row_optional_str(row, "last_health_error"),
            last_health_at=row_optional_str(row, "last_health_at"),
        )
