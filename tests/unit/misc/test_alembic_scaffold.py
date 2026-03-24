"""Alembic 스캐폴드 존재를 검증한다."""

from pathlib import Path

from sari.db.migration import HEAD_VERSION, _fallback_upgrade_0003, ensure_migrated
from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.schema import init_schema, connect


def test_alembic_scaffold_files_exist() -> None:
    """마이그레이션 기본 파일이 저장소에 존재해야 한다."""
    project_root = Path(__file__).resolve().parents[3]

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
        relation_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'lsp_call_relations'"
        ).fetchone()
        relation_cols = (
            {str(row["name"]) for row in conn.execute("PRAGMA table_info(lsp_call_relations)").fetchall()}
            if relation_row is not None
            else set()
        )
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
    assert "lease_token" in runtime_cols
    assert "owner_generation" in runtime_cols
    assert "updated_at" in runtime_cols
    assert "lease_expires_at" in runtime_cols
    assert "priority" in queue_cols
    assert "bootstrap_mode_enabled" in policy_cols
    assert "watcher_queue_max" in policy_cols
    assert "watcher_overflow_rescan_cooldown_sec" in policy_cols
    assert "required_languages_json" in matrix_cols
    assert "enabled" in probe_cols
    assert "symbol_key" in symbol_cols
    if relation_row is not None:
        assert "from_symbol_key" in relation_cols
        assert "to_symbol_key" in relation_cols
    assert "repo_id" in queue_cols
    assert "repo_id" in symbol_cols
    assert registry_row is not None
    assert l3_tool_row is not None
    assert l4_tool_row is not None
    assert l5_tool_row is not None


def test_ensure_migrated_backfills_relation_symbol_keys_from_symbols(tmp_path: Path) -> None:
    """기존 relation row는 migration 후 symbol_key가 가능한 범위에서 채워져야 한다."""
    db_path = tmp_path / "legacy-rel.db"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS collected_files_l1 (
                repo_root TEXT NOT NULL,
                scope_repo_root TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS lsp_symbols (
                repo_root TEXT NOT NULL,
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
            CREATE TABLE IF NOT EXISTS lsp_call_relations (
                repo_root TEXT NOT NULL,
                scope_repo_root TEXT NOT NULL DEFAULT '',
                relative_path TEXT NOT NULL,
                caller_relative_path TEXT NULL,
                content_hash TEXT NOT NULL,
                from_symbol TEXT NOT NULL,
                to_symbol TEXT NOT NULL,
                line INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(repo_root, relative_path, content_hash, from_symbol, to_symbol, line)
            );
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            );
            DELETE FROM alembic_version;
            INSERT INTO alembic_version(version_num) VALUES('20260222_0008');
            INSERT INTO lsp_symbols(
                repo_root, scope_repo_root, relative_path, content_hash, name, kind, line, end_line,
                symbol_key, parent_symbol_key, depth, container_name, created_at
            ) VALUES(
                '/repo', '/repo', 'src/main.py', 'h1', 'AuthController.login', 'Function', 3, 8,
                'py:/repo/src/main.py#AuthController.login', NULL, 0, NULL, '2026-03-24T00:00:00+00:00'
            );
            INSERT INTO lsp_symbols(
                repo_root, scope_repo_root, relative_path, content_hash, name, kind, line, end_line,
                symbol_key, parent_symbol_key, depth, container_name, created_at
            ) VALUES(
                '/repo', '/repo', 'src/main.py', 'h1', 'AuthService.login', 'Function', 12, 20,
                'py:/repo/src/main.py#AuthService.login', NULL, 0, NULL, '2026-03-24T00:00:00+00:00'
            );
            INSERT INTO lsp_call_relations(
                repo_root, scope_repo_root, relative_path, caller_relative_path, content_hash,
                from_symbol, to_symbol, line, created_at
            ) VALUES(
                '/repo', '/repo', 'src/main.py', 'src/main.py', 'h1',
                'AuthController.login', 'AuthService.login', 13, '2026-03-24T00:00:00+00:00'
            );
            """
        )
        conn.commit()

    ensure_migrated(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT from_symbol_key, to_symbol_key
            FROM lsp_call_relations
            WHERE repo_root='/repo' AND relative_path='src/main.py'
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert str(row["from_symbol_key"]) == "py:/repo/src/main.py#AuthController.login"
    assert str(row["to_symbol_key"]) == "py:/repo/src/main.py#AuthService.login"


def test_ensure_migrated_backfills_from_symbol_key_for_cross_file_relations(tmp_path: Path) -> None:
    """caller/callee 파일 hash가 달라도 caller path 기준으로 from_symbol_key를 채워야 한다."""
    db_path = tmp_path / "legacy-rel-cross-file.db"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS collected_files_l1 (
                repo_root TEXT NOT NULL,
                scope_repo_root TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS lsp_symbols (
                repo_root TEXT NOT NULL,
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
            CREATE TABLE IF NOT EXISTS lsp_call_relations (
                repo_root TEXT NOT NULL,
                scope_repo_root TEXT NOT NULL DEFAULT '',
                relative_path TEXT NOT NULL,
                caller_relative_path TEXT NULL,
                content_hash TEXT NOT NULL,
                from_symbol TEXT NOT NULL,
                to_symbol TEXT NOT NULL,
                line INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(repo_root, relative_path, content_hash, from_symbol, to_symbol, line)
            );
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            );
            DELETE FROM alembic_version;
            INSERT INTO alembic_version(version_num) VALUES('20260222_0008');
            INSERT INTO lsp_symbols(
                repo_root, scope_repo_root, relative_path, content_hash, name, kind, line, end_line,
                symbol_key, parent_symbol_key, depth, container_name, created_at
            ) VALUES(
                '/repo', '/repo', 'src/controller.py', 'h-controller', 'Controller.run', 'Function', 10, 20,
                'py:/repo/src/controller.py#Controller.run', NULL, 0, NULL, '2026-03-24T00:00:00+00:00'
            );
            INSERT INTO lsp_symbols(
                repo_root, scope_repo_root, relative_path, content_hash, name, kind, line, end_line,
                symbol_key, parent_symbol_key, depth, container_name, created_at
            ) VALUES(
                '/repo', '/repo', 'src/service.py', 'h-service', 'Service.exec', 'Function', 30, 40,
                'py:/repo/src/service.py#Service.exec', NULL, 0, NULL, '2026-03-24T00:00:00+00:00'
            );
            INSERT INTO lsp_call_relations(
                repo_root, scope_repo_root, relative_path, caller_relative_path, content_hash,
                from_symbol, to_symbol, line, created_at
            ) VALUES(
                '/repo', '/repo', 'src/service.py', 'src/controller.py', 'h-service',
                'Controller.run', 'Service.exec', 33, '2026-03-24T00:00:00+00:00'
            );
            """
        )
        conn.commit()

    ensure_migrated(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT from_symbol_key, to_symbol_key
            FROM lsp_call_relations
            WHERE repo_root='/repo' AND relative_path='src/service.py'
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert str(row["from_symbol_key"]) == "py:/repo/src/controller.py#Controller.run"
    assert str(row["to_symbol_key"]) == "py:/repo/src/service.py#Service.exec"


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


def test_ensure_migrated_handles_partial_legacy_schema_without_optional_tables(tmp_path: Path) -> None:
    """부분 legacy 스키마(일부 optional 테이블 누락)에서도 migration이 중단되지 않아야 한다."""
    db_path = tmp_path / "partial-legacy.db"
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
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            );
            DELETE FROM alembic_version;
            INSERT INTO alembic_version(version_num) VALUES('20260222_0008');
            """
        )
        conn.commit()

    # optional 테이블(pipeline_error_events 등)이 없어도 예외 없이 완료되어야 한다.
    ensure_migrated(db_path)

    with connect(db_path) as conn:
        version_row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
        indexes = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='collected_files_l1'"
            ).fetchall()
        }
    assert version_row is not None
    assert str(version_row["version_num"]) == HEAD_VERSION
    assert "idx_collected_files_l1_scope_repo" in indexes


def test_fallback_upgrade_0003_creates_repo_probe_table_when_missing(tmp_path: Path) -> None:
    """0003 fallback은 누락된 repo probe 테이블이 있어도 실패하지 않아야 한다."""
    db_path = tmp_path / "legacy-0003.db"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE pipeline_lsp_matrix_runs(id INTEGER);
            CREATE TABLE language_probe_status(language TEXT);
            CREATE TABLE lsp_symbols(
                repo_root TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                line INTEGER NOT NULL
            );
            """
        )
        conn.commit()

    with connect(db_path) as conn:
        _fallback_upgrade_0003(conn)
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(repo_language_probe_state)").fetchall()
        }

    assert "status" in columns
    assert "updated_at" in columns


def test_ensure_migrated_additively_creates_repo_probe_table_for_head_db(tmp_path: Path) -> None:
    """head 버전 DB라도 additive repo probe 테이블은 ensure_migrated에서 보장되어야 한다."""
    db_path = tmp_path / "head-missing-repo-probe.db"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            );
            DELETE FROM alembic_version;
            INSERT INTO alembic_version(version_num) VALUES('20260222_0008');
            """
        )
        conn.commit()

    ensure_migrated(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='repo_language_probe_state'"
        ).fetchone()

    assert row is not None


def test_ensure_migrated_handles_pipeline_error_events_null_repo_root(tmp_path: Path) -> None:
    """pipeline_error_events.repo_root=NULL 레거시 행이 있어도 0012 백필이 실패하지 않아야 한다."""
    db_path = tmp_path / "legacy-null-repo-root.db"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pipeline_error_events (
                event_id TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                component TEXT NOT NULL,
                phase TEXT NOT NULL,
                severity TEXT NOT NULL,
                scope_type TEXT NOT NULL DEFAULT 'GLOBAL',
                repo_root TEXT NULL,
                relative_path TEXT NULL,
                job_id TEXT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL DEFAULT '',
                stacktrace_text TEXT NOT NULL DEFAULT '',
                context_json TEXT NOT NULL DEFAULT '{}',
                worker_name TEXT NOT NULL DEFAULT '',
                run_mode TEXT NOT NULL DEFAULT 'prod',
                resolved INTEGER NOT NULL DEFAULT 0,
                resolved_at TEXT NULL
            );
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            );
            DELETE FROM alembic_version;
            INSERT INTO alembic_version(version_num) VALUES('20260222_0008');
            INSERT INTO pipeline_error_events(
                event_id, occurred_at, component, phase, severity, scope_type,
                repo_root, relative_path, job_id, attempt_count,
                error_code, error_message, error_type, stacktrace_text, context_json,
                worker_name, run_mode, resolved, resolved_at
            )
            VALUES(
                'e1', '2026-02-25T00:00:00+00:00', 'migration', 'seed', 'error', 'GLOBAL',
                NULL, NULL, NULL, 0,
                'ERR_SEED', 'seed error', 'RuntimeError', '', '{}',
                'worker', 'test', 0, NULL
            );
            """
        )
        conn.commit()

    ensure_migrated(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT scope_repo_root
            FROM pipeline_error_events
            WHERE event_id = 'e1'
            """
        ).fetchone()
        pragma_row = conn.execute(
            """
            SELECT name, type, "notnull"
            FROM pragma_table_info('pipeline_error_events')
            WHERE name = 'scope_repo_root'
            """
        ).fetchone()
    assert row is not None
    assert row["scope_repo_root"] is None
    assert pragma_row is not None
    assert int(pragma_row["notnull"]) == 0

    repo = PipelineErrorEventRepository(db_path)
    event_id = repo.record_event(
        occurred_at="2026-02-25T00:10:00+00:00",
        component="file_collection_service",
        phase="legacy_migration_check",
        severity="error",
        repo_root=None,
        relative_path=None,
        job_id=None,
        attempt_count=0,
        error_code="ERR_GLOBAL_SAMPLE",
        error_message="global error sample",
        error_type="RuntimeError",
        stacktrace_text="trace",
        context_data={},
        worker_name="worker",
        run_mode="test",
    )
    assert isinstance(event_id, str)
    assert len(event_id) > 0
