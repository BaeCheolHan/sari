import pytest
import logging
import json

from sari.core.indexer.main import (
    _apply_incremental_low_impact_caps,
    Indexer,
    _adaptive_flush_threshold,
    _effective_inflight_limit,
    _scan_to_db,
    _update_cpu_throttle_state,
    _worker_build_snapshot,
)
from sari.core.indexer.worker import compute_hash
from sari.core.db.main import LocalSearchDB
from sari.core.config import Config
from sari.core.models import IndexingResult
from sari.core.workspace import WorkspaceManager

@pytest.fixture
def test_context(tmp_path):
    # Setup WS
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')")
    (ws / "utils.js").write_text("function add(a,b) { return a+b; }")
    
    # Setup DB
    db = LocalSearchDB(str(tmp_path / "sari.db"))
    
    # Setup Config
    cfg = Config(**Config.get_defaults(str(ws)))
    
    return {"ws": ws, "db": db, "cfg": cfg}

def test_indexer_end_to_end_flow(test_context):
    """
    Verify the real modernization: Scan -> Parallel Process -> Turbo DB -> Read.
    """
    db, cfg, ws = test_context["db"], test_context["cfg"], test_context["ws"]
    indexer = Indexer(cfg, db)
    
    # Execute actual high-speed scan
    indexer.scan_once()
    
    # Verify DB content (Real verification of the new architecture)
    assert indexer.status.indexed_files >= 2
    assert indexer.status.index_ready is True
    
    # Verify content retrieval (Testing the intelligent read_file)
    content = db.read_file(str(ws / "main.py"))
    assert "print('hello')" in content

def test_indexer_lifecycle_cleanup(test_context):
    """
    Ensure the process pool is actually terminated on stop.
    """
    db, cfg = test_context["db"], test_context["cfg"]
    indexer = Indexer(cfg, db)
    assert indexer._executor is not None
    indexer.stop()
    assert indexer._executor is None


def test_scan_to_db_raises_when_parent_dead(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))

    with pytest.raises(RuntimeError, match="orphaned worker detected"):
        _scan_to_db(
            cfg,
            db,
            logging.getLogger("test"),
            parent_pid=999999,
            parent_alive_check=lambda _pid: False,
        )


def test_worker_build_snapshot_writes_error_when_parent_dead(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')", encoding="utf-8")
    cfg = Config(**Config.get_defaults(str(ws)))
    snapshot = str(tmp_path / "idx.db.snapshot")
    status_path = str(tmp_path / "status.json")
    log_path = str(tmp_path / "worker.log")

    monkeypatch.setattr("sari.core.indexer.main._is_pid_alive", lambda _pid: False)
    _worker_build_snapshot(cfg.__dict__, snapshot, status_path, log_path, parent_pid=12345)

    import json
    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert "orphaned worker detected" in payload["error"]


def test_scan_to_db_emits_progress_callback(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    events = []

    _scan_to_db(cfg, db, logging.getLogger("test"), progress_callback=lambda s: events.append(dict(s)))
    assert events
    assert any(str(e.get("stage", "")) == "start" for e in events)
    assert str(events[-1].get("stage", "")) == "done"
    last = events[-1]
    assert "adaptive_flush_enabled" in last
    assert "pending_inflight" in last
    assert "max_inflight" in last
    assert isinstance(last.get("flush_thresholds"), dict)
    for key in ("file_rows", "seen_rows", "symbol_rows", "rel_rows", "rel_replace_rows"):
        assert int(last["flush_thresholds"].get(key, 0)) > 0


def test_scan_to_db_returns_perf_breakdown(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))

    status = _scan_to_db(cfg, db, logging.getLogger("test"))
    perf = status.get("perf") or {}
    assert isinstance(perf, dict)
    for key in (
        "worker_result_ms",
        "flush_files_ms",
        "flush_files_calls",
        "flush_symbols_ms",
        "flush_symbols_calls",
        "flush_relations_ms",
        "flush_relations_calls",
        "flush_seen_ms",
        "flush_seen_calls",
        "cleanup_ms",
    ):
        assert key in perf


def test_indexer_runtime_status_prefers_worker_progress(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    indexer = Indexer(cfg, db)

    status_path = tmp_path / "worker.status.json"
    status_path.write_text(
        json.dumps(
            {
                "ok": True,
                "in_progress": True,
                "status": {
                    "scanned_files": 10,
                    "indexed_files": 6,
                    "symbols_extracted": 13,
                    "errors": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    indexer._worker_status_path = str(status_path)

    class _AliveProc:
        @staticmethod
        def is_alive():
            return True

    indexer._worker_proc = _AliveProc()
    runtime = indexer.get_runtime_status()
    assert runtime["status_source"] == "worker_progress"
    assert runtime["scanned_files"] == 10
    assert runtime["indexed_files"] == 6
    assert runtime["symbols_extracted"] == 13
    assert runtime["errors"] == 1


def test_scan_to_db_flushes_in_batches(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(5):
        (ws / f"f{i}.py").write_text(f"print({i})\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_FLUSH_FILE_ROWS", "2")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_SYMBOL_ROWS", "2")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_REL_ROWS", "2")

    calls = {"files": 0, "finalize": 0}
    orig_upsert = db.upsert_files_turbo
    orig_finalize = db.finalize_turbo_batch

    def _count_upsert(rows):
        calls["files"] += 1
        return orig_upsert(rows)

    def _count_finalize():
        calls["finalize"] += 1
        return orig_finalize()

    monkeypatch.setattr(db, "upsert_files_turbo", _count_upsert)
    monkeypatch.setattr(db, "finalize_turbo_batch", _count_finalize)

    status = _scan_to_db(cfg, db, logging.getLogger("test"))
    assert int(status["indexed_files"]) >= 5
    assert calls["files"] >= 2
    assert calls["finalize"] >= calls["files"]


def test_scan_to_db_marks_excluded_legacy_rows_deleted(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    rid = cfg.workspace_roots and cfg.workspace_roots[0]
    from sari.core.workspace import WorkspaceManager
    root_id = WorkspaceManager.root_id(rid)
    db.ensure_root(root_id, str(ws))

    legacy = IndexingResult(
        path=f"{root_id}/.venv/lib/site.py",
        rel=".venv/lib/site.py",
        root_id=root_id,
        repo="repo",
        type="new",
        content="x=1",
        fts_content="x=1",
        mtime=1,
        size=3,
        content_hash="h-legacy",
        scan_ts=1,
        metadata_json="{}",
    )
    db.upsert_files_turbo([legacy.to_file_row()])
    db.finalize_turbo_batch()

    before = db.execute("SELECT deleted_ts FROM files WHERE path = ?", (legacy.path,)).fetchone()
    assert int(before[0] if not hasattr(before, "keys") else before["deleted_ts"]) == 0

    _scan_to_db(cfg, db, logging.getLogger("test"))

    after = db.execute("SELECT deleted_ts FROM files WHERE path = ?", (legacy.path,)).fetchone()
    assert after is not None
    deleted_ts = int(after[0] if not hasattr(after, "keys") else after["deleted_ts"])
    assert deleted_ts > 0


def test_scan_to_db_replaces_outgoing_relations_when_result_has_no_relations(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    file_path = ws / "main.py"
    file_path.write_text("print('x')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "0")
    root_id = WorkspaceManager.root_id(str(ws))
    db.ensure_root(root_id, str(ws))

    # Seed stale outgoing relation for this file.
    db.upsert_relations_tx(
        None,
        [
            (
                str(file_path),
                root_id,
                "main",
                "sid-main",
                str(ws / "old.py"),
                root_id,
                "old",
                "sid-old",
                "calls",
                1,
                "{}",
            )
        ],
    )

    def _no_relation_result(
        self, root, path, st, now, mtime, include_content, root_id=None, extract_symbols=True
    ):
        return IndexingResult(
            type="changed",
            path=str(path),
            rel=str(path.relative_to(root)),
            root_id=str(root_id or ""),
            repo="repo",
            mtime=int(mtime),
            size=int(getattr(st, "st_size", 0) or 0),
            content="print('x')\n",
            content_hash="h-new",
            fts_content="print('x')",
            scan_ts=int(now),
            metadata_json="{}",
            symbols=[],
            relations=[],
        )

    monkeypatch.setattr(
        "sari.core.indexer.worker.IndexWorker.process_file_task",
        _no_relation_result,
    )

    _scan_to_db(cfg, db, logging.getLogger("test"))
    row = db.execute(
        "SELECT COUNT(1) FROM symbol_relations WHERE from_path = ? AND from_root_id = ?",
        (str(file_path), root_id),
    ).fetchone()
    assert int(row[0]) == 0


def test_scan_to_db_keeps_unchanged_file_not_deleted_and_refreshes_last_seen(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    file_path = ws / "main.py"
    file_path.write_text("print('stable')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    root_id = WorkspaceManager.root_id(str(ws))
    db.ensure_root(root_id, str(ws))

    stable_content = "print('stable')\n"
    st = file_path.stat()
    stable_hash = compute_hash(stable_content)

    stale = IndexingResult(
        path=f"{root_id}/main.py",
        rel="main.py",
        root_id=root_id,
        repo="repo",
        type="changed",
        content=stable_content,
        fts_content="print stable",
        mtime=int(st.st_mtime),
        size=int(st.st_size),
        content_hash=stable_hash,
        scan_ts=1,
        metadata_json="{}",
    )
    db.upsert_files_turbo([stale.to_file_row()])
    db.finalize_turbo_batch()
    db.execute("UPDATE files SET deleted_ts = 0, last_seen_ts = 1 WHERE path = ?", (f"{root_id}/main.py",))

    _scan_to_db(cfg, db, logging.getLogger("test"))

    row = db.execute(
        "SELECT deleted_ts, last_seen_ts FROM files WHERE path = ?",
        (f"{root_id}/main.py",),
    ).fetchone()
    assert row is not None
    assert int(row[0]) == 0
    assert int(row[1]) > 1


def test_scan_to_db_flushes_relation_replace_sources_without_relations(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    files = []
    for i in range(5):
        p = ws / f"f{i}.py"
        p.write_text(f"print({i})\n", encoding="utf-8")
        files.append(p)
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "0")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_REL_ROWS", "10000")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_REL_REPLACE_ROWS", "2")

    def _no_relation_result(
        self, root, path, st, now, mtime, include_content, root_id=None, extract_symbols=True
    ):
        return IndexingResult(
            type="changed",
            path=str(path),
            rel=str(path.relative_to(root)),
            root_id=str(root_id or ""),
            repo="repo",
            mtime=int(mtime),
            size=int(getattr(st, "st_size", 0) or 0),
            content=f"print({path.stem})\n",
            content_hash=f"h-{path.stem}",
            fts_content=f"print {path.stem}",
            scan_ts=int(now),
            metadata_json="{}",
            symbols=[],
            relations=[],
        )

    monkeypatch.setattr(
        "sari.core.indexer.worker.IndexWorker.process_file_task",
        _no_relation_result,
    )

    calls = {"empty_replace": 0}
    orig_upsert_rel = db.upsert_relations_tx

    def _count_upsert_rel(cur, rows, replace_sources=None):
        if not rows and replace_sources:
            calls["empty_replace"] += 1
        return orig_upsert_rel(cur, rows, replace_sources=replace_sources)

    monkeypatch.setattr(db, "upsert_relations_tx", _count_upsert_rel)
    _scan_to_db(cfg, db, logging.getLogger("test"))

    # With threshold=2 and 5 changed files (all relation-less), replace-source
    # flush should occur incrementally, not only once at final force flush.
    assert calls["empty_replace"] >= 2


def test_scan_to_db_combines_symbol_relation_tx_by_default(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("def a():\n  return 1\n\na()\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "0")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_SYMBOL_ROWS", "1")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_REL_ROWS", "1")
    monkeypatch.setenv("SARI_INDEXER_ADAPTIVE_FLUSH", "0")

    calls = {"combined": 0, "sym": 0, "rel": 0}
    orig_combined = db.upsert_symbols_and_relations_tx

    def _combined(symbol_rows, relation_rows, replace_sources=None):
        calls["combined"] += 1
        return orig_combined(symbol_rows, relation_rows, replace_sources=replace_sources)

    def _sym(cur, rows, root_id="root"):
        calls["sym"] += 1
        return None

    def _rel(cur, rows, replace_sources=None):
        calls["rel"] += 1
        return None

    monkeypatch.setattr(db, "upsert_symbols_and_relations_tx", _combined)
    monkeypatch.setattr(db, "upsert_symbols_tx", _sym)
    monkeypatch.setattr(db, "upsert_relations_tx", _rel)
    _scan_to_db(cfg, db, logging.getLogger("test"))

    assert calls["combined"] >= 1
    assert calls["sym"] == 0


def test_scan_to_db_can_disable_combined_symbol_relation_tx(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("def a():\n  return 1\n\na()\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "0")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_SYMBOL_ROWS", "1")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_REL_ROWS", "1")
    monkeypatch.setenv("SARI_INDEXER_ADAPTIVE_FLUSH", "0")
    monkeypatch.setenv("SARI_INDEXER_COMBINED_SYMBOL_REL_TX", "0")

    calls = {"combined": 0, "sym": 0, "rel": 0}

    def _combined(symbol_rows, relation_rows, replace_sources=None):
        calls["combined"] += 1
        return None

    orig_sym = db.upsert_symbols_tx
    orig_rel = db.upsert_relations_tx

    def _sym(cur, rows, root_id="root"):
        calls["sym"] += 1
        return orig_sym(cur, rows, root_id=root_id)

    def _rel(cur, rows, replace_sources=None):
        calls["rel"] += 1
        return orig_rel(cur, rows, replace_sources=replace_sources)

    monkeypatch.setattr(db, "upsert_symbols_and_relations_tx", _combined)
    monkeypatch.setattr(db, "upsert_symbols_tx", _sym)
    monkeypatch.setattr(db, "upsert_relations_tx", _rel)
    _scan_to_db(cfg, db, logging.getLogger("test"))

    assert calls["combined"] == 0
    assert calls["sym"] >= 1
    assert calls["rel"] >= 1


def test_adaptive_flush_threshold_contract():
    assert _adaptive_flush_threshold(200, pending_count=0, max_inflight=8, enabled=False) == 200
    assert _adaptive_flush_threshold(200, pending_count=1, max_inflight=8, enabled=True) == 400
    assert _adaptive_flush_threshold(200, pending_count=7, max_inflight=8, enabled=True) == 100
    assert _adaptive_flush_threshold(200, pending_count=4, max_inflight=8, enabled=True) == 200


def test_cpu_throttle_state_hysteresis():
    assert _update_cpu_throttle_state(False, 80.0, high_watermark=70.0, resume_watermark=55.0) is True
    assert _update_cpu_throttle_state(True, 60.0, high_watermark=70.0, resume_watermark=55.0) is True
    assert _update_cpu_throttle_state(True, 50.0, high_watermark=70.0, resume_watermark=55.0) is False


def test_effective_inflight_limit_contract():
    assert _effective_inflight_limit(32, 8, throttle_active=False, throttle_workers=2) == 32
    assert _effective_inflight_limit(32, 8, throttle_active=True, throttle_workers=2) <= 8
    assert _effective_inflight_limit(4, 8, throttle_active=True, throttle_workers=1) >= 1


def test_apply_incremental_low_impact_caps_contract():
    w, i = _apply_incremental_low_impact_caps(12, 48, incremental_mode=True, enabled=True)
    assert w <= 4
    assert i <= 8
    w2, i2 = _apply_incremental_low_impact_caps(12, 48, incremental_mode=False, enabled=True)
    assert w2 == 12
    assert i2 == 48


def test_scan_to_db_flushes_files_before_symbols_to_preserve_fk(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_FLUSH_FILE_ROWS", "10000")
    monkeypatch.setenv("SARI_INDEXER_FLUSH_SYMBOL_ROWS", "1")
    monkeypatch.setenv("SARI_INDEXER_ADAPTIVE_FLUSH", "0")

    _scan_to_db(cfg, db, logging.getLogger("test"))
    row = db.execute("SELECT COUNT(1) FROM symbols").fetchone()
    assert int(row[0]) >= 1


def test_scan_to_db_fast_mode_defers_symbols_and_relations(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_PHASE_MODE", "fast")

    status = _scan_to_db(cfg, db, logging.getLogger("test"))
    files = db.execute("SELECT COUNT(1) FROM files WHERE deleted_ts = 0").fetchone()
    symbols = db.execute("SELECT COUNT(1) FROM symbols").fetchone()
    rels = db.execute("SELECT COUNT(1) FROM symbol_relations").fetchone()

    assert int(files[0]) >= 1
    assert int(symbols[0]) == 0
    assert int(rels[0]) == 0
    assert int(status.get("symbols_deferred_files", 0) or 0) >= 1


def test_scan_to_db_fast_mode_repeat_scan_does_not_duplicate_file_rows(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_PHASE_MODE", "fast")

    _scan_to_db(cfg, db, logging.getLogger("test"))
    _scan_to_db(cfg, db, logging.getLogger("test"))

    root_id = WorkspaceManager.root_id(str(ws))
    row = db.execute(
        "SELECT COUNT(1) FROM files WHERE path = ? AND root_id = ?",
        (f"{root_id}/main.py", root_id),
    ).fetchone()
    assert int(row[0]) == 1


def test_scan_to_db_skips_process_pool_on_high_cpu(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))

    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "1")
    monkeypatch.setenv("SARI_INDEXER_INITIAL_PROCESS_POOL", "1")
    monkeypatch.setenv("SARI_INDEXER_CPU_THROTTLE_ENABLED", "1")
    monkeypatch.setenv("SARI_INDEXER_CPU_HIGH_WATERMARK", "1")
    monkeypatch.setenv("SARI_INDEXER_CPU_RESUME_WATERMARK", "0")
    monkeypatch.setattr("sari.core.indexer.main._read_system_cpu_percent", lambda: 99.0)

    def _must_not_create_process_pool(*_args, **_kwargs):
        raise AssertionError("process pool should be skipped under high cpu")

    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", _must_not_create_process_pool)
    status = _scan_to_db(cfg, db, logging.getLogger("test"))
    assert int(status.get("indexed_files", 0) or 0) >= 1


def test_value_index_split_defers_then_backfills_payload(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    src = ws / "main.py"
    src.write_text("print('payload')\n", encoding="utf-8")

    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "1")
    monkeypatch.setenv("SARI_INDEXER_INITIAL_PROCESS_POOL", "0")
    monkeypatch.setenv("SARI_INDEXER_VALUE_INDEX_SPLIT", "1")

    _scan_to_db(cfg, db, logging.getLogger("test"))

    root_id = WorkspaceManager.root_id(str(ws))
    db_path = f"{root_id}/main.py"
    row = db.execute("SELECT metadata_json FROM files WHERE path = ?", (db_path,)).fetchone()
    assert row is not None
    meta_raw = row["metadata_json"] if hasattr(row, "keys") else row[0]
    meta = json.loads(meta_raw or "{}")
    assert bool(meta.get("deferred_payload", False)) is True
    assert db.read_file(str(src)) == ""

    monkeypatch.setenv("SARI_INDEXER_VALUE_INDEX_SPLIT", "0")
    _scan_to_db(cfg, db, logging.getLogger("test"))
    row2 = db.execute("SELECT metadata_json FROM files WHERE path = ?", (db_path,)).fetchone()
    meta2_raw = row2["metadata_json"] if hasattr(row2, "keys") else row2[0]
    meta2 = json.loads(meta2_raw or "{}")
    assert bool(meta2.get("deferred_payload", False)) is False
    assert "payload" in db.read_file(str(src))


def test_scan_to_db_initial_fastpath_skips_file_meta_lookup_when_root_empty(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("print('a')\n", encoding="utf-8")
    (ws / "b.py").write_text("print('b')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "1")

    def _fail_meta(_path):
        raise AssertionError("get_file_meta should be skipped for empty roots in initial fastpath")

    monkeypatch.setattr(db, "get_file_meta", _fail_meta)
    status = _scan_to_db(cfg, db, logging.getLogger("test"))
    assert int(status.get("indexed_files", 0) or 0) >= 2


def test_scan_to_db_initial_empty_skips_relation_replace_sources(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("print('a')\n", encoding="utf-8")
    (ws / "b.py").write_text("print('b')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "1")

    captured_replace_sizes: list[int] = []
    orig_upsert_rel = db.upsert_relations_tx

    def _capture_rel(cur, rows, replace_sources=None):
        captured_replace_sizes.append(len(list(replace_sources or [])))
        return orig_upsert_rel(cur, rows, replace_sources=replace_sources)

    monkeypatch.setattr(db, "upsert_relations_tx", _capture_rel)
    _scan_to_db(cfg, db, logging.getLogger("test"))
    assert captured_replace_sizes
    assert max(captured_replace_sizes) == 0


def test_scan_to_db_defer_turbo_stats_updates_during_flush(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(8):
        (ws / f"f{i}.py").write_text(f"print({i})\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_FLUSH_FILE_ROWS", "1")

    calls = {"update_stats": 0}
    orig_update_stats = db.update_stats

    def _count_update_stats():
        calls["update_stats"] += 1
        return orig_update_stats()

    monkeypatch.setattr(db, "update_stats", _count_update_stats)
    _scan_to_db(cfg, db, logging.getLogger("test"))
    # Consolidated update should happen near scan tail, not on every flush.
    assert calls["update_stats"] <= 2


def test_scan_to_db_process_pool_fallback_to_thread_pool(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("print('a')\n", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    monkeypatch.setenv("SARI_INDEXER_INITIAL_FASTPATH", "1")
    monkeypatch.setenv("SARI_INDEXER_INITIAL_PROCESS_POOL", "1")

    def _raise_perm(*args, **kwargs):
        raise PermissionError("process pool blocked")

    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", _raise_perm)
    status = _scan_to_db(cfg, db, logging.getLogger("test"))
    assert int(status.get("indexed_files", 0) or 0) >= 1
