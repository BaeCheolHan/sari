from __future__ import annotations

from sari.mcp.server_method_registry import (
    build_dispatch_methods,
    resolve_root_entries,
)


def test_resolve_root_entries_formats_uri_and_name():
    entries = resolve_root_entries(["/tmp/ws", "/tmp/ws/src"])
    assert entries[0]["uri"] == "file:///tmp/ws"
    assert entries[0]["name"] == "ws"
    assert entries[1]["uri"] == "file:///tmp/ws/src"
    assert entries[1]["name"] == "src"


def test_build_dispatch_methods_contains_core_methods():
    methods = build_dispatch_methods(
        handle_initialize=lambda _p: {"ok": True},
        list_tools=lambda: [{"name": "status"}],
        list_roots=lambda: [{"uri": "file:///tmp/ws", "name": "ws"}],
        server_name="sari",
        server_version="1.0.0",
        workspace_root="/tmp/ws",
        pid=123,
    )

    assert methods["initialize"]({}) == {"ok": True}
    assert methods["tools/list"]({}) == {"tools": [{"name": "status"}]}
    assert methods["roots/list"]({}) == {"roots": [{"uri": "file:///tmp/ws", "name": "ws"}]}
    identify = methods["sari/identify"]({})
    assert identify["name"] == "sari"
    assert identify["version"] == "1.0.0"
    assert identify["workspaceRoot"] == "/tmp/ws"
    assert identify["pid"] == 123
