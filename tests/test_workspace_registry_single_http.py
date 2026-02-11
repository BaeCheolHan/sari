from types import SimpleNamespace

from sari.mcp.workspace_registry import SharedState
from sari.mcp.workspace_registry import _resolve_workspace_roots_for_indexing


def test_shared_state_start_does_not_start_http_server(monkeypatch):
    called = {"serve_forever": 0}

    def _fake_serve_forever(*args, **kwargs):
        called["serve_forever"] += 1
        return object(), 47777

    monkeypatch.setattr("sari.core.http_server.serve_forever", _fake_serve_forever)

    state = SimpleNamespace(
        db=SimpleNamespace(ensure_root=lambda root_id, root: None),
        indexer=SimpleNamespace(run_forever=lambda: None),
        watcher=None,
        workspace_root="/tmp/ws",
        root_id="root-id",
    )

    SharedState.start(state)
    assert called["serve_forever"] == 0


def test_resolve_workspace_roots_for_indexing_keeps_existing_roots(tmp_path):
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    ws.mkdir()
    other.mkdir()

    roots = _resolve_workspace_roots_for_indexing(str(ws), [str(other)])
    assert roots == [str(other), str(ws)]


def test_resolve_workspace_roots_for_indexing_drops_missing_paths(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    missing = str(tmp_path / "Document" / "study")

    roots = _resolve_workspace_roots_for_indexing(str(ws), [missing])
    assert roots == [str(ws)]
