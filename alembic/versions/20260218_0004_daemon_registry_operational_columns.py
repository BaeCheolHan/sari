"""daemon registry operational columns.

Revision ID: 20260218_0004
Revises: 20260217_0003
Create Date: 2026-02-18
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_0004"
down_revision = "20260217_0003"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    """테이블의 현재 컬럼 집합을 반환한다."""
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def upgrade() -> None:
    """daemon registry 운영 컬럼을 추가한다."""
    registry_cols = _table_columns("daemon_registry")
    if "deployment_state" not in registry_cols:
        op.execute(
            "ALTER TABLE daemon_registry ADD COLUMN deployment_state TEXT NOT NULL DEFAULT 'ACTIVE'"
        )
    if "health_fail_streak" not in registry_cols:
        op.execute(
            "ALTER TABLE daemon_registry ADD COLUMN health_fail_streak INTEGER NOT NULL DEFAULT 0"
        )
    if "last_health_error" not in registry_cols:
        op.execute("ALTER TABLE daemon_registry ADD COLUMN last_health_error TEXT NULL")
    if "last_health_at" not in registry_cols:
        op.execute("ALTER TABLE daemon_registry ADD COLUMN last_health_at TEXT NULL")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daemon_registry_workspace
        ON daemon_registry(workspace_root, is_draining, deployment_state, last_seen_at DESC)
        """
    )


def downgrade() -> None:
    """SQLite 운영 특성상 안전한 down migration은 제공하지 않는다."""
    pass
