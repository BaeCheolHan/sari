from __future__ import annotations

from types import SimpleNamespace

import pytest

from sari.mcp.server_tool_runtime import (
    ensure_connection_id,
    resolve_tool_runtime,
)


def test_ensure_connection_id_overwrites_spoofed_value():
    args = {"query": "x", "connection_id": "spoofed"}
    out = ensure_connection_id(args, "server-123")
    assert out["connection_id"] == "server-123"
    assert out["query"] == "x"


def test_resolve_tool_runtime_uses_injected_handles_without_session():
    cfg = SimpleNamespace(workspace_roots=["/tmp/ws"])
    db = object()
    indexer = object()

    runtime = resolve_tool_runtime(
        injected_cfg=cfg,
        injected_db=db,
        injected_indexer=indexer,
        session=None,
        registry=SimpleNamespace(get_or_create=lambda _ws: None),
        workspace_root="/tmp/ws",
    )
    assert runtime.db is db
    assert runtime.indexer is indexer
    assert runtime.roots == ["/tmp/ws"]
    assert runtime.session is None
    assert runtime.session_acquired is False


def test_resolve_tool_runtime_loads_session_and_roots_from_registry():
    sess = SimpleNamespace(
        db=object(),
        indexer=object(),
        config_data={"workspace_roots": ["/tmp/ws-a", "/tmp/ws-b"]},
    )
    registry = SimpleNamespace(get_or_create=lambda _ws: sess)
    runtime = resolve_tool_runtime(
        injected_cfg=None,
        injected_db=None,
        injected_indexer=None,
        session=None,
        registry=registry,
        workspace_root="/tmp/ws",
    )
    assert runtime.db is sess.db
    assert runtime.indexer is sess.indexer
    assert runtime.roots == ["/tmp/ws-a", "/tmp/ws-b"]
    assert runtime.session is sess
    assert runtime.session_acquired is True


def test_resolve_tool_runtime_raises_when_session_db_missing():
    sess = SimpleNamespace(db=None, indexer=object(), config_data={"workspace_roots": ["/tmp/ws"]})
    registry = SimpleNamespace(get_or_create=lambda _ws: sess)
    with pytest.raises(RuntimeError):
        resolve_tool_runtime(
            injected_cfg=None,
            injected_db=None,
            injected_indexer=None,
            session=None,
            registry=registry,
            workspace_root="/tmp/ws",
            error_builder=lambda msg: RuntimeError(msg),
        )
