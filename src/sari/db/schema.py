"""SQLite 스키마를 관리한다."""

from pathlib import Path
import sqlite3

from sari.db.migration import ensure_migrated


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspaces (
    path TEXT PRIMARY KEY,
    name TEXT NULL,
    indexed_at TEXT NULL,
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS daemon_runtime (
    singleton_key TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    session_count INTEGER NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    last_exit_reason TEXT NULL
);

CREATE TABLE IF NOT EXISTS daemon_runtime_history (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid INTEGER NOT NULL,
    exit_reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daemon_runtime_history_time
ON daemon_runtime_history(occurred_at DESC);

CREATE TABLE IF NOT EXISTS daemon_registry (
    daemon_id TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    workspace_root TEXT NOT NULL,
    protocol TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_draining INTEGER NOT NULL CHECK (is_draining IN (0, 1)),
    deployment_state TEXT NOT NULL DEFAULT 'ACTIVE',
    health_fail_streak INTEGER NOT NULL DEFAULT 0,
    last_health_error TEXT NULL,
    last_health_at TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_daemon_registry_workspace
ON daemon_registry(workspace_root, is_draining, deployment_state, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_daemon_registry_seen
ON daemon_registry(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS repositories (
    repo_id TEXT PRIMARY KEY,
    repo_label TEXT NOT NULL,
    repo_root TEXT NOT NULL,
    workspace_root TEXT NULL,
    updated_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_repositories_workspace_label
ON repositories(workspace_root, repo_label);

CREATE INDEX IF NOT EXISTS idx_repositories_root
ON repositories(repo_root);

CREATE TABLE IF NOT EXISTS lsp_symbol_cache (
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    relative_path TEXT NOT NULL,
    query TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    items_json TEXT NOT NULL,
    invalidated INTEGER NOT NULL DEFAULT 0 CHECK (invalidated IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, relative_path, query)
);

CREATE INDEX IF NOT EXISTS idx_lsp_symbol_cache_lookup
ON lsp_symbol_cache(repo_root, query, invalidated);

CREATE TABLE IF NOT EXISTS collected_files_l1 (
    repo_id TEXT NOT NULL DEFAULT '',
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    absolute_path TEXT NOT NULL,
    repo_label TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    enrich_state TEXT NOT NULL,
    PRIMARY KEY(repo_root, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_collected_files_l1_repo
ON collected_files_l1(repo_root, is_deleted);

CREATE INDEX IF NOT EXISTS idx_collected_files_l1_label
ON collected_files_l1(repo_label, is_deleted);

CREATE INDEX IF NOT EXISTS idx_collected_files_l1_seen
ON collected_files_l1(repo_root, last_seen_at);

CREATE INDEX IF NOT EXISTS idx_collected_files_l1_scope_repo
ON collected_files_l1(scope_repo_root, is_deleted);

CREATE TABLE IF NOT EXISTS file_enrich_queue (
    job_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL DEFAULT '',
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_raw TEXT NOT NULL DEFAULT '',
    content_encoding TEXT NOT NULL DEFAULT 'utf-8',
    priority INTEGER NOT NULL DEFAULT 30,
    enqueue_source TEXT NOT NULL DEFAULT 'scan',
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_error TEXT NULL,
    defer_reason TEXT NULL,
    deferred_state TEXT NULL,
    deferred_count INTEGER NOT NULL DEFAULT 0,
    first_deferred_at TEXT NULL,
    last_deferred_at TEXT NULL,
    scope_level TEXT NULL,
    scope_root TEXT NULL,
    scope_attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_enrich_queue_status
ON file_enrich_queue(status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_file_enrich_queue_path
ON file_enrich_queue(repo_root, relative_path);

CREATE INDEX IF NOT EXISTS idx_file_enrich_queue_sched
ON file_enrich_queue(status, priority DESC, next_retry_at, created_at);

CREATE INDEX IF NOT EXISTS idx_file_enrich_queue_scope_sched
ON file_enrich_queue(scope_repo_root, status, next_retry_at);

CREATE TABLE IF NOT EXISTS candidate_index_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_type TEXT NOT NULL,
    status TEXT NOT NULL,
    repo_id TEXT NOT NULL DEFAULT '',
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    absolute_path TEXT NULL,
    content_hash TEXT NULL,
    mtime_ns INTEGER NULL,
    size_bytes INTEGER NULL,
    event_source TEXT NOT NULL,
    reason TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_index_changes_status
ON candidate_index_changes(status, change_id);

CREATE INDEX IF NOT EXISTS idx_candidate_index_changes_path_status
ON candidate_index_changes(repo_root, relative_path, status);

CREATE INDEX IF NOT EXISTS idx_candidate_index_changes_scope_path_status
ON candidate_index_changes(scope_repo_root, relative_path, status);

CREATE TABLE IF NOT EXISTS collected_file_bodies_l2 (
    repo_id TEXT NOT NULL DEFAULT '',
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_zlib BLOB NOT NULL,
    content_len INTEGER NOT NULL,
    normalized_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, relative_path, content_hash)
);

CREATE TABLE IF NOT EXISTS lsp_symbols (
    repo_id TEXT NOT NULL DEFAULT '',
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    symbol_key TEXT NULL,
    parent_symbol_key TEXT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    container_name TEXT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, relative_path, content_hash, name, kind, line, end_line)
);

CREATE INDEX IF NOT EXISTS idx_lsp_symbols_repo_path_depth
ON lsp_symbols(repo_root, relative_path, depth, line);

CREATE INDEX IF NOT EXISTS idx_lsp_symbols_symbol_key
ON lsp_symbols(repo_root, relative_path, content_hash, symbol_key);

CREATE TABLE IF NOT EXISTS lsp_call_relations (
    repo_id TEXT NOT NULL DEFAULT '',
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    from_symbol TEXT NOT NULL,
    to_symbol TEXT NOT NULL,
    line INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, relative_path, content_hash, from_symbol, to_symbol, line)
);

CREATE TABLE IF NOT EXISTS tool_data_l3_symbols (
    workspace_id TEXT NOT NULL,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    symbols_json TEXT NOT NULL,
    degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0, 1)),
    l3_skipped_large_file INTEGER NOT NULL DEFAULT 0 CHECK (l3_skipped_large_file IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, repo_root, relative_path, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_tool_data_l3_lookup
ON tool_data_l3_symbols(workspace_id, repo_root, relative_path, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_tool_data_l3_scope_lookup
ON tool_data_l3_symbols(workspace_id, scope_repo_root, relative_path, updated_at DESC);

CREATE TABLE IF NOT EXISTS tool_data_l4_normalized_symbols (
    workspace_id TEXT NOT NULL,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    ambiguity REAL NOT NULL,
    coverage REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, repo_root, relative_path, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_tool_data_l4_lookup
ON tool_data_l4_normalized_symbols(workspace_id, repo_root, relative_path, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_tool_data_l4_scope_lookup
ON tool_data_l4_normalized_symbols(workspace_id, scope_repo_root, relative_path, updated_at DESC);

CREATE TABLE IF NOT EXISTS tool_data_l5_semantics (
    workspace_id TEXT NOT NULL,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    semantics_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, repo_root, relative_path, content_hash, reason_code)
);

CREATE INDEX IF NOT EXISTS idx_tool_data_l5_lookup
ON tool_data_l5_semantics(workspace_id, repo_root, relative_path, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_tool_data_l5_scope_lookup
ON tool_data_l5_semantics(workspace_id, scope_repo_root, relative_path, updated_at DESC);

CREATE TABLE IF NOT EXISTS symbol_importance_cache (
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    symbol_name TEXT NOT NULL,
    reference_count INTEGER NOT NULL,
    revision_epoch INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, symbol_name)
);

CREATE INDEX IF NOT EXISTS idx_symbol_importance_repo_count
ON symbol_importance_cache(repo_root, reference_count DESC);

CREATE INDEX IF NOT EXISTS idx_symbol_importance_scope_count
ON symbol_importance_cache(scope_repo_root, reference_count DESC);

CREATE TABLE IF NOT EXISTS tool_readiness_state (
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    list_files_ready INTEGER NOT NULL CHECK (list_files_ready IN (0, 1)),
    read_file_ready INTEGER NOT NULL CHECK (read_file_ready IN (0, 1)),
    search_symbol_ready INTEGER NOT NULL CHECK (search_symbol_ready IN (0, 1)),
    get_callers_ready INTEGER NOT NULL CHECK (get_callers_ready IN (0, 1)),
    consistency_ready INTEGER NOT NULL CHECK (consistency_ready IN (0, 1)),
    quality_ready INTEGER NOT NULL CHECK (quality_ready IN (0, 1)),
    tool_ready INTEGER NOT NULL CHECK (tool_ready IN (0, 1)),
    last_reason TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, relative_path)
);

CREATE TABLE IF NOT EXISTS pipeline_policy (
    singleton_key TEXT PRIMARY KEY,
    deletion_hold INTEGER NOT NULL CHECK (deletion_hold IN (0, 1)),
    l3_p95_threshold_ms INTEGER NOT NULL,
    dead_ratio_threshold_bps INTEGER NOT NULL,
    enrich_worker_count INTEGER NOT NULL,
    watcher_queue_max INTEGER NOT NULL DEFAULT 10000,
    watcher_overflow_rescan_cooldown_sec INTEGER NOT NULL DEFAULT 30,
    bootstrap_mode_enabled INTEGER NOT NULL DEFAULT 0 CHECK (bootstrap_mode_enabled IN (0, 1)),
    bootstrap_l3_worker_count INTEGER NOT NULL DEFAULT 1,
    bootstrap_l3_queue_max INTEGER NOT NULL DEFAULT 1000,
    bootstrap_exit_min_l2_coverage_bps INTEGER NOT NULL DEFAULT 9500,
    bootstrap_exit_max_sec INTEGER NOT NULL DEFAULT 1800,
    alert_window_sec INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_control_state (
    singleton_key TEXT PRIMARY KEY,
    auto_hold_enabled INTEGER NOT NULL CHECK (auto_hold_enabled IN (0, 1)),
    auto_hold_active INTEGER NOT NULL CHECK (auto_hold_active IN (0, 1)),
    last_action TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_stage_baseline (
    singleton_key TEXT PRIMARY KEY,
    l4_admission_rate_baseline_p50 REAL NULL,
    l4_admission_rate_baseline_samples INTEGER NOT NULL DEFAULT 0,
    p95_pending_available_age_baseline_sec REAL NULL,
    p95_pending_available_age_baseline_samples INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_job_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_job_events_time
ON pipeline_job_events(created_at);

CREATE INDEX IF NOT EXISTS idx_pipeline_job_events_status
ON pipeline_job_events(created_at, status);

CREATE TABLE IF NOT EXISTS pipeline_error_events (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    component TEXT NOT NULL,
    phase TEXT NOT NULL,
    severity TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT 'GLOBAL' CHECK (scope_type IN ('GLOBAL', 'REPO')),
    repo_root TEXT NULL,
    scope_repo_root TEXT NULL,
    relative_path TEXT NULL,
    job_id TEXT NULL,
    attempt_count INTEGER NOT NULL,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    error_type TEXT NOT NULL,
    stacktrace_text TEXT NOT NULL,
    context_json TEXT NOT NULL,
    worker_name TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    resolved INTEGER NOT NULL CHECK (resolved IN (0, 1)),
    resolved_at TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_time
ON pipeline_error_events(occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_component
ON pipeline_error_events(component, phase);

CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_repo_path
ON pipeline_error_events(repo_root, relative_path);

CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_scope_path
ON pipeline_error_events(scope_repo_root, relative_path);

CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_code
ON pipeline_error_events(error_code);

CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_scope_time
ON pipeline_error_events(scope_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_perf_runs (
    run_id TEXT PRIMARY KEY,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    target_files INTEGER NOT NULL,
    profile TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NULL,
    status TEXT NOT NULL,
    summary_json TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_perf_runs_started
ON pipeline_perf_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_quality_runs (
    run_id TEXT PRIMARY KEY,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    limit_files INTEGER NOT NULL,
    profile TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NULL,
    status TEXT NOT NULL,
    summary_json TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_quality_runs_started
ON pipeline_quality_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_lsp_matrix_runs (
    run_id TEXT PRIMARY KEY,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    required_languages_json TEXT NOT NULL,
    fail_on_unavailable INTEGER NOT NULL CHECK (fail_on_unavailable IN (0, 1)),
    strict_symbol_gate INTEGER NOT NULL CHECK (strict_symbol_gate IN (0, 1)) DEFAULT 1,
    started_at TEXT NOT NULL,
    finished_at TEXT NULL,
    status TEXT NOT NULL,
    summary_json TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_lsp_matrix_runs_started
ON pipeline_lsp_matrix_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS language_probe_status (
    language TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    last_probe_at TEXT NULL,
    last_error_code TEXT NULL,
    last_error_message TEXT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_language_probe_status_available
ON language_probe_status(available, updated_at DESC);

CREATE TABLE IF NOT EXISTS file_embeddings (
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, relative_path, content_hash, model_id)
);

CREATE INDEX IF NOT EXISTS idx_file_embeddings_lookup
ON file_embeddings(repo_root, relative_path, model_id);

CREATE TABLE IF NOT EXISTS query_embeddings (
    query_hash TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(query_hash, model_id)
);

CREATE TABLE IF NOT EXISTS snippet_entries (
    snippet_id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    tag TEXT NOT NULL,
    note TEXT NULL,
    commit_hash TEXT NULL,
    content_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snippet_entries_repo_tag
ON snippet_entries(repo_root, tag, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_snippet_entries_repo_path
ON snippet_entries(repo_root, source_path, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    scope_repo_root TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL,
    content_text TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    related_files_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_entries_repo_kind
ON knowledge_entries(repo_root, kind, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_entries_repo_topic
ON knowledge_entries(repo_root, topic, created_at DESC);
"""

def ensure_parent_dir(db_path: Path) -> None:
    """DB 상위 디렉터리를 보장한다."""
    db_path.parent.mkdir(parents=True, exist_ok=True)


def connect(db_path: Path) -> sqlite3.Connection:
    """SQLite 연결을 생성하고 로우 팩토리를 설정한다."""
    conn = sqlite3.connect(str(db_path))
    # busy timeout/synchronous는 연결 단위 정책으로 항상 적용한다.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """테이블 존재 여부를 반환한다."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = :name",
        {"name": table_name},
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """테이블 컬럼 이름 집합을 조회한다."""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _create_repo_id_indexes(conn: sqlite3.Connection) -> None:
    """repo_id 기반 인덱스를 컬럼 존재 시에만 생성한다."""
    if _table_exists(conn, "collected_files_l1"):
        l1_cols = _table_columns(conn, "collected_files_l1")
        if {"repo_id", "is_deleted"}.issubset(l1_cols):
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
        change_cols = _table_columns(conn, "candidate_index_changes")
        if {"repo_id", "status", "change_id"}.issubset(change_cols):
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_candidate_index_changes_repo_id_status
                ON candidate_index_changes(repo_id, status, change_id)
                """
            )


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    """sqlite 내부 테이블을 제외한 사용자 테이블 존재 여부를 반환한다."""
    row = conn.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchone()
    if row is None:
        return False
    return int(row["cnt"]) > 0


def init_schema(db_path: Path) -> None:
    """필수 테이블을 생성한다."""
    ensure_parent_dir(db_path)
    has_tables = False
    with connect(db_path) as conn:
        has_tables = _has_user_tables(conn)
    if has_tables:
        # 기존 DB는 먼저 마이그레이션해 컬럼 호환성을 맞춘다.
        ensure_migrated(db_path)
    with connect(db_path) as conn:
        # WAL은 초기화(쓰기 가능) 단계에서만 고정한다.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    # 신규 DB는 baseline 생성 후 head까지 마이그레이션한다.
    if not has_tables:
        ensure_migrated(db_path)
    with connect(db_path) as conn:
        _create_repo_id_indexes(conn)
        conn.commit()
