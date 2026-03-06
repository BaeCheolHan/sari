"""repo 식별자 SSOT 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import RepoIdentityDTO
from sari.db.row_mapper import row_optional_str, row_str
from sari.db.schema import connect


class RepoRegistryRepository:
    """repositories 테이블 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def upsert(self, identity: RepoIdentityDTO) -> None:
        """repo_id 기준으로 레지스트리 정보를 업서트한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO repositories(repo_id, repo_label, repo_root, workspace_root, updated_at, is_active)
                VALUES(:repo_id, :repo_label, :repo_root, :workspace_root, :updated_at, 1)
                ON CONFLICT(repo_id) DO UPDATE SET
                    repo_label = excluded.repo_label,
                    repo_root = excluded.repo_root,
                    workspace_root = excluded.workspace_root,
                    updated_at = excluded.updated_at,
                    is_active = 1
                """,
                identity.to_sql_params(),
            )
            conn.commit()

    def get_by_repo_root(self, repo_root: str) -> RepoIdentityDTO | None:
        """repo_root로 레지스트리 항목을 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT repo_id, repo_label, repo_root, workspace_root, updated_at
                FROM repositories
                WHERE repo_root = :repo_root
                LIMIT 1
                """,
                {"repo_root": repo_root},
            ).fetchone()
        if row is None:
            return None
        return RepoIdentityDTO(
            repo_id=row_str(row, "repo_id"),
            repo_label=row_str(row, "repo_label"),
            repo_root=row_str(row, "repo_root"),
            workspace_root=row_optional_str(row, "workspace_root"),
            updated_at=row_str(row, "updated_at"),
        )

    def get_by_repo_id(self, repo_id: str) -> RepoIdentityDTO | None:
        """repo_id로 레지스트리 항목을 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT repo_id, repo_label, repo_root, workspace_root, updated_at
                FROM repositories
                WHERE repo_id = :repo_id
                LIMIT 1
                """,
                {"repo_id": repo_id},
            ).fetchone()
        if row is None:
            return None
        return RepoIdentityDTO(
            repo_id=row_str(row, "repo_id"),
            repo_label=row_str(row, "repo_label"),
            repo_root=row_str(row, "repo_root"),
            workspace_root=row_optional_str(row, "workspace_root"),
            updated_at=row_str(row, "updated_at"),
        )

    def get_by_repo_label(self, repo_label: str) -> RepoIdentityDTO | None:
        """repo_label로 레지스트리 항목을 조회한다.

        workspace_root 단위로만 label 유니크가 보장되므로 다중 매칭 시 None을 반환한다.
        """
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT repo_id, repo_label, repo_root, workspace_root, updated_at
                FROM repositories
                WHERE repo_label = :repo_label
                ORDER BY updated_at DESC, repo_id ASC
                """,
                {"repo_label": repo_label},
            ).fetchall()
        if len(rows) != 1:
            return None
        row = rows[0]
        return RepoIdentityDTO(
            repo_id=row_str(row, "repo_id"),
            repo_label=row_str(row, "repo_label"),
            repo_root=row_str(row, "repo_root"),
            workspace_root=row_optional_str(row, "workspace_root"),
            updated_at=row_str(row, "updated_at"),
        )
