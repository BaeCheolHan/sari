import pytest
from sari.core.db.main import LocalSearchDB

def test_storage_direct_turbo_persistence(tmp_path):
    """
    Verify that the storage backend correctly persists data through the Turbo path.
    """
    db_path = tmp_path / "storage.db"
    db = LocalSearchDB(str(db_path))
    
    # 1. Prepare bulk data
    rows = []
    for i in range(100):
        rows.append((f"p{i}", f"rel{i}", "root", "repo", 0, 10, b"data", "h", "fts", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}"))
    
    # 2. Bulk Ingestion
    db.upsert_files_turbo(rows)
    db.finalize_turbo_batch()
    
    # 3. Persistence Check
    assert len(db.search_files("rel50")) == 1
    db.close()
    
    # 4. Reload Check (Real durability)
    db2 = LocalSearchDB(str(db_path))
    assert len(db2.search_files("rel99")) == 1