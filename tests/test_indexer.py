import pytest
import logging

from sari.core.indexer.main import Indexer, _scan_to_db, _worker_build_snapshot
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


def test_scan_to_db_raises_when_parent_dead(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')", encoding="utf-8")
    db = LocalSearchDB(str(tmp_path / "idx.db"))
    cfg = Config(**Config.get_defaults(str(ws)))

    with pytest.raises(RuntimeError, match="orphaned worker detected"):
        _scan_to_db(
            cfg,
            db,
            logging.getLogger("test"),
            parent_pid=999999,
            parent_alive_check=lambda _pid: False,
        )


def test_worker_build_snapshot_writes_error_when_parent_dead(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("print('x')", encoding="utf-8")
    cfg = Config(**Config.get_defaults(str(ws)))
    snapshot = str(tmp_path / "idx.db.snapshot")
    status_path = str(tmp_path / "status.json")
    log_path = str(tmp_path / "worker.log")

    monkeypatch.setattr("sari.core.indexer.main._is_pid_alive", lambda _pid: False)
    _worker_build_snapshot(cfg.__dict__, snapshot, status_path, log_path, parent_pid=12345)

    import json
    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert "orphaned worker detected" in payload["error"]
