import pytest
import os

@pytest.fixture(autouse=True)
def sari_env(monkeypatch):
    monkeypatch.setenv("SARI_DAEMON_PORT", "48000")
    monkeypatch.setenv("SARI_DAEMON_IDLE_SEC", "3600")
    monkeypatch.setenv("SARI_TEST_MODE", "1")
    monkeypatch.setenv("SARI_FORMAT", "pack")

@pytest.fixture
def db(tmp_path):
    from sari.core.db.main import LocalSearchDB
    db_file = tmp_path / f"sari_test_{os.getpid()}.db"
    
    # Initialize with proper pragmas and WAL mode
    db_inst = LocalSearchDB(str(db_file), journal_mode="wal")
    
    yield db_inst
    
    # Graceful cleanup
    try:
        db_inst.close_all()
    except Exception:
        pass

@pytest.fixture
def workspace(db, tmp_path):
    """Provides a registered workspace root and its absolute path."""
    root_path = str(tmp_path / "fake_workspace")
    import os
    os.makedirs(root_path, exist_ok=True)
    
    # Register in DB
    db.upsert_root(root_path, root_path, root_path)
    return root_path

@pytest.fixture
def sample_project(db, workspace):
    """Pre-populates the DB with a standard Interface -> Class inheritance project."""
    from sari.core.models import IndexingResult
    rid = workspace
    
    # 1. Create Files
    files = [
        IndexingResult(path=f"{rid}/A.py", rel="A.py", root_id=rid, repo="repo", type="new"),
        IndexingResult(path=f"{rid}/B.py", rel="B.py", root_id=rid, repo="repo", type="new"),
    ]
    db.upsert_files_turbo(files)
    db.finalize_turbo_batch()
    
    # 2. Create Symbols & Relations
    conn = db.get_read_connection()
    conn.execute("PRAGMA foreign_keys = OFF") # For fast bulk setup in tests
    try:
        # A (Interface)
        conn.execute("INSERT INTO symbols (symbol_id, path, root_id, name, kind, line, end_line, content, qualname) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("sid-a", f"{rid}/A.py", rid, "Base", "class", 1, 10, "class Base: pass", "Base"))
        # B (Implementation)
        conn.execute("INSERT INTO symbols (symbol_id, path, root_id, name, kind, line, end_line, content, qualname) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("sid-b", f"{rid}/B.py", rid, "Impl", "class", 1, 10, "class Impl(Base): pass", "Impl"))
        # Relation: B extends A
        conn.execute("INSERT INTO symbol_relations (from_path, from_root_id, from_symbol, from_symbol_id, to_path, to_root_id, to_symbol, to_symbol_id, rel_type, line) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (f"{rid}/B.py", rid, "Impl", "sid-b", f"{rid}/A.py", rid, "Base", "sid-a", "extends", 1))
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    
    return {"root": rid, "base_sid": "sid-a", "impl_sid": "sid-b"}



@pytest.fixture(autouse=True)
def cleanup_mocks():
    yield
    from unittest.mock import patch
    patch.stopall()
