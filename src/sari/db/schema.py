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
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
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

CREATE TABLE IF NOT EXISTS file_enrich_queue (
    job_id TEXT PRIMARY KEY,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_raw TEXT NOT NULL DEFAULT '',
    content_encoding TEXT NOT NULL DEFAULT 'utf-8',
    priority INTEGER NOT NULL DEFAULT 30,
    enqueue_source TEXT NOT NULL DEFAULT 'scan',
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_error TEXT NULL,
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

CREATE TABLE IF NOT EXISTS candidate_index_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_type TEXT NOT NULL,
    status TEXT NOT NULL,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
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

CREATE TABLE IF NOT EXISTS collected_file_bodies_l2 (
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
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
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
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
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    from_symbol TEXT NOT NULL,
    to_symbol TEXT NOT NULL,
    line INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, relative_path, content_hash, from_symbol, to_symbol, line)
);

CREATE TABLE IF NOT EXISTS symbol_importance_cache (
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    symbol_name TEXT NOT NULL,
    reference_count INTEGER NOT NULL,
    revision_epoch INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(repo_root, symbol_name)
);

CREATE INDEX IF NOT EXISTS idx_symbol_importance_repo_count
ON symbol_importance_cache(repo_root, reference_count DESC);

CREATE TABLE IF NOT EXISTS tool_readiness_state (
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
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

CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_code
ON pipeline_error_events(error_code);

CREATE INDEX IF NOT EXISTS idx_pipeline_error_events_scope_time
ON pipeline_error_events(scope_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_benchmark_runs (
    run_id TEXT PRIMARY KEY,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
    target_files INTEGER NOT NULL,
    profile TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NULL,
    status TEXT NOT NULL,
    summary_json TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_benchmark_runs_started
ON pipeline_benchmark_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_quality_runs (
    run_id TEXT PRIMARY KEY,
    repo_root TEXT NOT NULL CHECK (repo_root <> ''),
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


def init_schema(db_path: Path) -> None:
    """필수 테이블을 생성한다."""
    ensure_parent_dir(db_path)
    with connect(db_path) as conn:
        # WAL은 초기화(쓰기 가능) 단계에서만 고정한다.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    # 스키마 마이그레이션의 단일 진실 소스는 Alembic head다.
    ensure_migrated(db_path)
