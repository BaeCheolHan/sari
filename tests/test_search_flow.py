import pytest
from pathlib import Path
from sari.core.db.main import LocalSearchDB
from sari.core.search_engine import SearchEngine
from sari.core.models import SearchOptions

@pytest.fixture
def db_with_data(tmp_path):
    db = LocalSearchDB(str(tmp_path / "search.db"))
    # Pre-requisite for FOREIGN KEY
    db.upsert_root("root-1", "/path/1", "/real/1")
    
    cur = db._write.cursor()
    rows = [
        ("root-1/test.py", "test.py", "root-1", "repo1", 100, 50, b"def search_target(): pass", "h1", "", 0, 0, "ok", "none", "none", "none", 0, 0, 0, 25, "{}"),
        ("root-1/README.md", "README.md", "root-1", "repo1", 101, 100, b"# Documentation", "h2", "", 0, 0, "ok", "none", "none", "none", 0, 0, 0, 15, "{}"),
    ]
    db.upsert_files_tx(cur, rows)
    db._write.commit()
    return db

def test_sqlite_fallback_search(db_with_data):
    engine = SearchEngine(db_with_data)
    opts = SearchOptions(query="search_target", root_ids=["root-1"])
    
    hits, meta = engine.search_v2(opts)
    
    assert len(hits) == 1
    assert "test.py" in hits[0].path
    assert meta["engine"] == "sqlite"

def test_root_id_filtering(db_with_data):
    engine = SearchEngine(db_with_data)
    
    opts = SearchOptions(query="Documentation", root_ids=["root-wrong"])
    hits, _ = engine.search_v2(opts)
    assert len(hits) == 0
    
    opts = SearchOptions(query="Documentation", root_ids=["root-1"])
    hits, _ = engine.search_v2(opts)
    assert len(hits) == 1