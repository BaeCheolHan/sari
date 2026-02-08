import pytest
from unittest.mock import MagicMock
from sari.mcp.tools import execute_list_files, execute_status
from sari.core.db.main import LocalSearchDB
from sari.core.workspace import WorkspaceManager

@pytest.fixture
def mcp_context(tmp_path):
    db = LocalSearchDB(str(tmp_path / "mcp.db"))
    # Use stable root_id derived from the root path
    rid = WorkspaceManager.root_id_for_workspace("root1")
    
    # Add dummy root entry first
    from sari.core.db.models import Root
    with db.db.atomic():
        Root.create(root_id=rid, root_path="root1", real_path="root1", label="repo1")
        
    # Add dummy roots/files
    db.upsert_files_turbo([(rid + "/main.py", "main.py", rid, "repo1", 0, 10, b"data", "h", "fts", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}")])
    db.finalize_turbo_batch()
    return {"db": db, "logger": MagicMock(), "roots": ["root1"]}

def test_mcp_list_files_truth(mcp_context):
    """
    Verify that MCP list_files tool actually sees the files in the new Turbo DB.
    """
    db, roots = mcp_context["db"], mcp_context["roots"]
    resp = execute_list_files({"limit": 10}, db, mcp_context["logger"], roots)
    
    text = resp["content"][0]["text"]
    assert "ok=true" in text
    assert "main.py" in text

def test_mcp_status_reporting_truth(mcp_context):
    """
    Verify that status reporting reflects the modernized Indexer and DB.
    """
    db, roots = mcp_context["db"], mcp_context["roots"]
    indexer = MagicMock()
    indexer.status.index_ready = True
    indexer.status.indexed_files = 1
    
    cfg = MagicMock()
    cfg.http_api_port = 48000
    cfg.workspace_roots = roots
    
    resp = execute_status({"details": True}, indexer, db, cfg, "root1", "2.5.0-turbo", mcp_context["logger"])
    text = resp["content"][0]["text"]
    
    assert "ok=true" in text
    assert "m:index_ready=true" in text
