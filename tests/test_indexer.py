import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from sari.core.indexer.main import Indexer

@pytest.fixture
def mock_indexer(tmp_path):
    cfg = MagicMock()
    # Set attributes BEFORE Indexer creation
    cfg.workspace_roots = [str(tmp_path)]
    cfg.include_ext = [".py", ".js"]
    cfg.include_files = []
    cfg.exclude_dirs = []
    cfg.exclude_globs = []
    
    db = MagicMock()
    mock_settings = MagicMock()
    mock_settings.FOLLOW_SYMLINKS = False
    mock_settings.INDEX_MEM_MB = 1024
    mock_settings.INDEX_WORKERS = 2
    mock_settings.MAX_DEPTH = 30
    mock_settings.get_int.side_effect = lambda key, default: default
    mock_settings.WATCHER_MONITOR_SECONDS = 10
    
    # Scanner uses cfg.settings or global_settings
    cfg.settings = mock_settings
    cfg.max_depth = 30
    
    with patch('sari.core.db.storage.GlobalStorageManager.get_instance') as mock_get_storage:
        mock_storage = MagicMock()
        mock_get_storage.return_value = mock_storage
        indexer = Indexer(cfg, db, settings_obj=mock_settings)
        indexer.storage = mock_storage
        return indexer

def test_indexer_init(mock_indexer):
    assert mock_indexer.status.index_ready is False

def test_indexer_scan_once(mock_indexer, tmp_path):
    (tmp_path / "file1.py").write_text("print(1)")
    mock_indexer.scan_once()
    tasks = []
    while True:
        task = mock_indexer.coordinator.get_next_task()
        if not task: break
        tasks.append(task)
    assert len(tasks) >= 1

def test_indexer_handle_task(mock_indexer, tmp_path):
    root = tmp_path.absolute()
    path = root / "test.py"
    path.write_text("def hello(): pass")
    from sari.core.workspace import WorkspaceManager
    root_id = WorkspaceManager.root_id(str(root))
    
    task = {"kind": "scan_file", "root": root, "path": path, "st": path.stat(), "scan_ts": 1000, "excluded": False}
    mock_indexer.worker.process_file_task = MagicMock(return_value={
        "type": "changed", "rel": f"{root_id}/test.py", "repo": "repo1",
        "mtime": 100, "size": 50, "content": "def hello(): pass",
        "parse_status": "ok", "parse_reason": "", "ast_status": "ok", "ast_reason": "",
        "is_binary": False, "is_minified": False, "symbols": []
    })
    
    mock_indexer._handle_task(root_id, task)
    assert root_id in mock_indexer._l1_buffer
    assert mock_indexer.status.indexed_files == 1

def test_indexer_l1_flush(mock_indexer, tmp_path):
    root = tmp_path.absolute()
    from sari.core.workspace import WorkspaceManager
    root_id = WorkspaceManager.root_id(str(root))
    mock_indexer._l1_max_size = 2
    
    # Pre-populate to avoid KeyError
    mock_indexer._l1_buffer[root_id] = []
    mock_indexer._l1_docs[root_id] = []
    mock_indexer._l1_syms[root_id] = []
    
    def add_file(name):
        path = root / name
        path.write_text("content")
        mock_indexer.worker.process_file_task = MagicMock(return_value={
            "type": "changed", "rel": f"{root_id}/{name}", "repo": "repo",
            "mtime": 100, "size": 7, "content": "content",
            "parse_status": "ok", "parse_reason": "", "ast_status": "none", "ast_reason": "",
            "is_binary": False, "is_minified": False
        })
        mock_indexer._handle_task(root_id, {"kind": "scan_file", "root": root, "path": path, "st": path.stat(), "scan_ts": 100, "excluded": False})

    add_file("f1.py")
    add_file("f2.py") 
    assert mock_indexer.storage.upsert_files.called
