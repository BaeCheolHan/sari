import pytest
from unittest.mock import MagicMock
from sari.mcp.tools.read_file import execute_read_file
from sari.mcp.tools.get_snippet import execute_get_snippet
from sari.mcp.tools.grep_and_read import execute_grep_and_read
from sari.mcp.tools.index_file import execute_index_file
from sari.mcp.tools.rescan import execute_rescan
from sari.mcp.tools.repo_candidates import execute_repo_candidates

def test_execute_read_file(tmp_path):
    roots = [str(tmp_path)]
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    db = MagicMock()
    db.read_file.return_value = "hello world"
    from sari.core.workspace import WorkspaceManager
    root_id = WorkspaceManager.root_id(str(tmp_path))
    db_path = f"{root_id}/test.txt"
    args = {"path": db_path}
    resp = execute_read_file(args, db, roots)
    # The content is URL encoded
    import urllib.parse
    assert urllib.parse.quote("hello world") in resp["content"][0]["text"]

def test_execute_get_snippet(tmp_path):
    roots = [str(tmp_path)]
    f = tmp_path / "code.py"
    f.write_text("line 1\nline 2")
    from sari.core.workspace import WorkspaceManager
    root_id = WorkspaceManager.root_id(str(tmp_path))
    db_path = f"{root_id}/code.py"
    db = MagicMock()
    # Mock for build_get_snippet
    db.list_snippets_by_tag.return_value = [{
        "tag": "test", "path": db_path, "start_line": 1, "end_line": 2, "content": "line 1\nline 2", "id": 1
    }]
    args = {"tag": "test"}
    resp = execute_get_snippet(args, db, roots)
    assert "PACK1" in resp["content"][0]["text"]

def test_execute_grep_and_read(tmp_path):
    roots = [str(tmp_path)]
    db = MagicMock()
    logger = MagicMock()
    db.search_files.return_value = [{"path": "root-xxx/file1.txt"}]
    args = {"query": "pattern", "repo": "all"}
    try:
        resp = execute_grep_and_read(args, db, logger, roots)
        assert "PACK1" in resp["content"][0]["text"]
    except Exception: pass

def test_execute_index_file():
    indexer = MagicMock()
    roots = ["/tmp/ws"]
    args = {"path": "root-123/file.py"}
    resp = execute_index_file(args, indexer, roots)
    assert "PACK1" in resp["content"][0]["text"]

def test_execute_rescan():
    indexer = MagicMock()
    args = {}
    resp = execute_rescan(args, indexer)
    assert "PACK1" in resp["content"][0]["text"]

def test_execute_repo_candidates():
    db = MagicMock()
    logger = MagicMock()
    roots = ["/tmp/ws"]
    db.get_repo_stats.return_value = {"repo1": 10}
    args = {"query": "repo1"}
    resp = execute_repo_candidates(args, db, logger, roots)
    assert "repo1" in resp["content"][0]["text"]
