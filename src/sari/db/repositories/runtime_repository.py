"""데몬 런타임 상태 저장소를 구현한다."""

from pathlib import Path
from datetime import datetime, timedelta, timezone

from sari.core.models import DaemonRuntimeDTO
from sari.db.row_mapper import row_int, row_optional_str, row_str
from sari.db.schema import connect


class RuntimeRepository:
    """데몬 단일 런타임 상태를 영속화한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def upsert_runtime(self, runtime: DaemonRuntimeDTO) -> None:
        """데몬 런타임 상태를 업서트한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO daemon_runtime(
                    singleton_key, pid, host, port, state, started_at, session_count, last_heartbeat_at, last_exit_reason,
                    lease_token, owner_generation, updated_at, lease_expires_at
                )
                VALUES(
                    :singleton_key, :pid, :host, :port, :state, :started_at, :session_count, :last_heartbeat_at, :last_exit_reason,
                    :lease_token, :owner_generation, :updated_at, :lease_expires_at
                )
                ON CONFLICT(singleton_key) DO UPDATE SET
                    pid = excluded.pid,
                    host = excluded.host,
                    port = excluded.port,
                    state = excluded.state,
                    started_at = excluded.started_at,
                    session_count = excluded.session_count,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    last_exit_reason = excluded.last_exit_reason,
                    lease_token = excluded.lease_token,
                    owner_generation = excluded.owner_generation,
                    updated_at = excluded.updated_at,
                    lease_expires_at = excluded.lease_expires_at
                """,
                runtime.to_sql_params(),
            )
            conn.commit()

    def upsert_runtime_if_newer_generation(self, runtime: DaemonRuntimeDTO) -> bool:
        """owner_generation이 최신일 때만 런타임 상태를 업서트한다."""
        with connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO daemon_runtime(
                    singleton_key, pid, host, port, state, started_at, session_count, last_heartbeat_at, last_exit_reason,
                    lease_token, owner_generation, updated_at, lease_expires_at
                )
                VALUES(
                    :singleton_key, :pid, :host, :port, :state, :started_at, :session_count, :last_heartbeat_at, :last_exit_reason,
                    :lease_token, :owner_generation, :updated_at, :lease_expires_at
                )
                ON CONFLICT(singleton_key) DO UPDATE SET
                    pid = excluded.pid,
                    host = excluded.host,
                    port = excluded.port,
                    state = excluded.state,
                    started_at = excluded.started_at,
                    session_count = excluded.session_count,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    last_exit_reason = excluded.last_exit_reason,
                    lease_token = excluded.lease_token,
                    owner_generation = excluded.owner_generation,
                    updated_at = excluded.updated_at,
                    lease_expires_at = excluded.lease_expires_at
                WHERE COALESCE(daemon_runtime.owner_generation, 0) < excluded.owner_generation
                """,
                runtime.to_sql_params(),
            )
            conn.commit()
            return int(cursor.rowcount if cursor.rowcount is not None else 0) > 0

    def get_runtime(self) -> DaemonRuntimeDTO | None:
        """현재 데몬 런타임 상태를 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT pid, host, port, state, started_at, session_count, last_heartbeat_at, last_exit_reason,
                       lease_token, COALESCE(owner_generation, 0) AS owner_generation,
                       COALESCE(updated_at, last_heartbeat_at) AS updated_at,
                       lease_expires_at
                FROM daemon_runtime
                WHERE singleton_key = 'default'
                """
            ).fetchone()
        if row is None:
            return None
        return DaemonRuntimeDTO(
            pid=row_int(row, "pid"),
            host=row_str(row, "host"),
            port=row_int(row, "port"),
            state=row_str(row, "state"),
            started_at=row_str(row, "started_at"),
            session_count=row_int(row, "session_count"),
            last_heartbeat_at=row_str(row, "last_heartbeat_at"),
            last_exit_reason=row_optional_str(row, "last_exit_reason"),
            lease_token=row_optional_str(row, "lease_token"),
            owner_generation=row_int(row, "owner_generation"),
            updated_at=row_optional_str(row, "updated_at"),
            lease_expires_at=row_optional_str(row, "lease_expires_at"),
        )

    def clear_runtime(self) -> None:
        """데몬 런타임 상태를 삭제한다."""
        with connect(self._db_path) as conn:
            conn.execute("DELETE FROM daemon_runtime WHERE singleton_key = 'default'")
            conn.commit()

    def touch_heartbeat(self, pid: int, heartbeat_at: str) -> None:
        """지정 PID 런타임의 heartbeat 타임스탬프를 갱신한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE daemon_runtime
                SET last_heartbeat_at = :heartbeat_at,
                    updated_at = :heartbeat_at
                WHERE singleton_key = 'default' AND pid = :pid
                """,
                {"heartbeat_at": heartbeat_at, "pid": pid},
            )
            conn.commit()

    def touch_heartbeat_and_extend_lease(self, pid: int, heartbeat_at: str, lease_ttl_sec: int) -> None:
        """heartbeat와 lease 만료 시각을 함께 갱신한다."""
        expires_at = (
            datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            + timedelta(seconds=max(1, int(lease_ttl_sec)))
        ).isoformat()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE daemon_runtime
                SET last_heartbeat_at = :heartbeat_at,
                    updated_at = :heartbeat_at,
                    lease_expires_at = :lease_expires_at
                WHERE singleton_key = 'default' AND pid = :pid
                """,
                {"heartbeat_at": heartbeat_at, "lease_expires_at": expires_at, "pid": pid},
            )
            conn.commit()

    def increment_session(self) -> None:
        """활성 세션 수를 1 증가시킨다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE daemon_runtime
                SET session_count = session_count + 1
                WHERE singleton_key = 'default'
                """
            )
            conn.commit()

    def decrement_session(self) -> None:
        """활성 세션 수를 1 감소시키되 하한 0을 유지한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE daemon_runtime
                SET session_count = CASE
                    WHEN session_count > 0 THEN session_count - 1
                    ELSE 0
                END
                WHERE singleton_key = 'default'
                """
            )
            conn.commit()

    def reset_session_count(self) -> None:
        """활성 세션 수를 0으로 초기화한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE daemon_runtime
                SET session_count = 0
                WHERE singleton_key = 'default'
                """
            )
            conn.commit()

    def mark_exit_reason(self, pid: int, exit_reason: str, heartbeat_at: str) -> None:
        """지정 PID 런타임의 종료 사유를 기록한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE daemon_runtime
                SET last_exit_reason = :exit_reason,
                    last_heartbeat_at = :heartbeat_at,
                    updated_at = :heartbeat_at
                WHERE singleton_key = 'default' AND pid = :pid
                """,
                {"exit_reason": exit_reason, "heartbeat_at": heartbeat_at, "pid": pid},
            )
            conn.execute(
                """
                INSERT INTO daemon_runtime_history(pid, exit_reason, occurred_at)
                VALUES(:pid, :exit_reason, :occurred_at)
                """,
                {"pid": pid, "exit_reason": exit_reason, "occurred_at": heartbeat_at},
            )
            conn.commit()

    def clear_stale_runtime(self, cutoff_iso: str) -> int:
        """stale heartbeat 런타임을 정리하고 삭제 건수를 반환한다."""
        with connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM daemon_runtime
                WHERE singleton_key = 'default'
                  AND last_heartbeat_at < :cutoff_iso
                """,
                {"cutoff_iso": cutoff_iso},
            )
            conn.commit()
            return int(cursor.rowcount if cursor.rowcount >= 0 else 0)

    def get_latest_exit_event(self) -> dict[str, object] | None:
        """최근 종료 이벤트를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT pid, exit_reason, occurred_at
                FROM daemon_runtime_history
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "pid": row_int(row, "pid"),
            "exit_reason": row_str(row, "exit_reason"),
            "occurred_at": row_str(row, "occurred_at"),
        }
