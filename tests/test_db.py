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
    
    row = ("p_comp", "rel", "root", "repo", 100, len(compressed), compressed, "h", "fts", 200, 0, "ok", "", "ok", "", 0, 0, 0, len(content), "{}")
    db.upsert_files_turbo([row])
    db.finalize_turbo_batch()
    
    # Must return decrypted string
    assert db.read_file("p_comp") == content
