import json
from sari.mcp.tools._util import (
    pack_encode_text, pack_encode_id, pack_header, pack_line, 
    pack_error, mcp_response, ErrorCode,
    resolve_db_path, resolve_fs_path, parse_search_options
)

def test_pack_encoders():
    assert pack_encode_text("hello world") == "hello%20world"
    assert pack_encode_id("path/to/file.py") == "path/to/file.py"
    assert pack_encode_id("id with space") == "id%20with%20space"

def test_pack_builders():
    header = pack_header("test_tool", {"k1": "v1"}, returned=5)
    assert "PACK1 tool=test_tool ok=true k1=v1 returned=5" in header
    
    line = pack_line("r", {"a": "1", "b": "2"})
    assert line == "r:a=1 b=2"
    
    single = pack_line("m", single_value="val")
    assert single == "m:val"
    
    err = pack_error("tool", ErrorCode.INVALID_ARGS, "msg", hints=["hint1"])
    assert "PACK1 tool=tool ok=false code=INVALID_ARGS msg=msg" in err
    assert "hint=hint1" in err

def test_mcp_response_pack(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    resp = mcp_response(
        "tool",
        lambda: "PACK_OUTPUT",
        lambda: {"json": "output"}
    )
    assert resp["content"][0]["text"] == "PACK_OUTPUT"


def test_mcp_response_pack_detects_error_with_leading_whitespace(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    resp = mcp_response(
        "tool",
        lambda: "  PACK1 tool=tool ok=false code=INTERNAL msg=boom",
        lambda: {"json": "output"},
    )
    assert resp["isError"] is True


def test_mcp_response_json(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_RESPONSE_COMPACT", "0")
    resp = mcp_response(
        "tool",
        lambda: "PACK_OUTPUT",
        lambda: {"key": "val"}
    )
    text = resp["content"][0]["text"]
    assert json.loads(text)["key"] == "val"
    assert resp["key"] == "val"


def test_resolve_db_path_blocks_traversal():
    roots = ["/tmp/ws"]
    rid = __import__("sari.core.workspace", fromlist=["WorkspaceManager"]).WorkspaceManager.root_id("/tmp/ws")
    assert resolve_db_path(f"{rid}/../../etc/passwd", roots) is None


def test_resolve_fs_path_blocks_traversal():
    roots = ["/tmp/ws"]
    rid = __import__("sari.core.workspace", fromlist=["WorkspaceManager"]).WorkspaceManager.root_id("/tmp/ws")
    assert resolve_fs_path(f"{rid}/../../etc/passwd", roots) is None


def test_parse_search_options_normalizes_scalar_filters_and_boolean_strings():
    opts = parse_search_options(
        {
            "query": " hello ",
            "file_types": "py",
            "exclude_patterns": "*.min.js",
            "recency_boost": "false",
            "use_regex": "true",
            "case_sensitive": "0",
        },
        roots=[],
    )
    assert opts.query == "hello"
    assert opts.file_types == ["py"]
    assert opts.exclude_patterns == ["*.min.js"]
    assert opts.recency_boost is False
    assert opts.use_regex is True
    assert opts.case_sensitive is False
