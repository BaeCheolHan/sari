"""baseline schema revision.

Revision ID: 20260216_0001
Revises:
Create Date: 2026-02-16
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260216_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """초기 리비전은 기존 schema bootstrap을 기준선으로만 등록한다."""
    pass


def downgrade() -> None:
    """baseline 리비전은 down migration을 제공하지 않는다."""
    pass
