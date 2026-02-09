import os
import pytest
import urllib.parse
from pathlib import Path
from sari.mcp.tools.read_file import execute_read_file
from sari.core.db import LocalSearchDB
from sari.core.workspace import WorkspaceManager

def test_read_file_policy_index_hit(tmp_path):
    """
    Scenario: File is indexed but its parent is NOT in current 'roots'.
    Expectation: Should succeed because it's in the DB.
    """
    db_path = tmp_path / "index.db"
    db = LocalSearchDB(str(db_path))
    
    project_a = tmp_path / "project_a"
    project_a.mkdir()
    file_path = project_a / "secret.py"
    # Make sure we use the absolute resolved path as the key in DB
    abs_path = str(file_path.resolve())
    file_path.write_text("print('hello_world_success')")
    
    # Manually inject into DB tables (roots then files)
    rid = WorkspaceManager.root_id_for_workspace(str(project_a))
    
    conn = db.db.connection()
    # 1. Insert root first to satisfy FK
    conn.execute(
        "INSERT INTO roots (root_id, root_path, real_path, label) VALUES (?, ?, ?, ?)",
        (rid, str(project_a), str(project_a.resolve()), "project_a")
    )
    # 2. Insert file
    conn.execute(
        "INSERT INTO files (path, rel_path, root_id, mtime, size, content) VALUES (?, ?, ?, ?, ?, ?)",
        (abs_path, "secret.py", rid, 0, 100, "print('hello_world_success')")
    )
    
    # Request with empty roots
    result = execute_read_file({"path": abs_path}, db, roots=[])
    
    # Should succeed despite empty roots!
    assert "hello_world_success" in str(result)

def test_read_file_policy_registration_guidance(tmp_path):
    """
    Scenario: File is NOT indexed but exists on disk.
    Expectation: Should return guidance to add to roots.
    """
    db_path = tmp_path / "index.db"
    db = LocalSearchDB(str(db_path))
    
    project_b = tmp_path / "project_b"
    project_b.mkdir()
    file_path = project_b / "new.py"
    file_path.write_text("new content")
    
    # Request file that is NOT in DB
    result = execute_read_file({"path": str(file_path)}, db, roots=[])
    
    # Decode result text for assertion
    res_text = urllib.parse.unquote(str(result))
    assert "분석 범위(인덱스)에 포함되어 있지 않습니다" in res_text
    assert str(project_b) in res_text
    assert "roots" in res_text

def test_read_file_policy_not_found(tmp_path):
    """
    Scenario: File does not exist anywhere.
    Expectation: Should return standard not found error (NOT_INDEXED).
    """
    db_path = tmp_path / "index.db"
    db = LocalSearchDB(str(db_path))
    
    result = execute_read_file({"path": "/tmp/non_existent_file_999.py"}, db, roots=[])
    
    res_text = urllib.parse.unquote(str(result))
    assert "파일을 찾을 수 없습니다" in res_text
    assert "NOT_INDEXED" in res_text
