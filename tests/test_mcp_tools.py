import pytest
from pathlib import Path
from sari.mcp.tools.search import execute_search
from sari.mcp.tools.read_file import execute_read_file
from sari.mcp.tools.list_files import execute_list_files
from sari.mcp.tools.registry import ToolContext
from sari.core.db.main import LocalSearchDB
from sari.core.search_engine import SearchEngine
from sari.core.indexer.main import Indexer
from sari.core.config.main import Config
from sari.core.workspace import WorkspaceManager

@pytest.fixture
def tool_ctx(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.py").write_text("print('hello')")
    (ws / ".sariroot").touch() # Boundary marker
    
    # Calculate stable root_id
    root_id = WorkspaceManager.root_id(str(ws))
    
    db = LocalSearchDB(str(tmp_path / "tools.db"))
    db.upsert_root(root_id, str(ws), str(ws))
    
    cur = db._write.cursor()
    # 20 columns row with correct root_id
    rows = [(f"{root_id}/hello.py", "hello.py", root_id, "repo", 100, 14, b"print('hello')", "h1", "", 0, 0, "ok", "none", "none", "none", 0, 0, 0, 14, "{}")]
    db.upsert_files_tx(cur, rows)
    db._write.commit()
    
    cfg = Config.load(None, workspace_root_override=str(ws))
    
    return ToolContext(
        db=db,
        engine=SearchEngine(db),
        indexer=Indexer(cfg, db),
        roots=[str(ws)],
        cfg=cfg,
        logger=type('MockLogger', (), {'log_telemetry': lambda *a, **k: None})(),
        workspace_root=str(ws),
        server_version="test"
    )

def test_execute_search(tool_ctx):
    # Expect JSON for easier assertion in tests
    import os
    os.environ["SARI_FORMAT"] = "json"
    res = execute_search({"query": "hello"}, tool_ctx.db, tool_ctx.logger, tool_ctx.roots, engine=tool_ctx.engine)
    assert "hello.py" in str(res)

def test_execute_read_file(tool_ctx):
    import os
    os.environ["SARI_FORMAT"] = "json"
    # Use absolute path to let resolve_db_path work
    abs_path = str(Path(tool_ctx.workspace_root) / "hello.py")
    res = execute_read_file({"path": abs_path}, tool_ctx.db, tool_ctx.roots)
    assert "print('hello')" in str(res)

def test_execute_list_files(tool_ctx):
    import os
    os.environ["SARI_FORMAT"] = "json"
    res = execute_list_files({"repo": "repo"}, tool_ctx.db, tool_ctx.logger, tool_ctx.roots)
    assert "hello.py" in str(res)