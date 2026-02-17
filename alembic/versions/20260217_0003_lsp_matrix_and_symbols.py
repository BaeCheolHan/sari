"""lsp matrix and symbol hierarchy upgrade.

Revision ID: 20260217_0003
Revises: 20260217_0002
Create Date: 2026-02-17
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260217_0003"
down_revision = "20260217_0002"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    """테이블의 현재 컬럼 집합을 반환한다."""
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def upgrade() -> None:
    """LSP/언어 매트릭스/심볼 계층 컬럼을 추가한다."""
    matrix_cols = _table_columns("pipeline_lsp_matrix_runs")
    if "required_languages_json" not in matrix_cols:
        op.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN required_languages_json TEXT NOT NULL DEFAULT '[]'")
    if "fail_on_unavailable" not in matrix_cols:
        op.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN fail_on_unavailable INTEGER NOT NULL DEFAULT 1")
    if "strict_symbol_gate" not in matrix_cols:
        op.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN strict_symbol_gate INTEGER NOT NULL DEFAULT 1")
    if "started_at" not in matrix_cols:
        op.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN started_at TEXT NOT NULL DEFAULT ''")
    if "finished_at" not in matrix_cols:
        op.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN finished_at TEXT NULL")
    if "status" not in matrix_cols:
        op.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'RUNNING'")
    if "summary_json" not in matrix_cols:
        op.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN summary_json TEXT NULL")

    probe_cols = _table_columns("language_probe_status")
    if "enabled" not in probe_cols:
        op.execute("ALTER TABLE language_probe_status ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    if "available" not in probe_cols:
        op.execute("ALTER TABLE language_probe_status ADD COLUMN available INTEGER NOT NULL DEFAULT 0")
    if "last_probe_at" not in probe_cols:
        op.execute("ALTER TABLE language_probe_status ADD COLUMN last_probe_at TEXT NULL")
    if "last_error_code" not in probe_cols:
        op.execute("ALTER TABLE language_probe_status ADD COLUMN last_error_code TEXT NULL")
    if "last_error_message" not in probe_cols:
        op.execute("ALTER TABLE language_probe_status ADD COLUMN last_error_message TEXT NULL")
    if "updated_at" not in probe_cols:
        op.execute("ALTER TABLE language_probe_status ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")

    symbol_cols = _table_columns("lsp_symbols")
    if "symbol_key" not in symbol_cols:
        op.execute("ALTER TABLE lsp_symbols ADD COLUMN symbol_key TEXT NULL")
    if "parent_symbol_key" not in symbol_cols:
        op.execute("ALTER TABLE lsp_symbols ADD COLUMN parent_symbol_key TEXT NULL")
    if "depth" not in symbol_cols:
        op.execute("ALTER TABLE lsp_symbols ADD COLUMN depth INTEGER NOT NULL DEFAULT 0")
    if "container_name" not in symbol_cols:
        op.execute("ALTER TABLE lsp_symbols ADD COLUMN container_name TEXT NULL")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lsp_symbols_repo_path_depth
        ON lsp_symbols(repo_root, relative_path, depth, line)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lsp_symbols_symbol_key
        ON lsp_symbols(repo_root, relative_path, content_hash, symbol_key)
        """
    )


def downgrade() -> None:
    """SQLite 운영 특성상 안전한 down migration은 제공하지 않는다."""
    pass
