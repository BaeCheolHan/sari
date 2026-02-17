"""심볼 중요도 캐시 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.row_mapper import row_int, row_str
from sari.db.schema import connect


class SymbolImportanceRepository:
    """심볼 fan-in 기반 중요도 캐시를 영속화한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def get_reference_count(self, repo_root: str, symbol_name: str, revision_epoch: int = 0) -> int | None:
        """캐시된 참조 파일 수를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT reference_count
                FROM symbol_importance_cache
                WHERE repo_root = :repo_root
                  AND symbol_name = :symbol_name
                  AND revision_epoch = :revision_epoch
                """,
                {
                    "repo_root": repo_root,
                    "symbol_name": symbol_name,
                    "revision_epoch": revision_epoch,
                },
            ).fetchone()
        if row is None:
            return None
        return row_int(row, "reference_count")

    def upsert_reference_count(
        self,
        repo_root: str,
        symbol_name: str,
        reference_count: int,
        updated_at: str,
        revision_epoch: int = 0,
    ) -> None:
        """심볼 참조 파일 수를 캐시에 업서트한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO symbol_importance_cache(repo_root, symbol_name, reference_count, revision_epoch, updated_at)
                VALUES(:repo_root, :symbol_name, :reference_count, :revision_epoch, :updated_at)
                ON CONFLICT(repo_root, symbol_name) DO UPDATE SET
                    reference_count = excluded.reference_count,
                    revision_epoch = excluded.revision_epoch,
                    updated_at = excluded.updated_at
                """,
                {
                    "repo_root": repo_root,
                    "symbol_name": symbol_name,
                    "reference_count": reference_count,
                    "revision_epoch": revision_epoch,
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def list_top_symbols(self, repo_root: str, limit: int = 20) -> list[dict[str, object]]:
        """저장소 기준 상위 참조 심볼 캐시를 반환한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT symbol_name, reference_count, revision_epoch, updated_at
                FROM symbol_importance_cache
                WHERE repo_root = :repo_root
                ORDER BY reference_count DESC, symbol_name ASC
                LIMIT :limit
                """,
                {"repo_root": repo_root, "limit": limit},
            ).fetchall()
        return [
            {
                "symbol_name": row_str(row, "symbol_name"),
                "reference_count": row_int(row, "reference_count"),
                "revision_epoch": row_int(row, "revision_epoch"),
                "updated_at": row_str(row, "updated_at"),
            }
            for row in rows
        ]
