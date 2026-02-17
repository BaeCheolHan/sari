"""언어별 LSP readiness 스냅샷 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import LanguageProbeStatusDTO
from sari.db.row_mapper import row_bool, row_optional_str, row_str
from sari.db.schema import connect


class LanguageProbeRepository:
    """언어 readiness 스냅샷을 영속화한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def upsert_result(
        self,
        language: str,
        enabled: bool,
        available: bool,
        last_probe_at: str | None,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> None:
        """단일 언어 readiness 결과를 upsert한다."""
        dto = LanguageProbeStatusDTO(
            language=language,
            enabled=enabled,
            available=available,
            last_probe_at=last_probe_at,
            last_error_code=last_error_code,
            last_error_message=last_error_message,
            updated_at=last_probe_at or "",
        )
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO language_probe_status(
                    language, enabled, available, last_probe_at, last_error_code, last_error_message, updated_at
                )
                VALUES(
                    :language, :enabled, :available, :last_probe_at, :last_error_code, :last_error_message, :updated_at
                )
                ON CONFLICT(language) DO UPDATE SET
                    enabled = excluded.enabled,
                    available = excluded.available,
                    last_probe_at = excluded.last_probe_at,
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message,
                    updated_at = excluded.updated_at
                """,
                dto.to_sql_params(),
            )
            conn.commit()

    def list_all(self) -> list[LanguageProbeStatusDTO]:
        """저장된 전체 언어 readiness 스냅샷을 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT language, enabled, available, last_probe_at, last_error_code, last_error_message, updated_at
                FROM language_probe_status
                ORDER BY language ASC
                """
            ).fetchall()
        items: list[LanguageProbeStatusDTO] = []
        for row in rows:
            items.append(
                LanguageProbeStatusDTO(
                    language=row_str(row, "language"),
                    enabled=row_bool(row, "enabled"),
                    available=row_bool(row, "available"),
                    last_probe_at=row_optional_str(row, "last_probe_at"),
                    last_error_code=row_optional_str(row, "last_error_code"),
                    last_error_message=row_optional_str(row, "last_error_message"),
                    updated_at=row_str(row, "updated_at"),
                )
            )
        return items
