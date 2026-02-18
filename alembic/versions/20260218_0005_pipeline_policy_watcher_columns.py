"""pipeline policy watcher columns.

Revision ID: 20260218_0005
Revises: 20260218_0004
Create Date: 2026-02-18
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_0005"
down_revision = "20260218_0004"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    """테이블의 현재 컬럼 집합을 반환한다."""
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def upgrade() -> None:
    """pipeline_policy watcher 제어 컬럼을 추가한다."""
    policy_cols = _table_columns("pipeline_policy")
    if "watcher_queue_max" not in policy_cols:
        op.execute("ALTER TABLE pipeline_policy ADD COLUMN watcher_queue_max INTEGER NOT NULL DEFAULT 10000")
    if "watcher_overflow_rescan_cooldown_sec" not in policy_cols:
        op.execute(
            "ALTER TABLE pipeline_policy ADD COLUMN watcher_overflow_rescan_cooldown_sec INTEGER NOT NULL DEFAULT 30"
        )


def downgrade() -> None:
    """SQLite 운영 특성상 안전한 down migration은 제공하지 않는다."""
    pass
