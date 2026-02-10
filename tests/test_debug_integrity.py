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
    
    # Verify DB state with concrete assertions.
    conn = db.db.connection()
    all_symbols = conn.execute("SELECT * FROM symbols").fetchall()
    assert all_symbols

    all_files = conn.execute("SELECT path FROM files").fetchall()
    assert all_files
