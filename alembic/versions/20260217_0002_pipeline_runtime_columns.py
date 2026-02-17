"""pipeline/runtime column upgrade.

Revision ID: 20260217_0002
Revises: 20260216_0001
Create Date: 2026-02-17
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260217_0002"
down_revision = "20260216_0001"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    """테이블의 현재 컬럼 집합을 반환한다."""
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def upgrade() -> None:
    """런타임/파이프라인 핵심 확장 컬럼을 추가한다."""
    daemon_cols = _table_columns("daemon_runtime")
    if "last_heartbeat_at" not in daemon_cols:
        op.execute("ALTER TABLE daemon_runtime ADD COLUMN last_heartbeat_at TEXT NULL")
    if "last_exit_reason" not in daemon_cols:
        op.execute("ALTER TABLE daemon_runtime ADD COLUMN last_exit_reason TEXT NULL")
    op.execute(
        """
        UPDATE daemon_runtime
        SET last_heartbeat_at = COALESCE(last_heartbeat_at, started_at)
        WHERE singleton_key = 'default'
        """
    )

    queue_cols = _table_columns("file_enrich_queue")
    if "priority" not in queue_cols:
        op.execute("ALTER TABLE file_enrich_queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 30")
    if "enqueue_source" not in queue_cols:
        op.execute("ALTER TABLE file_enrich_queue ADD COLUMN enqueue_source TEXT NOT NULL DEFAULT 'scan'")

    policy_cols = _table_columns("pipeline_policy")
    if "bootstrap_mode_enabled" not in policy_cols:
        op.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_mode_enabled INTEGER NOT NULL DEFAULT 0")
    if "bootstrap_l3_worker_count" not in policy_cols:
        op.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_l3_worker_count INTEGER NOT NULL DEFAULT 1")
    if "bootstrap_l3_queue_max" not in policy_cols:
        op.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_l3_queue_max INTEGER NOT NULL DEFAULT 1000")
    if "bootstrap_exit_min_l2_coverage_bps" not in policy_cols:
        op.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_exit_min_l2_coverage_bps INTEGER NOT NULL DEFAULT 9500")
    if "bootstrap_exit_max_sec" not in policy_cols:
        op.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_exit_max_sec INTEGER NOT NULL DEFAULT 1800")

    error_cols = _table_columns("pipeline_error_events")
    if "scope_type" not in error_cols:
        op.execute(
            "ALTER TABLE pipeline_error_events ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'GLOBAL' CHECK (scope_type IN ('GLOBAL', 'REPO'))"
        )
    op.execute(
        """
        UPDATE pipeline_error_events
        SET scope_type = CASE
            WHEN repo_root IS NULL OR TRIM(repo_root) = '' THEN 'GLOBAL'
            ELSE 'REPO'
        END
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_scope_time
        ON pipeline_error_events(scope_type, occurred_at DESC)
        """
    )


def downgrade() -> None:
    """SQLite 운영 특성상 안전한 down migration은 제공하지 않는다."""
    pass
