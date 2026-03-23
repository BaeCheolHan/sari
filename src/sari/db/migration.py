"""Alembic 마이그레이션 실행 유틸리티를 제공한다."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

HEAD_VERSION = "20260222_0008"
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
    # Alembic head와 별개로 additive fallback 확장 테이블을 항상 보장한다.
    with _connect(db_path) as conn:
        _ensure_repo_language_probe_state_table(conn)
        _fallback_upgrade_0009(conn)
        _fallback_upgrade_0010(conn)
        _fallback_upgrade_0011(conn)
        _fallback_upgrade_0012(conn)
        _fallback_upgrade_0013(conn)
        _fallback_upgrade_0014(conn)
        _fallback_upgrade_0015(conn)
        conn.commit()


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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """테이블 존재 여부를 반환한다."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = :name",
        {"name": table_name},
    ).fetchone()
    return row is not None


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
            current_version = "20260218_0004"
        if current_version < "20260218_0005":
            _fallback_upgrade_0005(conn)
            _set_version(conn, "20260218_0005")
            current_version = "20260218_0005"
        if current_version < "20260219_0006":
            _fallback_upgrade_0006(conn)
            _set_version(conn, "20260219_0006")
            current_version = "20260219_0006"
        if current_version < "20260219_0007":
            _fallback_upgrade_0007(conn)
            _set_version(conn, "20260219_0007")
            current_version = "20260219_0007"
        if current_version < "20260222_0008":
            _fallback_upgrade_0008(conn)
            _set_version(conn, "20260222_0008")
        # Alembic head(0008) 이후 additive fallback 확장:
        # tool_data L3/L4/L5 분리 테이블은 버전 스탬프를 올리지 않고 보장한다.
        _fallback_upgrade_0009(conn)
        _fallback_upgrade_0010(conn)
        _fallback_upgrade_0011(conn)
        _fallback_upgrade_0012(conn)
        _fallback_upgrade_0013(conn)
        _fallback_upgrade_0014(conn)
        _fallback_upgrade_0015(conn)
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

    _ensure_repo_language_probe_state_table(conn)

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


def _ensure_repo_language_probe_state_table(conn: sqlite3.Connection) -> None:
    """repo_language_probe_state additive 테이블/컬럼을 보장한다."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repo_language_probe_state (
            repo_root TEXT NOT NULL CHECK (repo_root <> ''),
            language TEXT NOT NULL CHECK (language <> ''),
            status TEXT NOT NULL,
            fail_count INTEGER NOT NULL DEFAULT 0,
            inflight_phase TEXT NULL,
            next_retry_at TEXT NULL,
            last_error_code TEXT NULL,
            last_error_message TEXT NULL,
            last_trigger TEXT NULL,
            last_seen_at TEXT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(repo_root, language)
        )
        """
    )
    repo_probe_cols = _table_columns(conn, "repo_language_probe_state")
    if "status" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN status TEXT NOT NULL DEFAULT 'IDLE'")
    if "fail_count" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0")
    if "inflight_phase" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN inflight_phase TEXT NULL")
    if "next_retry_at" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN next_retry_at TEXT NULL")
    if "last_error_code" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN last_error_code TEXT NULL")
    if "last_error_message" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN last_error_message TEXT NULL")
    if "last_trigger" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN last_trigger TEXT NULL")
    if "last_seen_at" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN last_seen_at TEXT NULL")
    if "updated_at" not in repo_probe_cols:
        conn.execute("ALTER TABLE repo_language_probe_state ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_repo_language_probe_state_status
        ON repo_language_probe_state(status, updated_at DESC)
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


def _fallback_upgrade_0014(conn: sqlite3.Connection) -> None:
    """daemon_runtime lease/owner 컬럼을 보강한다."""
    if not _table_exists(conn, "daemon_runtime"):
        return
    daemon_cols = _table_columns(conn, "daemon_runtime")
    if "lease_token" not in daemon_cols:
        conn.execute("ALTER TABLE daemon_runtime ADD COLUMN lease_token TEXT NULL")
    if "owner_generation" not in daemon_cols:
        conn.execute("ALTER TABLE daemon_runtime ADD COLUMN owner_generation INTEGER NOT NULL DEFAULT 0")
    if "updated_at" not in daemon_cols:
        conn.execute("ALTER TABLE daemon_runtime ADD COLUMN updated_at TEXT NULL")
    if "lease_expires_at" not in daemon_cols:
        conn.execute("ALTER TABLE daemon_runtime ADD COLUMN lease_expires_at TEXT NULL")
    conn.execute(
        """
        UPDATE daemon_runtime
        SET updated_at = COALESCE(updated_at, last_heartbeat_at),
            owner_generation = COALESCE(owner_generation, 0)
        WHERE singleton_key = 'default'
        """
    )


def _fallback_upgrade_0015(conn: sqlite3.Connection) -> None:
    """Python semantic caller edge 테이블을 보장한다."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS python_semantic_call_edges (
            repo_id TEXT NOT NULL DEFAULT '',
            repo_root TEXT NOT NULL CHECK (repo_root <> ''),
            scope_repo_root TEXT NOT NULL DEFAULT '',
            relative_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            from_symbol TEXT NOT NULL,
            to_symbol TEXT NOT NULL,
            line INTEGER NOT NULL,
            evidence_type TEXT NOT NULL,
            confidence REAL NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(repo_root, relative_path, content_hash, from_symbol, to_symbol, line, evidence_type)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_python_semantic_call_edges_target
        ON python_semantic_call_edges(repo_root, to_symbol, relative_path, line)
        """
    )


def _fallback_upgrade_0005(conn: sqlite3.Connection) -> None:
    """0005 리비전 컬럼을 적용한다."""
    policy_cols = _table_columns(conn, "pipeline_policy")
    if "watcher_queue_max" not in policy_cols:
        conn.execute("ALTER TABLE pipeline_policy ADD COLUMN watcher_queue_max INTEGER NOT NULL DEFAULT 10000")
    if "watcher_overflow_rescan_cooldown_sec" not in policy_cols:
        conn.execute(
            "ALTER TABLE pipeline_policy ADD COLUMN watcher_overflow_rescan_cooldown_sec INTEGER NOT NULL DEFAULT 30"
        )


def _fallback_upgrade_0006(conn: sqlite3.Connection) -> None:
    """0006 리비전 repo_id SSOT 컬럼/테이블을 적용한다."""
    conn.execute(
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
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_repositories_workspace_label
        ON repositories(workspace_root, repo_label)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repositories_root ON repositories(repo_root)")

    if _table_exists(conn, "collected_files_l1"):
        l1_cols = _table_columns(conn, "collected_files_l1")
        if "repo_id" not in l1_cols:
            conn.execute("ALTER TABLE collected_files_l1 ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists(conn, "file_enrich_queue"):
        queue_cols = _table_columns(conn, "file_enrich_queue")
        if "repo_id" not in queue_cols:
            conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists(conn, "candidate_index_changes"):
        change_cols = _table_columns(conn, "candidate_index_changes")
        if "repo_id" not in change_cols:
            conn.execute("ALTER TABLE candidate_index_changes ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists(conn, "collected_file_bodies_l2"):
        body_cols = _table_columns(conn, "collected_file_bodies_l2")
        if "repo_id" not in body_cols:
            conn.execute("ALTER TABLE collected_file_bodies_l2 ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists(conn, "lsp_symbols"):
        symbol_cols = _table_columns(conn, "lsp_symbols")
        if "repo_id" not in symbol_cols:
            conn.execute("ALTER TABLE lsp_symbols ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
    if _table_exists(conn, "lsp_call_relations"):
        relation_cols = _table_columns(conn, "lsp_call_relations")
        if "repo_id" not in relation_cols:
            conn.execute("ALTER TABLE lsp_call_relations ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''")
        if "caller_relative_path" not in relation_cols:
            conn.execute("ALTER TABLE lsp_call_relations ADD COLUMN caller_relative_path TEXT NULL")

    if _table_exists(conn, "collected_files_l1"):
        conn.execute(
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
        conn.execute(
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
    if _table_exists(conn, "file_enrich_queue"):
        conn.execute(
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
    if _table_exists(conn, "candidate_index_changes"):
        conn.execute(
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
    if _table_exists(conn, "collected_files_l1"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_collected_files_l1_repo_id
            ON collected_files_l1(repo_id, is_deleted)
            """
        )
    if _table_exists(conn, "file_enrich_queue"):
        queue_cols = _table_columns(conn, "file_enrich_queue")
        if {"repo_id", "status", "next_retry_at"}.issubset(queue_cols):
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_file_enrich_queue_repo_id
                ON file_enrich_queue(repo_id, status, next_retry_at)
                """
            )
    if _table_exists(conn, "candidate_index_changes"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_candidate_index_changes_repo_id_status
            ON candidate_index_changes(repo_id, status, change_id)
            """
        )


def _fallback_upgrade_0007(conn: sqlite3.Connection) -> None:
    """0007 리비전 pipeline perf 실행 테이블을 적용한다."""
    conn.execute(
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
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pipeline_perf_runs_started
        ON pipeline_perf_runs(started_at DESC)
        """
    )


def _fallback_upgrade_0008(conn: sqlite3.Connection) -> None:
    """0008 리비전 file_enrich_queue defer/scope 컬럼을 적용한다."""
    if not _table_exists(conn, "file_enrich_queue"):
        return
    queue_cols = _table_columns(conn, "file_enrich_queue")
    if "defer_reason" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN defer_reason TEXT NULL")
    if "scope_level" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN scope_level TEXT NULL")
    if "scope_root" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN scope_root TEXT NULL")
    if "scope_attempts" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN scope_attempts INTEGER NOT NULL DEFAULT 0")


def _fallback_upgrade_0009(conn: sqlite3.Connection) -> None:
    """0009 리비전 tool_data L3/L4/L5 분리 테이블을 적용한다."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_data_l3_symbols (
            workspace_id TEXT NOT NULL,
            repo_root TEXT NOT NULL CHECK (repo_root <> ''),
            relative_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            symbols_json TEXT NOT NULL,
            degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0, 1)),
            l3_skipped_large_file INTEGER NOT NULL DEFAULT 0 CHECK (l3_skipped_large_file IN (0, 1)),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, repo_root, relative_path, content_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tool_data_l3_lookup
        ON tool_data_l3_symbols(workspace_id, repo_root, relative_path, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_data_l4_normalized_symbols (
            workspace_id TEXT NOT NULL,
            repo_root TEXT NOT NULL CHECK (repo_root <> ''),
            relative_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            normalized_json TEXT NOT NULL,
            confidence REAL NOT NULL,
            ambiguity REAL NOT NULL,
            coverage REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, repo_root, relative_path, content_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tool_data_l4_lookup
        ON tool_data_l4_normalized_symbols(workspace_id, repo_root, relative_path, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_data_l5_semantics (
            workspace_id TEXT NOT NULL,
            repo_root TEXT NOT NULL CHECK (repo_root <> ''),
            relative_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            semantics_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, repo_root, relative_path, content_hash, reason_code)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tool_data_l5_lookup
        ON tool_data_l5_semantics(workspace_id, repo_root, relative_path, updated_at DESC)
        """
    )


def _fallback_upgrade_0010(conn: sqlite3.Connection) -> None:
    """0010 리비전 file_enrich_queue deferred 상태머신 컬럼을 적용한다."""
    if not _table_exists(conn, "file_enrich_queue"):
        return
    queue_cols = _table_columns(conn, "file_enrich_queue")
    if "deferred_state" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN deferred_state TEXT NULL")
    if "deferred_count" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN deferred_count INTEGER NOT NULL DEFAULT 0")
    if "first_deferred_at" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN first_deferred_at TEXT NULL")
    if "last_deferred_at" not in queue_cols:
        conn.execute("ALTER TABLE file_enrich_queue ADD COLUMN last_deferred_at TEXT NULL")


def _fallback_upgrade_0011(conn: sqlite3.Connection) -> None:
    """0011 additive 확장: stage baseline SSOT 테이블을 적용한다."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_stage_baseline (
            singleton_key TEXT PRIMARY KEY,
            l4_admission_rate_baseline_p50 REAL NULL,
            l4_admission_rate_baseline_samples INTEGER NOT NULL DEFAULT 0,
            p95_pending_available_age_baseline_sec REAL NULL,
            p95_pending_available_age_baseline_samples INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    cols = _table_columns(conn, "pipeline_stage_baseline")
    if "p95_pending_available_age_baseline_sec" not in cols:
        conn.execute("ALTER TABLE pipeline_stage_baseline ADD COLUMN p95_pending_available_age_baseline_sec REAL NULL")
    if "p95_pending_available_age_baseline_samples" not in cols:
        conn.execute(
            "ALTER TABLE pipeline_stage_baseline ADD COLUMN p95_pending_available_age_baseline_samples INTEGER NOT NULL DEFAULT 0"
        )


def _fallback_upgrade_0012(conn: sqlite3.Connection) -> None:
    """0012 additive 확장: scope_repo_root 컬럼/인덱스를 적용한다."""
    target_tables: tuple[str, ...] = (
        "collected_files_l1",
        "file_enrich_queue",
        "candidate_index_changes",
        "collected_file_bodies_l2",
        "lsp_symbols",
        "lsp_call_relations",
        "tool_data_l3_symbols",
        "tool_data_l4_normalized_symbols",
        "tool_data_l5_semantics",
        "tool_readiness_state",
        "pipeline_error_events",
        "pipeline_perf_runs",
        "pipeline_quality_runs",
        "pipeline_lsp_matrix_runs",
        "file_embeddings",
        "snippet_entries",
        "knowledge_entries",
        "symbol_importance_cache",
    )
    for table_name in target_tables:
        if not _table_exists(conn, table_name):
            continue
        cols = _table_columns(conn, table_name)
        if "scope_repo_root" not in cols:
            if table_name == "pipeline_error_events":
                # GLOBAL 이벤트(repo_root=NULL)와 런타임 insert(repo_root=None)를 허용해야 하므로 nullable 유지.
                conn.execute("ALTER TABLE pipeline_error_events ADD COLUMN scope_repo_root TEXT NULL")
            else:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN scope_repo_root TEXT NOT NULL DEFAULT ''")
            cols = _table_columns(conn, table_name)
        if "repo_root" in cols:
            if table_name == "pipeline_error_events":
                conn.execute(
                    """
                    UPDATE pipeline_error_events
                    SET scope_repo_root = repo_root
                    WHERE scope_repo_root IS NULL OR TRIM(scope_repo_root) = ''
                    """
                )
            else:
                conn.execute(
                    f"""
                    UPDATE {table_name}
                    SET scope_repo_root = COALESCE(repo_root, '')
                    WHERE scope_repo_root IS NULL OR TRIM(scope_repo_root) = ''
                    """
                )
    index_specs: tuple[tuple[str, tuple[str, ...], str], ...] = (
        (
            "collected_files_l1",
            ("scope_repo_root", "is_deleted"),
            """
            CREATE INDEX IF NOT EXISTS idx_collected_files_l1_scope_repo
            ON collected_files_l1(scope_repo_root, is_deleted)
            """,
        ),
        (
            "file_enrich_queue",
            ("scope_repo_root", "status", "next_retry_at"),
            """
            CREATE INDEX IF NOT EXISTS idx_file_enrich_queue_scope_sched
            ON file_enrich_queue(scope_repo_root, status, next_retry_at)
            """,
        ),
        (
            "candidate_index_changes",
            ("scope_repo_root", "relative_path", "status"),
            """
            CREATE INDEX IF NOT EXISTS idx_candidate_index_changes_scope_path_status
            ON candidate_index_changes(scope_repo_root, relative_path, status)
            """,
        ),
        (
            "tool_data_l3_symbols",
            ("workspace_id", "scope_repo_root", "relative_path", "updated_at"),
            """
            CREATE INDEX IF NOT EXISTS idx_tool_data_l3_scope_lookup
            ON tool_data_l3_symbols(workspace_id, scope_repo_root, relative_path, updated_at DESC)
            """,
        ),
        (
            "tool_data_l4_normalized_symbols",
            ("workspace_id", "scope_repo_root", "relative_path", "updated_at"),
            """
            CREATE INDEX IF NOT EXISTS idx_tool_data_l4_scope_lookup
            ON tool_data_l4_normalized_symbols(workspace_id, scope_repo_root, relative_path, updated_at DESC)
            """,
        ),
        (
            "tool_data_l5_semantics",
            ("workspace_id", "scope_repo_root", "relative_path", "updated_at"),
            """
            CREATE INDEX IF NOT EXISTS idx_tool_data_l5_scope_lookup
            ON tool_data_l5_semantics(workspace_id, scope_repo_root, relative_path, updated_at DESC)
            """,
        ),
        (
            "pipeline_error_events",
            ("scope_repo_root", "relative_path"),
            """
            CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_scope_path
            ON pipeline_error_events(scope_repo_root, relative_path)
            """,
        ),
        (
            "symbol_importance_cache",
            ("scope_repo_root", "reference_count"),
            """
            CREATE INDEX IF NOT EXISTS idx_symbol_importance_scope_count
            ON symbol_importance_cache(scope_repo_root, reference_count DESC)
            """,
        ),
    )
    for table_name, required_cols, index_sql in index_specs:
        if not _table_exists(conn, table_name):
            continue
        cols = _table_columns(conn, table_name)
        if not set(required_cols).issubset(cols):
            continue
        conn.execute(index_sql)


def _fallback_upgrade_0013(conn: sqlite3.Connection) -> None:
    """0013 tool_data_l4_normalized_symbols에서 needs_l5 컬럼을 제거한다."""
    if not _table_exists(conn, "tool_data_l4_normalized_symbols"):
        return
    cols = _table_columns(conn, "tool_data_l4_normalized_symbols")
    if "needs_l5" in cols:
        conn.execute("ALTER TABLE tool_data_l4_normalized_symbols DROP COLUMN needs_l5")
