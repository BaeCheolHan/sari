import pytest
import os
import json
from sari.core.server_registry import ServerRegistry

@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg_file = tmp_path / "registry.json"
    # ServerRegistry uses SARI_REGISTRY_FILE env var to determine path
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(reg_file))
    return ServerRegistry()

def test_register_daemon(registry):
    registry.register_daemon("boot-1", "127.0.0.1", 47779, 1234, version="1.0.0")
    data = registry._load()
    daemons = data.get("daemons", {})
    assert "boot-1" in daemons
    assert daemons["boot-1"]["port"] == 47779

def test_register_workspace(registry):
    # set_workspace is the correct method name in v2
    registry.set_workspace("/path/to/project", "boot-1", http_port=47777, http_host="127.0.0.1")
    data = registry._load()
    workspaces = data.get("workspaces", {})
    # Note: _normalize_workspace_root might resolve /path/to/project, so we check values
    found = False
    for ws_path, info in workspaces.items():
        if info["boot_id"] == "boot-1":
            found = True
            break
    assert found

def test_touch_and_cleanup(registry):
    registry.register_daemon("old-boot", "127.0.0.1", 1111, 555)
    # Manually set very old timestamp
    def _make_old(data):
        data["daemons"]["old-boot"]["last_seen_ts"] = 0
    registry._update(_make_old)
    
    # Prune happens automatically in get_daemon if process is dead or manually via _prune_dead_locked
    # Here we test if get_daemon unregisters dead process
    assert registry.get_daemon("old-boot") is None