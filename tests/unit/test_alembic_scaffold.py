"""Alembic 스캐폴드 존재를 검증한다."""

from pathlib import Path

from sari.db.migration import HEAD_VERSION, ensure_migrated
from sari.db.schema import init_schema, connect


def test_alembic_scaffold_files_exist() -> None:
    """마이그레이션 기본 파일이 저장소에 존재해야 한다."""
    project_root = Path(__file__).resolve().parents[2]

    assert (project_root / "alembic.ini").exists()
    assert (project_root / "alembic" / "env.py").exists()
    assert (project_root / "alembic" / "versions").exists()


def test_init_schema_stamps_alembic_version(tmp_path: Path) -> None:
    """스키마 초기화 시 alembic baseline 리비전이 기록되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    with connect(db_path) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()

    assert row is not None
    assert str(row["version_num"]) == HEAD_VERSION


def test_ensure_migrated_runs_without_error(tmp_path: Path, monkeypatch) -> None:
    """Alembic 자동 마이그레이션 훅은 기본 스키마 DB에서 오류 없이 실행되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    monkeypatch.delenv("SARI_DISABLE_ALEMBIC_AUTO", raising=False)

    ensure_migrated(db_path)


def test_ensure_migrated_upgrades_baseline_columns(tmp_path: Path) -> None:
    """baseline 스키마 DB는 Alembic head 업그레이드로 확장 컬럼이 추가되어야 한다."""
    db_path = tmp_path / "legacy.db"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daemon_runtime (
                singleton_key TEXT PRIMARY KEY,
                pid INTEGER NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                state TEXT NOT NULL,
                started_at TEXT NOT NULL,
                session_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daemon_registry (
                daemon_id TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                workspace_root TEXT NOT NULL,
                protocol TEXT NOT NULL,
                started_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_draining INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_enrich_queue (
                job_id TEXT PRIMARY KEY,
                repo_root TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pipeline_policy (
                singleton_key TEXT PRIMARY KEY,
                deletion_hold INTEGER NOT NULL,
                enrich_worker_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pipeline_error_events (
                event_id TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                repo_root TEXT NULL
            );
            CREATE TABLE IF NOT EXISTS pipeline_lsp_matrix_runs (
                run_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS language_probe_status (
                language TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS lsp_symbols (
                repo_root TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                line INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            );
            DELETE FROM alembic_version;
            INSERT INTO alembic_version(version_num) VALUES('20260216_0001');
            """
        )
        conn.commit()

    ensure_migrated(db_path)

    with connect(db_path) as conn:
        version_row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
        runtime_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(daemon_runtime)").fetchall()}
        queue_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(file_enrich_queue)").fetchall()}
        policy_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(pipeline_policy)").fetchall()}
        matrix_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(pipeline_lsp_matrix_runs)").fetchall()}
        probe_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(language_probe_status)").fetchall()}
        symbol_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(lsp_symbols)").fetchall()}
        registry_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'repositories'"
        ).fetchone()
        l3_tool_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tool_data_l3_symbols'"
        ).fetchone()
        l4_tool_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tool_data_l4_normalized_symbols'"
        ).fetchone()
        l5_tool_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tool_data_l5_semantics'"
        ).fetchone()

    assert version_row is not None
    assert str(version_row["version_num"]) == HEAD_VERSION
    assert "last_heartbeat_at" in runtime_cols
    assert "priority" in queue_cols
    assert "bootstrap_mode_enabled" in policy_cols
    assert "watcher_queue_max" in policy_cols
    assert "watcher_overflow_rescan_cooldown_sec" in policy_cols
    assert "required_languages_json" in matrix_cols
    assert "enabled" in probe_cols
    assert "symbol_key" in symbol_cols
    assert "repo_id" in queue_cols
    assert "repo_id" in symbol_cols
    assert registry_row is not None
    assert l3_tool_row is not None
    assert l4_tool_row is not None
    assert l5_tool_row is not None


def test_init_schema_handles_legacy_db_missing_repo_id_columns(tmp_path: Path) -> None:
    """legacy DB에서도 init_schema가 repo_id 인덱스 오류 없이 완료되어야 한다."""
    db_path = tmp_path / "legacy-init.db"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS collected_files_l1 (
                repo_root TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                repo_label TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                enrich_state TEXT NOT NULL,
                PRIMARY KEY(repo_root, relative_path)
            );
            CREATE TABLE IF NOT EXISTS file_enrich_queue (
                job_id TEXT PRIMARY KEY,
                repo_root TEXT NOT NULL,
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
            CREATE TABLE IF NOT EXISTS candidate_index_changes (
                change_id INTEGER PRIMARY KEY AUTOINCREMENT,
                change_type TEXT NOT NULL,
                status TEXT NOT NULL,
                repo_root TEXT NOT NULL,
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
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            );
            DELETE FROM alembic_version;
            INSERT INTO alembic_version(version_num) VALUES('20260218_0005');
            """
        )
        conn.commit()

    init_schema(db_path)

    with connect(db_path) as conn:
        l1_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(collected_files_l1)").fetchall()}
        queue_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(file_enrich_queue)").fetchall()}
        version_row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()

    assert "repo_id" in l1_cols
    assert "repo_id" in queue_cols
    assert version_row is not None
    assert str(version_row["version_num"]) == HEAD_VERSION
