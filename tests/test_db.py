import pytest
import zlib
from sari.core.db.main import LocalSearchDB

@pytest.fixture
def db(tmp_path):
    return LocalSearchDB(str(tmp_path / "test.db"))

def test_db_turbo_ingestion_and_search(db):
    """
    Verify the Ultra-Turbo ingestion logic: RAM Staging -> Flush -> Search.
    """
    # 0. Prerequisite: Root must exist
    db.upsert_root("root1", "/tmp/root1", "/tmp/root1")

    # 1. High-speed write to RAM
    row = ("p1", "rel1", "root1", "repo1", 100, 50, b"content1", "h1", "fts", 200, 0, "ok", "", "ok", "", 0, 0, 0, 50, "{}")
    db.upsert_files_turbo([row])
    
    # Verify not yet in Disk
    assert len(db.search_files("rel1")) == 0
    
    # 2. Flush to Disk
    db.finalize_turbo_batch()
    
    # 3. Verify Search (Using PeeWee backend)
    results = db.search_files("rel1")
    assert len(results) == 1
    assert results[0]["path"] == "p1"


def test_finalize_turbo_batch_invokes_wal_checkpoint_hook(db, monkeypatch):
    db.upsert_root("root1", "/tmp/root1", "/tmp/root1")
    row = ("p1", "rel1", "root1", "repo1", 100, 50, b"content1", "h1", "fts", 200, 0, "ok", "", "ok", "", 0, 0, 0, 50, "{}")
    db.upsert_files_turbo([row])

    calls = {"n": 0}

    def _count_checkpoint(force=False):
        calls["n"] += 1
        return False

    monkeypatch.setattr(db, "maybe_checkpoint_wal", _count_checkpoint)
    db.finalize_turbo_batch()
    assert calls["n"] >= 1


def test_maybe_checkpoint_wal_force_executes_passive(db, monkeypatch):
    db._wal_idle_checkpoint_enabled = True
    monkeypatch.setattr("os.path.getsize", lambda _p: 1024 * 1024 * 64)
    seen = {"checkpoint": 0}

    class _FakeConn:
        def execute(self, sql, *args, **kwargs):
            if "wal_checkpoint(PASSIVE)" in str(sql):
                seen["checkpoint"] += 1
            return []

    monkeypatch.setattr(db.db, "connection", lambda: _FakeConn())
    ok = db.maybe_checkpoint_wal(force=True)
    assert ok is True
    assert seen["checkpoint"] >= 1


def test_upsert_symbols_and_relations_tx_commits_both_sets(db):
    db.upsert_root("root1", "/tmp/root1", "/tmp/root1")
    file_row = (
        "root1/a.py",
        "a.py",
        "root1",
        "repo1",
        1,
        10,
        b"print('x')",
        "h1",
        "print x",
        1,
        0,
        "ok",
        "",
        "ok",
        "",
        "ok",
        "",
        0,
        0,
        "{}",
    )
    db.upsert_files_turbo([file_row])
    db.finalize_turbo_batch()

    symbol_rows = [
        ("sid-a", "root1/a.py", "root1", "a", "function", 1, 1, "def a(): pass", "", "{}", "", "a"),
    ]
    relation_rows = [
        ("root1/a.py", "root1", "a", "sid-a", "root1/a.py", "root1", "a", "sid-a", "calls", 1, "{}"),
    ]
    db.upsert_symbols_and_relations_tx(symbol_rows, relation_rows, replace_sources=[("root1/a.py", "root1")])

    sym = db.execute("SELECT COUNT(1) FROM symbols WHERE path = ?", ("root1/a.py",)).fetchone()
    rel = db.execute(
        "SELECT COUNT(1) FROM symbol_relations WHERE from_path = ? AND from_root_id = ?",
        ("root1/a.py", "root1"),
    ).fetchone()
    assert int(sym[0]) == 1
    assert int(rel[0]) == 1

def test_db_intelligent_read_compressed(db):
    """
    Verify that read_file handles compressed data automatically.
    """
    content = "Modern Sari Engine"
    compressed = b"ZLIB\0" + zlib.compress(content.encode("utf-8"))
    
    db.upsert_root("root", "/tmp/root", "/tmp/root")

    row = ("p_comp", "rel", "root", "repo", 100, len(compressed), compressed, "h", "fts", 200, 0, "ok", "", "ok", "", 0, 0, 0, len(content), "{}")
    db.upsert_files_turbo([row])
    db.finalize_turbo_batch()
    
    # Must return decrypted string
    assert db.read_file("p_comp") == content


def test_db_get_roots_includes_counts_and_paths(db):
    db.upsert_root("rid-a", "/tmp/ws-a", "/tmp/ws-a")
    db.upsert_root("rid-b", "/tmp/ws-b", "/tmp/ws-b")
    rows = [
        ("rid-a/a.py", "a.py", "rid-a", "repo-a", 100, 10, b"print(1)", "h1", "print(1)", 200, 0, "ok", "", "ok", "", 0, 0, 0, 8, "{}"),
        ("rid-a/b.py", "b.py", "rid-a", "repo-a", 101, 11, b"print(2)", "h2", "print(2)", 201, 0, "ok", "", "ok", "", 0, 0, 0, 8, "{}"),
        ("rid-b/c.py", "c.py", "rid-b", "repo-b", 102, 12, b"print(3)", "h3", "print(3)", 202, 0, "ok", "", "ok", "", 0, 0, 0, 8, "{}"),
    ]
    db.upsert_files_turbo(rows)
    db.finalize_turbo_batch()

    roots = sorted(db.get_roots(), key=lambda r: r["root_id"])

    assert [r["root_id"] for r in roots] == ["rid-a", "rid-b"]
    assert roots[0]["path"] == "/tmp/ws-a"
    assert roots[1]["path"] == "/tmp/ws-b"
    assert roots[0]["file_count"] == 2
    assert roots[1]["file_count"] == 1


def test_db_execute_allows_direct_sql(db):
    db.execute("CREATE TABLE IF NOT EXISTS _tmp_x (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO _tmp_x(name) VALUES (?)", ("ok",))
    row = db.execute("SELECT COUNT(1) FROM _tmp_x").fetchone()
    assert int(row[0]) == 1


def test_relations_upsert_deduplicates_duplicate_rows(db):
    db.upsert_root("rid-a", "/tmp/ws-a", "/tmp/ws-a")
    rel = (
        "rid-a/a.py",
        "rid-a",
        "caller",
        "sid-caller",
        "rid-a/b.py",
        "rid-a",
        "callee",
        "sid-callee",
        "calls",
        12,
        "{}",
    )
    db.upsert_relations_tx(None, [rel, rel, rel])
    db.upsert_relations_tx(None, [rel])
    row = db.execute("SELECT COUNT(1) FROM symbol_relations").fetchone()
    assert int(row[0]) == 1


def test_relations_replace_sources_removes_stale_outgoing_rows(db):
    db.upsert_root("rid-a", "/tmp/ws-a", "/tmp/ws-a")
    old_rel = (
        "rid-a/a.py",
        "rid-a",
        "caller",
        "sid-caller",
        "rid-a/b.py",
        "rid-a",
        "callee",
        "sid-callee",
        "calls",
        12,
        "{}",
    )
    db.upsert_relations_tx(None, [old_rel])
    before = db.execute("SELECT COUNT(1) FROM symbol_relations WHERE from_path = ?", ("rid-a/a.py",)).fetchone()
    assert int(before[0]) == 1

    db.upsert_relations_tx(None, [], replace_sources=[("rid-a/a.py", "rid-a")])

    after = db.execute("SELECT COUNT(1) FROM symbol_relations WHERE from_path = ?", ("rid-a/a.py",)).fetchone()
    assert int(after[0]) == 0


def test_relations_replace_sources_and_insert_new_rows_atomically(db):
    db.upsert_root("rid-a", "/tmp/ws-a", "/tmp/ws-a")
    old_rel = (
        "rid-a/a.py",
        "rid-a",
        "caller",
        "sid-caller",
        "rid-a/b.py",
        "rid-a",
        "callee",
        "sid-callee",
        "calls",
        12,
        "{}",
    )
    new_rel = (
        "rid-a/a.py",
        "rid-a",
        "caller",
        "sid-caller",
        "rid-a/c.py",
        "rid-a",
        "callee2",
        "sid-callee2",
        "calls",
        33,
        "{}",
    )
    db.upsert_relations_tx(None, [old_rel])
    db.upsert_relations_tx(None, [new_rel], replace_sources=[("rid-a/a.py", "rid-a")])

    rows = db.execute(
        "SELECT to_path, line FROM symbol_relations WHERE from_path = ? ORDER BY line ASC",
        ("rid-a/a.py",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "rid-a/c.py"
    assert int(rows[0][1]) == 33


def test_schema_has_symbol_relations_unique_index(db):
    rows = db.execute("PRAGMA index_list('symbol_relations')").fetchall()
    names = [str(r[1]) for r in rows]
    assert "ux_symbol_relations_identity" in names


def test_db_set_settings_is_available_for_runtime_bootstrap(db):
    marker = object()
    db.set_settings(marker)
    assert getattr(db, "settings", None) is marker


def test_update_last_seen_tx_accepts_none_cursor(db):
    db.upsert_root("rid-a", "/tmp/ws-a", "/tmp/ws-a")
    row = (
        "rid-a/a.py",
        "a.py",
        "rid-a",
        "repo-a",
        100,
        10,
        b"print(1)",
        "h1",
        "print(1)",
        1,
        0,
        "ok",
        "",
        "ok",
        "",
        0,
        0,
        0,
        8,
        "{}",
    )
    db.upsert_files_turbo([row])
    db.finalize_turbo_batch()

    db.update_last_seen_tx(None, ["rid-a/a.py"], 12345)
    got = db.execute("SELECT last_seen_ts FROM files WHERE path = ?", ("rid-a/a.py",)).fetchone()
    assert got is not None
    assert int(got[0]) == 12345


def test_upsert_symbols_tx_none_cursor_is_atomic_on_insert_failure(db):
    db.upsert_root("rid-a", "/tmp/ws-a", "/tmp/ws-a")
    db.upsert_files_turbo(
        [
            (
                "rid-a/a.py",
                "a.py",
                "rid-a",
                "repo-a",
                100,
                10,
                b"print(1)",
                "h1",
                "print(1)",
                100,
                0,
                "ok",
                "",
                "ok",
                "",
                0,
                0,
                0,
                8,
                "{}",
            )
        ]
    )
    db.finalize_turbo_batch()
    cur = db._write.cursor()
    cur.execute(
        "INSERT INTO symbols(symbol_id, path, root_id, name, kind, line, end_line, content, parent, meta_json, doc_comment, qualname, importance_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sid-old", "rid-a/a.py", "rid-a", "Old", "class", 1, 2, "class Old: pass", "", "{}", "", "Old", 1.0),
    )
    db._write.commit()
    db.execute(
        """
        CREATE TRIGGER tr_symbols_fail_insert
        BEFORE INSERT ON symbols
        BEGIN
            SELECT RAISE(ABORT, 'symbols insert blocked');
        END
        """
    )

    rows = [
        (
            "sid-new",
            "rid-a/a.py",
            "rid-a",
            "New",
            "class",
            1,
            2,
            "class New: pass",
            "",
            "{}",
            "",
            "New",
            1.0,
        )
    ]
    with pytest.raises(Exception):
        db.upsert_symbols_tx(None, rows)

    kept = db.execute(
        "SELECT symbol_id FROM symbols WHERE path = ? ORDER BY symbol_id",
        ("rid-a/a.py",),
    ).fetchall()
    assert [r[0] for r in kept] == ["sid-old"]


def test_upsert_relations_tx_none_cursor_is_atomic_on_insert_failure(db):
    db.upsert_root("rid-a", "/tmp/ws-a", "/tmp/ws-a")
    old_rel = (
        "rid-a/a.py",
        "rid-a",
        "caller",
        "sid-caller",
        "rid-a/b.py",
        "rid-a",
        "callee",
        "sid-callee",
        "calls",
        10,
        "{}",
    )
    db.upsert_relations_tx(None, [old_rel])
    db.execute(
        """
        CREATE TRIGGER tr_rel_fail_insert
        BEFORE INSERT ON symbol_relations
        BEGIN
            SELECT RAISE(ABORT, 'relations insert blocked');
        END
        """
    )
    new_rel = (
        "rid-a/a.py",
        "rid-a",
        "caller",
        "sid-caller",
        "rid-a/c.py",
        "rid-a",
        "callee2",
        "sid-callee2",
        "calls",
        11,
        "{}",
    )

    with pytest.raises(Exception):
        db.upsert_relations_tx(None, [new_rel], replace_sources=[("rid-a/a.py", "rid-a")])

    rows = db.execute(
        "SELECT to_path, line FROM symbol_relations WHERE from_path = ? ORDER BY line ASC",
        ("rid-a/a.py",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "rid-a/b.py"
    assert int(rows[0][1]) == 10
