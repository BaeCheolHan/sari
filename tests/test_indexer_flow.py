import time
import pytest
from pathlib import Path
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.config.main import Config
from sari.core.workspace import WorkspaceManager

@pytest.fixture
def db(tmp_path):
    return LocalSearchDB(str(tmp_path / "index.db"))

@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("def hello():\n    pass")
    (ws / "data.txt").write_text("some data")
    (ws / ".sariroot").touch()
    return ws

def test_indexer_full_scan(db, workspace):
    cfg = Config.load(None, workspace_root_override=str(workspace))
    root_id = WorkspaceManager.root_id(str(workspace))
    db.upsert_root(root_id, str(workspace), str(workspace))
    
    indexer = Indexer(cfg, db)
    for root in indexer.cfg.workspace_roots:
        rid = WorkspaceManager.root_id(root)
        db.upsert_root(rid, root, root)
    # 1. Dispatch scan tasks
    indexer.scan_once()
    
    # 2. Synchronous processing for testing
    while indexer.coordinator.fair_queue.qsize() > 0:
        item = indexer.coordinator.fair_queue.get()
        if item:
            # Manually call handle_task which usually runs in thread pool
            indexer._handle_task(item[0], item[1])
            
    # 3. Manually trigger DBWriter batch processing (synchronously)
    tasks = indexer._db_writer._drain_batch(100)
    with db._write:
        indexer._db_writer._process_batch(db._write.cursor(), tasks)
    
    hits = db.search_files("")
    assert len(hits) >= 2

def test_delta_indexing(db, workspace):
    cfg = Config.load(None, workspace_root_override=str(workspace))
    root_id = WorkspaceManager.root_id(str(workspace))
    db.upsert_root(root_id, str(workspace), str(workspace))
    
    indexer = Indexer(cfg, db)
    for root in indexer.cfg.workspace_roots:
        rid = WorkspaceManager.root_id(root)
        db.upsert_root(rid, root, root)
    
    # 1. First scan & commit
    indexer.scan_once()
    while indexer.coordinator.fair_queue.qsize() > 0:
        item = indexer.coordinator.fair_queue.get()
        indexer._handle_task(item[0], item[1])
    
    tasks = indexer._db_writer._drain_batch(100)
    with db._write: indexer._db_writer._process_batch(db._write.cursor(), tasks)
    
    first_count = indexer.status.indexed_files
    assert first_count >= 2
    
    # 2. Modify mtime but same content
    time.sleep(1.1)
    (workspace / "main.py").touch()
    
    indexer.scan_once()
    while indexer.coordinator.fair_queue.qsize() > 0:
        item = indexer.coordinator.fair_queue.get()
        indexer._handle_task(item[0], item[1])
    
    # No new tasks should be in DBWriter because content hash matched
    tasks = indexer._db_writer._drain_batch(100)
    assert len(tasks) == 0 or all(t.kind == "update_last_seen" for t in tasks)
    
    assert indexer.status.indexed_files == first_count
