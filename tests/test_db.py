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


def test_schema_has_symbol_relations_unique_index(db):
    rows = db.execute("PRAGMA index_list('symbol_relations')").fetchall()
    names = [str(r[1]) for r in rows]
    assert "ux_symbol_relations_identity" in names


def test_db_set_settings_is_available_for_runtime_bootstrap(db):
    marker = object()
    db.set_settings(marker)
    assert getattr(db, "settings", None) is marker
