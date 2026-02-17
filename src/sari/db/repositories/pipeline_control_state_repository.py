"""파이프라인 자동 제어 상태 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import PipelineAutoControlStateDTO, now_iso8601_utc
from sari.db.row_mapper import row_bool, row_str
from sari.db.schema import connect


class PipelineControlStateRepository:
    """자동제어 상태 SSOT를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소 DB 경로를 저장한다."""
        self._db_path = db_path

    def get_state(self) -> PipelineAutoControlStateDTO:
        """자동제어 상태를 조회한다."""
        self._ensure_default_row()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT auto_hold_enabled, auto_hold_active, last_action, updated_at
                FROM pipeline_control_state
                WHERE singleton_key = 'default'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("pipeline_control_state default row is missing")
        return PipelineAutoControlStateDTO(
            auto_hold_enabled=row_bool(row, "auto_hold_enabled"),
            auto_hold_active=row_bool(row, "auto_hold_active"),
            last_action=row_str(row, "last_action"),
            updated_at=row_str(row, "updated_at"),
        )

    def update_state(
        self,
        auto_hold_enabled: bool | None = None,
        auto_hold_active: bool | None = None,
        last_action: str | None = None,
    ) -> PipelineAutoControlStateDTO:
        """자동제어 상태를 부분 갱신한다."""
        current = self.get_state()
        next_enabled = current.auto_hold_enabled if auto_hold_enabled is None else auto_hold_enabled
        next_active = current.auto_hold_active if auto_hold_active is None else auto_hold_active
        next_action = current.last_action if last_action is None else last_action
        updated_at = now_iso8601_utc()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE pipeline_control_state
                SET auto_hold_enabled = :auto_hold_enabled,
                    auto_hold_active = :auto_hold_active,
                    last_action = :last_action,
                    updated_at = :updated_at
                WHERE singleton_key = 'default'
                """,
                {
                    "auto_hold_enabled": 1 if next_enabled else 0,
                    "auto_hold_active": 1 if next_active else 0,
                    "last_action": next_action,
                    "updated_at": updated_at,
                },
            )
            conn.commit()
        return PipelineAutoControlStateDTO(
            auto_hold_enabled=next_enabled,
            auto_hold_active=next_active,
            last_action=next_action,
            updated_at=updated_at,
        )

    def _ensure_default_row(self) -> None:
        """기본 상태 레코드를 보장한다."""
        now_iso = now_iso8601_utc()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pipeline_control_state(
                    singleton_key, auto_hold_enabled, auto_hold_active, last_action, updated_at
                )
                VALUES('default', 0, 0, 'initialized', :updated_at)
                ON CONFLICT(singleton_key) DO NOTHING
                """,
                {"updated_at": now_iso},
            )
            conn.commit()
