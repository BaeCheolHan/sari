from unittest.mock import MagicMock
from urllib.parse import unquote

from sari.mcp.tools.list_symbols import execute_list_symbols
from sari.mcp.tools.search import execute_search
from sari.mcp.tools.search_api_endpoints import execute_search_api_endpoints
from sari.mcp.tools.search_symbols import execute_search_symbols
from sari.mcp.tools.registry import build_default_registry


def _pack_text(resp: dict) -> str:
    content = resp.get("content") or []
    if not content:
        return ""
    return unquote(str(content[0].get("text") or ""))


def test_search_requires_repo():
    resp = execute_search({"query": "AuthService"}, MagicMock(), MagicMock(), ["/tmp/ws"])
    text = _pack_text(resp)
    assert resp.get("isError") is True
    assert "code=INVALID_ARGS" in text
    assert "repo is required" in text


def test_search_symbols_requires_repo():
    resp = execute_search_symbols({"query": "AuthService"}, MagicMock(), MagicMock(), ["/tmp/ws"])
    text = _pack_text(resp)
    assert resp.get("isError") is True
    assert "code=INVALID_ARGS" in text
    assert "repo is required" in text


def test_list_symbols_requires_repo():
    resp = execute_list_symbols({"path": "src/app.py"}, MagicMock(), ["/tmp/ws"])
    text = _pack_text(resp)
    assert resp.get("isError") is True
    assert "code=INVALID_ARGS" in text
    assert "repo is required" in text


def test_search_api_endpoints_requires_repo():
    resp = execute_search_api_endpoints({"path": "/api/users"}, MagicMock(), ["/tmp/ws"])
    text = _pack_text(resp)
    assert resp.get("isError") is True
    assert "code=INVALID_ARGS" in text
    assert "repo is required" in text


def test_registry_schemas_require_repo_for_phase1_tools():
    reg = build_default_registry()
    tools = {t["name"]: t for t in reg.list_tools()}

    search_required = tools["search"]["inputSchema"].get("required", [])
    list_symbols_required = tools["list_symbols"]["inputSchema"].get("required", [])

    assert "repo" in search_required
    assert "repo" in list_symbols_required


def test_registry_read_requires_repo():
    reg = build_default_registry()
    read_tool = {t["name"]: t for t in reg.list_tools()}["read"]
    assert "repo" in read_tool["inputSchema"].get("required", [])
