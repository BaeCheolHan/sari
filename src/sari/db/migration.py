"""Alembic 마이그레이션 실행 유틸리티를 제공한다."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

HEAD_VERSION = "20260218_0004"
BASELINE_VERSION = "20260216_0001"


def ensure_migrated(db_path: Path) -> None:
    """DB를 Alembic head 리비전까지 업그레이드한다."""
    if os.getenv("SARI_DISABLE_ALEMBIC_AUTO", "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    try:
        from alembic import command
        from alembic.config import Config
    except (ImportError, ModuleNotFoundError):
        _fallback_upgrade_sqlite(db_path)
        return
    project_root = Path(__file__).resolve().parents[3]
    alembic_ini = project_root / "alembic.ini"
    alembic_dir = project_root / "alembic"
    if not alembic_ini.exists() or not alembic_dir.exists():
        _fallback_upgrade_sqlite(db_path)
        return
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(alembic_dir))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    # CLI JSON stdout 오염 방지를 위해 Alembic 기본 로거 설정을 비활성화한다.
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


def _connect(db_path: Path) -> sqlite3.Connection:
    """SQLite 연결을 생성한다."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """테이블 컬럼 집합을 조회한다."""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_version_table(conn: sqlite3.Connection) -> str:
    """alembic_version 테이블을 보장하고 현재 버전을 반환한다."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        )
        """
    )
    row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO alembic_version(version_num) VALUES(:version_num)", {"version_num": BASELINE_VERSION})
        return BASELINE_VERSION
    return str(row["version_num"])


def _set_version(conn: sqlite3.Connection, version_num: str) -> None:
    """alembic_version 값을 갱신한다."""
    conn.execute("UPDATE alembic_version SET version_num = :version_num", {"version_num": version_num})


def _fallback_upgrade_sqlite(db_path: Path) -> None:
    """alembic 미설치 환경에서 head 스키마를 보장한다."""
    with _connect(db_path) as conn:
        current_version = _ensure_version_table(conn)
        if current_version < "20260217_0002":
            _fallback_upgrade_0002(conn)
            _set_version(conn, "20260217_0002")
            current_version = "20260217_0002"
        if current_version < "20260217_0003":
            _fallback_upgrade_0003(conn)
            _set_version(conn, "20260217_0003")
            current_version = "20260217_0003"
        if current_version < "20260218_0004":
            _fallback_upgrade_0004(conn)
            _set_version(conn, "20260218_0004")
        conn.commit()


def _fallback_upgrade_0002(conn: sqlite3.Connection) -> None:
    """0002 리비전 컬럼을 적용한다."""
    daemon_cols = _table_columns(conn, "daemon_runtime")
    if "last_heartbeat_at" not in daemon_cols:
        conn.execute("ALTER TABLE daemon_runtime ADD COLUMN last_heartbeat_at TEXT NULL")
    if "last_exit_reason" not in daemon_cols:
        conn.execute("ALTER TABLE daemon_runtime ADD COLUMN last_exit_reason TEXT NULL")
    conn.execute(
        """
        UPDATE daemon_runtime
        SET last_heartbeat_at = COALESCE(last_heartbeat_at, started_at)
        WHERE singleton_key = 'default'
        """
    )
    queue_cols = _table_columns(conn, "file_enrich_queue")
    if "priority" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 30")
    if "enqueue_source" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN enqueue_source TEXT NOT NULL DEFAULT 'scan'")
    policy_cols = _table_columns(conn, "pipeline_policy")
    if "bootstrap_mode_enabled" not in policy_cols:
        conn.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_mode_enabled INTEGER NOT NULL DEFAULT 0")
    if "bootstrap_l3_worker_count" not in policy_cols:
        conn.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_l3_worker_count INTEGER NOT NULL DEFAULT 1")
    if "bootstrap_l3_queue_max" not in policy_cols:
        conn.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_l3_queue_max INTEGER NOT NULL DEFAULT 1000")
    if "bootstrap_exit_min_l2_coverage_bps" not in policy_cols:
        conn.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_exit_min_l2_coverage_bps INTEGER NOT NULL DEFAULT 9500")
    if "bootstrap_exit_max_sec" not in policy_cols:
        conn.execute("ALTER TABLE pipeline_policy ADD COLUMN bootstrap_exit_max_sec INTEGER NOT NULL DEFAULT 1800")
    error_cols = _table_columns(conn, "pipeline_error_events")
    if "scope_type" not in error_cols:
        conn.execute(
            "ALTER TABLE pipeline_error_events ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'GLOBAL' CHECK (scope_type IN ('GLOBAL', 'REPO'))"
        )
    conn.execute(
        """
        UPDATE pipeline_error_events
        SET scope_type = CASE
            WHEN repo_root IS NULL OR TRIM(repo_root) = '' THEN 'GLOBAL'
            ELSE 'REPO'
        END
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_scope_time
        ON pipeline_error_events(scope_type, occurred_at DESC)
        """
    )


def _fallback_upgrade_0003(conn: sqlite3.Connection) -> None:
    """0003 리비전 컬럼을 적용한다."""
    matrix_cols = _table_columns(conn, "pipeline_lsp_matrix_runs")
    if "required_languages_json" not in matrix_cols:
        conn.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN required_languages_json TEXT NOT NULL DEFAULT '[]'")
    if "fail_on_unavailable" not in matrix_cols:
        conn.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN fail_on_unavailable INTEGER NOT NULL DEFAULT 1")
    if "strict_symbol_gate" not in matrix_cols:
        conn.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN strict_symbol_gate INTEGER NOT NULL DEFAULT 1")
    if "started_at" not in matrix_cols:
        conn.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN started_at TEXT NOT NULL DEFAULT ''")
    if "finished_at" not in matrix_cols:
        conn.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN finished_at TEXT NULL")
    if "status" not in matrix_cols:
        conn.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'RUNNING'")
    if "summary_json" not in matrix_cols:
        conn.execute("ALTER TABLE pipeline_lsp_matrix_runs ADD COLUMN summary_json TEXT NULL")

    probe_cols = _table_columns(conn, "language_probe_status")
    if "enabled" not in probe_cols:
        conn.execute("ALTER TABLE language_probe_status ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    if "available" not in probe_cols:
        conn.execute("ALTER TABLE language_probe_status ADD COLUMN available INTEGER NOT NULL DEFAULT 0")
    if "last_probe_at" not in probe_cols:
        conn.execute("ALTER TABLE language_probe_status ADD COLUMN last_probe_at TEXT NULL")
    if "last_error_code" not in probe_cols:
        conn.execute("ALTER TABLE language_probe_status ADD COLUMN last_error_code TEXT NULL")
    if "last_error_message" not in probe_cols:
        conn.execute("ALTER TABLE language_probe_status ADD COLUMN last_error_message TEXT NULL")
    if "updated_at" not in probe_cols:
        conn.execute("ALTER TABLE language_probe_status ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")

    symbol_cols = _table_columns(conn, "lsp_symbols")
    if "symbol_key" not in symbol_cols:
        conn.execute("ALTER TABLE lsp_symbols ADD COLUMN symbol_key TEXT NULL")
    if "parent_symbol_key" not in symbol_cols:
        conn.execute("ALTER TABLE lsp_symbols ADD COLUMN parent_symbol_key TEXT NULL")
    if "depth" not in symbol_cols:
        conn.execute("ALTER TABLE lsp_symbols ADD COLUMN depth INTEGER NOT NULL DEFAULT 0")
    if "container_name" not in symbol_cols:
        conn.execute("ALTER TABLE lsp_symbols ADD COLUMN container_name TEXT NULL")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lsp_symbols_repo_path_depth
        ON lsp_symbols(repo_root, relative_path, depth, line)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lsp_symbols_symbol_key
        ON lsp_symbols(repo_root, relative_path, content_hash, symbol_key)
        """
    )


def _fallback_upgrade_0004(conn: sqlite3.Connection) -> None:
    """0004 리비전 컬럼을 적용한다."""
    registry_cols = _table_columns(conn, "daemon_registry")
    if "deployment_state" not in registry_cols:
        conn.execute(
            "ALTER TABLE daemon_registry ADD COLUMN deployment_state TEXT NOT NULL DEFAULT 'ACTIVE'"
        )
    if "health_fail_streak" not in registry_cols:
        conn.execute(
            "ALTER TABLE daemon_registry ADD COLUMN health_fail_streak INTEGER NOT NULL DEFAULT 0"
        )
    if "last_health_error" not in registry_cols:
        conn.execute("ALTER TABLE daemon_registry ADD COLUMN last_health_error TEXT NULL")
    if "last_health_at" not in registry_cols:
        conn.execute("ALTER TABLE daemon_registry ADD COLUMN last_health_at TEXT NULL")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daemon_registry_workspace
        ON daemon_registry(workspace_root, is_draining, deployment_state, last_seen_at DESC)
        """
    )
