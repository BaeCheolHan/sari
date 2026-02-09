import pytest
import json
import time
from unittest.mock import MagicMock
from sari.mcp.tools.search import execute_search
from sari.mcp.tools.list_files import execute_list_files
from sari.mcp.tools.status import execute_status
from sari.core.models import SearchHit, SearchOptions

def test_execute_search_basic():
    db = MagicMock()
    logger = MagicMock()
    engine = MagicMock()
    roots = ["/tmp/ws"]
    hit = SearchHit(repo="repo1", path="path1", score=1.0, snippet="hi")
    
    # Mock db.search_v2 as the tool now calls it directly via Facade
    db.search_v2.return_value = ([hit], {"total": 1, "total_mode": "exact", "engine": "embedded"})
    
    args = {"query": "test", "limit": 10}
    resp = execute_search(args, db, logger, roots, engine=engine)
    text = resp["content"][0]["text"]
    assert "PACK1 tool=search ok=true" in text
    assert "r:path=path1 repo=repo1" in text

def test_execute_list_files_summary():
    db = MagicMock()
    logger = MagicMock()
    roots = ["/tmp/ws"]
    db.get_repo_stats.return_value = {"repo1": 10, "repo2": 5}
    args = {} # summary_only
    resp = execute_list_files(args, db, logger, roots)
    text = resp["content"][0]["text"]
    assert "PACK1 tool=list_files ok=true mode=summary" in text
    assert "r:repo=repo1 file_count=10" in text
    assert "r:repo=repo2 file_count=5" in text

def test_execute_list_files_detail():
    db = MagicMock()
    logger = MagicMock()
    roots = ["/tmp/ws"]
    db.list_files.return_value = [{"path": "file1", "repo": "repo1", "mtime": 100, "size": 10}]
    args = {"repo": "repo1", "limit": 10}
    resp = execute_list_files(args, db, logger, roots)
    text = resp["content"][0]["text"]
    assert "PACK1 tool=list_files ok=true" in text
    assert "f:path=file1" in text

def test_execute_status():
    indexer = MagicMock()
    indexer.status.index_ready = True
    indexer.status.scanned_files = 100
    indexer.status.indexed_files = 80
    indexer.status.errors = 0
    indexer.get_last_commit_ts.return_value = 12345
    
    db = MagicMock()
    db.fts_enabled = True
    db.count_failed_tasks.return_value = (0, 0)
    
    cfg = MagicMock()
    cfg.include_ext = [".py"]
    cfg.http_api_port = 47777
    cfg.workspace_roots = ["/tmp/ws"]
    
    logger = MagicMock()
    
    args = {"details": True}
    resp = execute_status(args, indexer, db, cfg, "/tmp/ws", "0.0.1", logger)
    text = resp["content"][0]["text"]
    assert "PACK1 tool=status ok=true" in text
    assert "m:index_ready=true" in text
    assert "m:scanned_files=100" in text
    assert "m:cfg_include_ext=.py" in text