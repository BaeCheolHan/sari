import pytest
import os
from sari.core.indexer.main import Indexer
from sari.core.db.main import LocalSearchDB
from sari.core.workspace import WorkspaceManager
from sari.core.config import Config

@pytest.fixture
def func_env(tmp_path):
    ws = (tmp_path / "func_ws").resolve()
    ws.mkdir()
    db = LocalSearchDB(str(tmp_path / "func.db"))
    # Ensure Config is based on the resolved absolute path
    cfg = Config(**Config.get_defaults(str(ws)))
    return {"ws": ws, "db": db, "cfg": cfg}

def test_symbol_extraction_integrity(func_env):
    """
    1. Verify that parallel workers correctly extract and store code symbols.
    """
    ws, db, cfg = func_env["ws"], func_env["db"], func_env["cfg"]
    
    # Use a raw string with precise indentation to avoid parsing issues
    code = (
        "class MyTruth:\n"
        "    def verify(self):\n"
        "        return True\n"
    )
    target_file = ws / "logic.py"
    target_file.write_text(code, encoding="utf-8")
    
    indexer = Indexer(cfg, db)
    indexer.scan_once()
    
    # Truth: Query the symbols table directly using broad match
    conn = db.db.connection()
    # We use LIKE to avoid absolute/relative path mismatch issues in tests
    res = conn.execute("SELECT name, kind FROM symbols WHERE name = 'MyTruth'").fetchone()
    
    assert res is not None, "Class symbol 'MyTruth' should be in DB"
    assert "class" in res[1].lower()
    
    res = conn.execute("SELECT name FROM symbols WHERE name = 'verify'").fetchone()
    assert res is not None, "Function symbol 'verify' should be in DB"

def test_stale_data_pruning_integrity(func_env):
    """
    2. Verify that deleted files are correctly removed from the DB.
    """
    ws, db, cfg = func_env["ws"], func_env["db"], func_env["cfg"]
    
    p1 = ws / "exists.py"
    p1.write_text("content")
    p2 = ws / "gone.py"
    p2.write_text("content")
    
    indexer = Indexer(cfg, db)
    indexer.scan_once()
    
    # Verify both exist
    assert len(db.search_files("exists.py")) == 1
    assert len(db.search_files("gone.py")) == 1
    
    # DELETE GONE.PY
    os.remove(p2)
    
    # RE-SCAN: Finds only p1
    indexer.scan_once()
    
    # ACTIVATE PRUNING: Only p1 remains active
    active_paths = [str(p1)]
    rid = WorkspaceManager.root_id(str(ws))
    db.prune_stale_data(rid, active_paths)
    
    # VERIFY: GONE.PY is wiped, EXISTS.PY remains
    assert len(db.search_files("gone.py")) == 0
    assert len(db.search_files("exists.py")) == 1

def test_search_hit_metadata_integrity(func_env):
    """
    3. Verify that search results return accurate metadata from Turbo DB.
    """
    ws, db, cfg = func_env["ws"], func_env["db"], func_env["cfg"]
    
    (ws / "meta.py").write_text("search_me_now")
    st = (ws / "meta.py").stat()
    
    indexer = Indexer(cfg, db)
    indexer.scan_once()
    
    results = db.search_files("search_me_now")
    assert len(results) == 1
    hit = results[0]
    
    # Metadata Truth: Path must be correct and size must match
    assert "meta.py" in hit["path"]
    assert hit["size"] == st.st_size