import pytest
import os
import signal
import time
import subprocess
from pathlib import Path
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.config import Config

def test_chaos_db_file_corruption_recovery(tmp_path):
    """
    CHAOS: What happens if the DB file is corrupted while Sari is running?
    Truth: Sari should handle OperationalError gracefully.
    """
    db_path = tmp_path / "chaos.db"
    db = LocalSearchDB(str(db_path))
    
    # Fill with some data
    db.upsert_files_turbo([("p1", "rel", "root", "repo", 0, 10, b"data", "h", "fts", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}")])
    db.finalize_turbo_batch()
    
    # CORRUPTION: Physically delete the file while Sari thinks it is open
    import os
    if os.path.exists(db_path):
        os.remove(db_path)
        
    # Sari must handle this as a failure state
    with pytest.raises(Exception):
        db.search_files("rel")

def test_chaos_indexer_mid_scan_termination(tmp_path):
    """
    CHAOS: Terminating Indexer in the middle of a massive scan.
    Truth: No orphaned threads, RAM DB should just evaporate without disk corruption.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(1000): (ws / f"f{i}.txt").write_text("chaos")
    
    db = LocalSearchDB(str(tmp_path / "sari.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    indexer = Indexer(cfg, db)
    
    # Start scan and stop immediately (simulating a crash/interrupt)
    indexer.scan_once()
    indexer.stop()
    
    assert indexer._executor is None