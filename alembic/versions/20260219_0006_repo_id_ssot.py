"""repo_id ssot columns.

Revision ID: 20260219_0006
Revises: 20260218_0005
Create Date: 2026-02-19
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260219_0006"
down_revision = "20260218_0005"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    """테이블의 현재 컬럼 집합을 반환한다."""
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _table_exists(table_name: str) -> bool:
    """테이블 존재 여부를 반환한다."""
    bind = op.get_bind()
    row = bind.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = :name",
        {"name": table_name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    """repo_id 기반 SSOT 테이블/컬럼을 추가한다."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS repositories (
            repo_id TEXT PRIMARY KEY,
            repo_label TEXT NOT NULL,
            repo_root TEXT NOT NULL,
            workspace_root TEXT NULL,
            updated_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_repositories_workspace_label
        ON repositories(workspace_root, repo_label)
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_repositories_root ON repositories(repo_root)")

    if _table_exists("collected_files_l1"):
        l1_cols = _table_columns("collected_files_l1")
        if "repo_id" not in l1_cols:
            op.execute("ALTER TABLE collected_files_l1 ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists("file_enrich_queue"):
        queue_cols = _table_columns("file_enrich_queue")
        if "repo_id" not in queue_cols:
            op.execute("ALTER TABLE file_enrich_queue ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists("candidate_index_changes"):
        change_cols = _table_columns("candidate_index_changes")
        if "repo_id" not in change_cols:
            op.execute("ALTER TABLE candidate_index_changes ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists("collected_file_bodies_l2"):
        body_cols = _table_columns("collected_file_bodies_l2")
        if "repo_id" not in body_cols:
            op.execute("ALTER TABLE collected_file_bodies_l2 ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists("lsp_symbols"):
        symbol_cols = _table_columns("lsp_symbols")
        if "repo_id" not in symbol_cols:
            op.execute("ALTER TABLE lsp_symbols ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists("lsp_call_relations"):
        relation_cols = _table_columns("lsp_call_relations")
        if "repo_id" not in relation_cols:
            op.execute("ALTER TABLE lsp_call_relations ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")

    if _table_exists("collected_files_l1"):
        op.execute(
            """
            INSERT OR IGNORE INTO repositories(repo_id, repo_label, repo_root, workspace_root, updated_at, is_active)
            SELECT
                'r_' || substr(lower(hex(randomblob(16))), 1, 20),
                COALESCE(NULLIF(repo_label, ''), repo_root),
                repo_root,
                NULL,
                MAX(updated_at),
                1
            FROM collected_files_l1
            GROUP BY repo_root, COALESCE(NULLIF(repo_label, ''), repo_root)
            """
        )
        op.execute(
            """
            UPDATE collected_files_l1
            SET repo_id = (
                SELECT repositories.repo_id
                FROM repositories
                WHERE repositories.repo_root = collected_files_l1.repo_root
                LIMIT 1
            )
            WHERE repo_id = ''
            """
        )
    if _table_exists("file_enrich_queue"):
        op.execute(
            """
            UPDATE file_enrich_queue
            SET repo_id = (
                SELECT repositories.repo_id
                FROM repositories
                WHERE repositories.repo_root = file_enrich_queue.repo_root
                LIMIT 1
            )
            WHERE repo_id = ''
            """
        )
    if _table_exists("candidate_index_changes"):
        op.execute(
            """
            UPDATE candidate_index_changes
            SET repo_id = (
                SELECT repositories.repo_id
                FROM repositories
                WHERE repositories.repo_root = candidate_index_changes.repo_root
                LIMIT 1
            )
            WHERE repo_id = ''
            """
        )
    if _table_exists("collected_files_l1"):
        op.execute("CREATE INDEX IF NOT EXISTS idx_collected_files_l1_repo_id ON collected_files_l1(repo_id, is_deleted)")
    if _table_exists("file_enrich_queue"):
        queue_cols = _table_columns("file_enrich_queue")
        if {"repo_id", "status", "next_retry_at"}.issubset(queue_cols):
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_enrich_queue_repo_id ON file_enrich_queue(repo_id, status, next_retry_at)"
            )
    if _table_exists("candidate_index_changes"):
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_index_changes_repo_id_status ON candidate_index_changes(repo_id, status, change_id)"
        )


def downgrade() -> None:
    """SQLite 운영 특성상 안전한 down migration은 제공하지 않는다."""
    pass
