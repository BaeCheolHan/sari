"""pipeline perf runs table.

Revision ID: 20260219_0007
Revises: 20260219_0006
Create Date: 2026-02-19
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260219_0007"
down_revision = "20260219_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """pipeline_perf_runs 테이블을 추가한다."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_perf_runs (
            run_id TEXT PRIMARY KEY,
            repo_root TEXT NOT NULL CHECK (repo_root <> ''),
            target_files INTEGER NOT NULL,
            profile TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NULL,
            status TEXT NOT NULL,
            summary_json TEXT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pipeline_perf_runs_started
        ON pipeline_perf_runs(started_at DESC)
        """
    )


def downgrade() -> None:
    """SQLite 운영 특성상 안전한 down migration은 제공하지 않는다."""
    pass
