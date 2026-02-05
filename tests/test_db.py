import os
import pytest
from sari.core.db.main import LocalSearchDB
from sari.core.db.schema import CURRENT_SCHEMA_VERSION

@pytest.fixture
def db(tmp_path):
    db_file = tmp_path / "test.db"
    return LocalSearchDB(str(db_file))

def test_db_initialization(db):
    assert os.path.exists(db.db_path)
    # Check schema version
    conn = db._get_conn()
    v = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert v == CURRENT_SCHEMA_VERSION

def test_root_isolation(db):
    db.upsert_root("root-1", "/path/1", "/real/1")
    db.upsert_root("root-2", "/path/2", "/real/2")
    
    # Insert files for different roots
    cur = db._write.cursor()
    # (path, rel_path, root_id, repo, mtime, size, content, content_hash, ...)
    rows = [
        ("root-1/a.py", "a.py", "root-1", "repo1", 100, 10, b"content", "h1", "", 0, 0, "ok", "none", "none", "none", 0, 0, 0, 7, "{}"),
        ("root-2/b.py", "b.py", "root-2", "repo2", 200, 20, b"content", "h2", "", 0, 0, "ok", "none", "none", "none", 0, 0, 0, 7, "{}"),
    ]
    db.upsert_files_tx(cur, rows)
    db._write.commit()
    
    # Search with root-1 filter
    hits = db.search_files("", root_id="root-1")
    assert len(hits) == 1
    assert hits[0]["root_id"] == "root-1"
    
    # Search with root-2 filter
    hits = db.search_files("", root_id="root-2")
    assert len(hits) == 1
    assert hits[0]["root_id"] == "root-2"

def test_apply_root_filter(db):
    sql, params = db.apply_root_filter("SELECT * FROM files", "root-X")
    assert "WHERE root_id = ?" in sql
    assert params == ["root-X"]
    
    sql2, params2 = db.apply_root_filter("SELECT * FROM files WHERE mtime > 0", "root-Y")
    assert "AND root_id = ?" in sql2
    assert params2 == ["root-Y"]
