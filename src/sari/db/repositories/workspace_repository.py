"""워크스페이스 저장소를 구현한다."""

from pathlib import Path

from sari.core.exceptions import ErrorContext, ValidationError
from sari.core.models import WorkspaceDTO
from sari.db.row_mapper import row_bool, row_optional_str, row_str
from sari.db.schema import connect


class WorkspaceRepository:
    """워크스페이스 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def add(self, workspace: WorkspaceDTO) -> None:
        """워크스페이스를 추가한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO workspaces(path, name, indexed_at, is_active)
                VALUES(:path, :name, :indexed_at, :is_active)
                """,
                workspace.to_sql_params(),
            )
            conn.commit()

    def get_by_path(self, path: str) -> WorkspaceDTO | None:
        """경로로 워크스페이스를 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT path, name, indexed_at, is_active
                FROM workspaces
                WHERE path = :path
                """,
                {"path": path},
            ).fetchone()
        if row is None:
            return None
        return self._workspace_from_row(row)

    def list_all(self) -> list[WorkspaceDTO]:
        """전체 워크스페이스를 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT path, name, indexed_at, is_active
                FROM workspaces
                ORDER BY path ASC
                """
            ).fetchall()
        results: list[WorkspaceDTO] = []
        for row in rows:
            results.append(self._workspace_from_row(row))
        return results

    def remove(self, path: str) -> None:
        """워크스페이스를 삭제한다."""
        with connect(self._db_path) as conn:
            conn.execute("DELETE FROM workspaces WHERE path = :path", {"path": path})
            conn.commit()

    def set_active(self, path: str, is_active: bool) -> None:
        """워크스페이스 활성 상태를 변경한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE workspaces
                SET is_active = :is_active
                WHERE path = :path
                """,
                {"path": path, "is_active": 1 if is_active else 0},
            )
            conn.commit()

    def _workspace_from_row(self, row: object) -> WorkspaceDTO:
        """DB Row를 WorkspaceDTO로 엄격 매핑한다."""
        if not hasattr(row, "__getitem__"):
            raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message="workspace row 형식이 올바르지 않습니다"))
        return WorkspaceDTO(
            path=row_str(row, "path"),  # type: ignore[arg-type]
            name=row_optional_str(row, "name"),  # type: ignore[arg-type]
            indexed_at=row_optional_str(row, "indexed_at"),  # type: ignore[arg-type]
            is_active=row_bool(row, "is_active"),  # type: ignore[arg-type]
        )
