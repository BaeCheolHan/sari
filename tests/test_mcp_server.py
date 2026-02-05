import json
import pytest
from sari.mcp.server import LocalSearchMCPServer

@pytest.fixture
def server(tmp_path):
    return LocalSearchMCPServer(str(tmp_path))

def test_handle_initialize(server):
    req = {
        "method": "initialize",
        "params": {"rootPath": server.workspace_root},
        "id": 1
    }
    resp = server.handle_request(req)
    assert resp["id"] == 1
    assert "protocolVersion" in resp["result"]

def test_handle_tools_list(server):
    req = {
        "method": "tools/list",
        "params": {},
        "id": 2
    }
    resp = server.handle_request(req)
    assert "tools" in resp["result"]
    assert len(resp["result"]["tools"]) > 0

def test_handle_ping(server):
    req = {"method": "ping", "params": {}, "id": 3}
    resp = server.handle_request(req)
    assert resp["id"] == 3
    assert resp["result"] == {}

def test_method_not_found(server):
    req = {"method": "invalid/method", "params": {}, "id": 4}
    resp = server.handle_request(req)
    assert "error" in resp
    assert resp["error"]["code"] == -32601
