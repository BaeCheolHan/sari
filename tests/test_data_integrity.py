import pytest
import os
import hashlib
import time
from pathlib import Path
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.config import Config
from sari.core.workspace import WorkspaceManager

@pytest.fixture
def integrity_env(tmp_path):
    ws = tmp_path / "integrity_ws"
    ws.mkdir()
    db = LocalSearchDB(str(tmp_path / "integrity.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    return {"ws": ws, "db": db, "cfg": cfg}

def test_data_content_identity(integrity_env):
    """
    1. Verify that stored content is bit-for-bit identical to source.
    """
    ws, db, cfg = integrity_env["ws"], integrity_env["db"], integrity_env["cfg"]
    
    # Create file with tricky content (Unicode, Newlines, Code snippets)
    tricky_content = "def hello():\n    print('안녕 🚀')\n" * 100
    file_path = ws / "tricky.py"
    file_path.write_text(tricky_content, encoding="utf-8")
    
    db.ensure_root(WorkspaceManager.root_id(str(ws)), str(ws))
    indexer = Indexer(cfg, db)
    indexer.scan_once()
    
    stored_content = db.read_file(str(file_path))
    assert stored_content == tricky_content
    assert len(stored_content.encode("utf-8")) == file_path.stat().st_size

def test_data_path_normalization_integrity(integrity_env):
    """
    2. Verify that paths are correctly normalized and don't duplicate.
    """
    ws, db, cfg = integrity_env["ws"], integrity_env["db"], integrity_env["cfg"]
    
    p1 = ws / "norm.py"
    p1.write_text("norm")
    
    db.ensure_root(WorkspaceManager.root_id(str(ws)), str(ws))
    indexer = Indexer(cfg, db)
    indexer.scan_once()
    indexer.scan_once()
    
    results = db.search_files("norm.py")
    assert len(results) == 1

def test_data_atomic_flush_integrity(integrity_env):
    """
    3. Verify transactional atomicity between RAM and Disk.
    """
    db = integrity_env["db"]
    db.ensure_root("root", "/tmp")

    rows = [
        (f"path_{i}", f"rel_{i}", "root", "repo", 0, 10, b"data", "h", "fts", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}")
        for i in range(50)
    ]
    db.upsert_files_turbo(rows)
    assert len(db.search_files("path_")) == 0
    db.finalize_turbo_batch()
    assert len(db.search_files("path_")) == 50

def test_data_large_file_integrity(integrity_env):
    """
    4. Verify handling of larger files (5MB+) through the Turbo pipeline.
    """
    ws, db, cfg = integrity_env["ws"], integrity_env["db"], integrity_env["cfg"]

    # Use .py extension (in include_ext) instead of .txt
    large_content = "# " + "A" * (5 * 1024 * 1024 - 2) # 5MB Python comment
    (ws / "large.py").write_text(large_content)

    db.ensure_root(WorkspaceManager.root_id(str(ws)), str(ws))
    indexer = Indexer(cfg, db)
    indexer.scan_once()    
    stored = db.read_file(str(ws / "large.py"))
    assert len(stored) == len(large_content)
    assert hashlib.md5(stored.encode()).hexdigest() == hashlib.md5(large_content.encode()).hexdigest()

def test_data_incremental_update_integrity(integrity_env):
    """
    5. Verify that changing a file updates existing record instead of leaking old data.
    """
    ws, db, cfg = integrity_env["ws"], integrity_env["db"], integrity_env["cfg"]
    
    target = ws / "update.py"
    target.write_text("version 1")
    
    db.ensure_root(WorkspaceManager.root_id(str(ws)), str(ws))
    indexer = Indexer(cfg, db)
    indexer.scan_once()
    assert db.read_file(str(target)) == "version 1"
    
    time.sleep(1.1) 
    target.write_text("version 2")
    
    indexer.scan_once()
    assert db.read_file(str(target)) == "version 2"
    
    # Check physical uniqueness
    conn = db.db.connection()
    count = conn.execute("SELECT COUNT(*) FROM files WHERE path LIKE '%update.py'").fetchone()[0]
    assert count == 1
