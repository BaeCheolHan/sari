"""file_enrich_queue defer/scope columns.

Revision ID: 20260222_0008
Revises: 20260219_0007
Create Date: 2026-02-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260222_0008"
down_revision = "20260219_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """file_enrich_queue에 defer/scope 관련 컬럼을 추가한다."""
    bind = op.get_bind()
    rows = bind.execute(sa.text("PRAGMA table_info(file_enrich_queue)")).fetchall()
    cols = {str(row[1]) for row in rows}
    if "defer_reason" not in cols:
        op.execute("ALTER TABLE file_enrich_queue ADD COLUMN defer_reason TEXT NULL")
    if "scope_level" not in cols:
        op.execute("ALTER TABLE file_enrich_queue ADD COLUMN scope_level TEXT NULL")
    if "scope_root" not in cols:
        op.execute("ALTER TABLE file_enrich_queue ADD COLUMN scope_root TEXT NULL")
    if "scope_attempts" not in cols:
        op.execute("ALTER TABLE file_enrich_queue ADD COLUMN scope_attempts INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    """SQLite 운영 특성상 안전한 down migration은 제공하지 않는다."""
    pass
