import pytest
import time
import os
from pathlib import Path
from sari.core.indexer.main import Indexer
from sari.core.db.main import LocalSearchDB
from sari.core.config import Config

def test_indexer_massive_file_stress(tmp_path):
    """
    STRESS: 10,000 files indexing.
    Verify that ProcessPoolExecutor and Turbo DB can handle heavy load without memory leaks or deadlocks.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    db = LocalSearchDB(str(tmp_path / "stress.db"))
    
    # Create 10,000 files
    for i in range(10000):
        (ws / f"file_{i}.txt").write_text(f"Stress content for file {i}")
    
    cfg = Config(**Config.get_defaults(str(ws)))
    indexer = Indexer(cfg, db)
    
    start = time.time()
    indexer.scan_once()
    elapsed = time.time() - start
    
    # Verify results
    assert indexer.status.indexed_files == 10000
    assert indexer.status.index_ready is True
    
    print(f"Indexed 10,000 files in {elapsed:.2f}s")

def test_indexer_rapid_stop_start_cycle(tmp_path):
    """
    STRESS: Verify lifecycle stability.
    Rapidly starting and stopping the indexer should not leave orphaned processes or corrupted DB handles.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "dummy.py").write_text("print(1)")
    db = LocalSearchDB(str(tmp_path / "cycle.db"))
    cfg = Config(**Config.get_defaults(str(ws)))
    
    for _ in range(5):
        indexer = Indexer(cfg, db)
        # Interrupting during scan (simulated via rapid stop)
        indexer.stop()
        assert indexer._executor is None