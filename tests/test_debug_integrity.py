import pytest
import sys
import os
from sari.core.indexer.main import Indexer
from sari.core.db.main import LocalSearchDB
from sari.core.config import Config

def test_debug_symbol_extraction(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    code = "class MyClass:\n    pass\n"
    (ws / "test.py").write_text(code)
    
    db = LocalSearchDB(str(tmp_path / "test.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    indexer = Indexer(cfg, db)
    
    # Run scan
    indexer.scan_once()
    
    # Examine DB
    conn = db.db.connection()
    all_symbols = conn.execute("SELECT * FROM symbols").fetchall()
    print(f"\nDEBUG: Total symbols in DB: {len(all_symbols)}")
    for s in all_symbols:
        print(f"DEBUG: Symbol row: {s}")
        
    all_files = conn.execute("SELECT path FROM files").fetchall()
    print(f"DEBUG: Total files in DB: {len(all_files)}")
    for f in all_files:
        print(f"DEBUG: File path: {f[0]}")