import pytest
from unittest.mock import MagicMock
from sari.core.db import LocalSearchDB
from sari.core.workspace import WorkspaceManager
from sari.mcp.tools.call_graph import execute_call_graph
from sari.mcp.tools.doctor import execute_doctor

@pytest.fixture
def complex_tool_context(tmp_path):
    root_path = tmp_path / "complex_ws"
    root_path.mkdir()
    db_path = root_path / "sari.db"
    db = LocalSearchDB(str(db_path))
    rid = WorkspaceManager.root_id(str(root_path))
    db.upsert_root(rid, str(root_path), str(root_path.resolve()), label="complex")
    
    cur = db._write.cursor()
    # 20-column Standard File
    files = [
        (f"{rid}/a.py", "a.py", rid, "repo", 100, 10, b"a", "h1", "a", 1000, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}"),
        (f"{rid}/b.py", "b.py", rid, "repo", 100, 10, b"b", "h2", "b", 1000, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}")
    ]
    db.upsert_files_tx(cur, files)
    
    # 12-column Standard Symbol
    symbols = [
        ("sa", f"{rid}/a.py", rid, "a", "function", 1, 1, "def a()", "", "{}", "", "a"),
        ("sb", f"{rid}/b.py", rid, "b", "function", 1, 1, "def b()", "", "{}", "", "b") # Auto-stub will handle b.py
    ]
    db.upsert_symbols_tx(cur, symbols, root_id=rid)
    db._write.commit()
    return {"db": db, "roots": [str(root_path)], "root_id": rid, "path": root_path}

def test_call_graph_logic_integrity(complex_tool_context):
    db, roots = complex_tool_context["db"], complex_tool_context["roots"]
    resp = execute_call_graph({"symbol": "a", "depth": 2}, db, MagicMock(), roots)
    text = resp["content"][0]["text"]
    assert "PACK1 tool=call_graph ok=true" in text

def test_doctor_diagnostics_integrity(complex_tool_context):
    db, roots = complex_tool_context["db"], complex_tool_context["roots"]
    resp = execute_doctor({"include_network": False}, db, MagicMock(), roots)
    assert "PACK1 tool=doctor ok=true" in resp["content"][0]["text"]
