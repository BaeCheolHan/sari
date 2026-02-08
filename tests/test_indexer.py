import pytest
import time
from pathlib import Path
from sari.core.indexer.main import Indexer
from sari.core.db.main import LocalSearchDB
from sari.core.config import Config

@pytest.fixture
def test_context(tmp_path):
    # Setup WS
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')")
    (ws / "utils.js").write_text("function add(a,b) { return a+b; }")
    
    # Setup DB
    db = LocalSearchDB(str(tmp_path / "sari.db"))
    
    # Setup Config
    cfg = Config(**Config.get_defaults(str(ws)))
    
    return {"ws": ws, "db": db, "cfg": cfg}

def test_indexer_end_to_end_flow(test_context):
    """
    Verify the real modernization: Scan -> Parallel Process -> Turbo DB -> Read.
    """
    db, cfg, ws = test_context["db"], test_context["cfg"], test_context["ws"]
    indexer = Indexer(cfg, db)
    
    # Execute actual high-speed scan
    indexer.scan_once()
    
    # Verify DB content (Real verification of the new architecture)
    assert indexer.status.indexed_files >= 2
    assert indexer.status.index_ready is True
    
    # Verify content retrieval (Testing the intelligent read_file)
    content = db.read_file(str(ws / "main.py"))
    assert "print('hello')" in content

def test_indexer_lifecycle_cleanup(test_context):
    """
    Ensure the process pool is actually terminated on stop.
    """
    db, cfg = test_context["db"], test_context["cfg"]
    indexer = Indexer(cfg, db)
    assert indexer._executor is not None
    indexer.stop()
    assert indexer._executor is None
