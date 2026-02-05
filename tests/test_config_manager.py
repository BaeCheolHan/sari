import json
import pytest
import os
from pathlib import Path
from sari.core.config.manager import ConfigManager

@pytest.fixture
def temp_workspace(tmp_path):
    """Creates a mock workspace with various markers."""
    ws = tmp_path / "my-project"
    ws.mkdir()
    return ws

def test_profile_detection(temp_workspace):
    # 1. Create a Python project marker
    (temp_workspace / "pyproject.toml").touch()
    
    manager = ConfigManager(str(temp_workspace))
    detected = manager.detect_profiles()
    
    assert "python" in detected
    assert "core" in detected
    assert "web" not in detected

def test_sariignore_respect(temp_workspace):
    # 1. Create a marker inside an ignored directory
    ignored_dir = temp_workspace / "node_modules"
    ignored_dir.mkdir()
    (ignored_dir / "package.json").touch()
    
    # 2. Add .sariignore
    (temp_workspace / ".sariignore").write_text("node_modules/")
    
    manager = ConfigManager(str(temp_workspace))
    detected = manager.detect_profiles()
    
    # 'web' should NOT be detected because package.json is in an ignored dir
    assert "web" not in detected

def test_layered_merge_order(temp_workspace, monkeypatch):
    # Mock global config via settings
    global_dir = temp_workspace / "global_config"
    global_dir.mkdir()
    
    # We need to patch the instance property or use a mock
    monkeypatch.setattr("sari.core.config.manager.settings", type('MockSettings', (), {
        'GLOBAL_CONFIG_DIR': str(global_dir),
        'WORKSPACE_CONFIG_DIR_NAME': '.sari'
    }))
    
    # 1. Global config: add an extension
    (global_dir / "config.json").write_text(json.dumps({
        "include_add": [".xyz"]
    }))
    
    # 2. Workspace config: remove an extension
    ws_sari = temp_workspace / ".sari"
    ws_sari.mkdir()
    (ws_sari / "config.json").write_text(json.dumps({
        "include_remove": [".xyz"]
    }))
    
    manager = ConfigManager(str(temp_workspace))
    final_config = manager.resolve_final_config()
    
    # Step 6 (Remove) should override Step 5 (Add)
    assert ".xyz" not in final_config["final_extensions"]

def test_sariroot_boundary(temp_workspace):
    # Create a nested structure
    sub_dir = temp_workspace / "sub"
    sub_dir.mkdir()
    (sub_dir / ".sariroot").touch()
    
    manager = ConfigManager(str(sub_dir))
    assert manager.is_project_root() is True
