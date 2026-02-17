"""데몬 레지스트리 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import DaemonRegistryEntryDTO
from sari.db.row_mapper import row_bool, row_int, row_str
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
                    daemon_id, host, port, pid, workspace_root, protocol, started_at, last_seen_at, is_draining
                )
                VALUES(
                    :daemon_id, :host, :port, :pid, :workspace_root, :protocol, :started_at, :last_seen_at, :is_draining
                )
                ON CONFLICT(daemon_id) DO UPDATE SET
                    host=excluded.host,
                    port=excluded.port,
                    pid=excluded.pid,
                    workspace_root=excluded.workspace_root,
                    protocol=excluded.protocol,
                    started_at=excluded.started_at,
                    last_seen_at=excluded.last_seen_at,
                    is_draining=excluded.is_draining
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
                SELECT daemon_id, host, port, pid, workspace_root, protocol, started_at, last_seen_at, is_draining
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
                )
            )
        return items

    def resolve_latest(self, workspace_root: str) -> DaemonRegistryEntryDTO | None:
        """워크스페이스 기준 최신 non-draining 데몬을 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT daemon_id, host, port, pid, workspace_root, protocol, started_at, last_seen_at, is_draining
                FROM daemon_registry
                WHERE workspace_root = :workspace_root
                  AND is_draining = 0
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
        )

