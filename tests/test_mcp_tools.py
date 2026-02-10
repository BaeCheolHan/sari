from unittest.mock import MagicMock
from sari.mcp.tools.search import execute_search
from sari.mcp.tools.list_files import execute_list_files
from sari.mcp.tools.status import execute_status
from sari.core.models import SearchHit

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


def test_execute_search_respects_result_and_snippet_caps():
    db = MagicMock()
    logger = MagicMock()
    roots = ["/tmp/ws"]
    long_snippet = "A" * 300
    hits = [
        SearchHit(repo="repo1", path="path1", score=1.0, snippet=long_snippet),
        SearchHit(repo="repo1", path="path2", score=0.9, snippet=long_snippet),
    ]
    db.search_v2.return_value = (hits, {"total": 2, "total_mode": "exact", "engine": "embedded"})

    args = {"query": "test", "limit": 10, "max_results": 1, "snippet_max_chars": 80}
    resp = execute_search(args, db, logger, roots)
    text = resp["content"][0]["text"]
    row_lines = [line for line in text.splitlines() if line.startswith("r:")]

    assert len(row_lines) == 1
    assert "path=path1" in row_lines[0]
    assert "A" * 100 not in row_lines[0]


def test_execute_search_respects_pack_budget():
    db = MagicMock()
    logger = MagicMock()
    roots = ["/tmp/ws"]
    big_snippet = "B" * 2000
    hits = [
        SearchHit(repo="repo1", path=f"path{i}", score=1.0, snippet=big_snippet)
        for i in range(10)
    ]
    db.search_v2.return_value = (hits, {"total": 10, "total_mode": "exact", "engine": "embedded"})

    args = {"query": "test", "limit": 10, "max_pack_bytes": 4096}
    resp = execute_search(args, db, logger, roots)
    text = resp["content"][0]["text"]

    assert "m:budget_bytes=4096" in text
    assert "m:truncated=maybe" in text


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


def test_execute_search_pack_error_sets_is_error_flag(monkeypatch):
    db = MagicMock()
    logger = MagicMock()
    roots = ["/tmp/ws"]
    monkeypatch.setenv("SARI_FORMAT", "pack")

    resp = execute_search({"query": ""}, db, logger, roots)
    text = resp["content"][0]["text"]
    assert "PACK1 tool=search ok=false" in text
    assert resp.get("isError") is True
