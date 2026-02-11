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


def test_shared_state_start_ensures_all_roots_and_requests_rescan(monkeypatch):
    called = {"ensure_roots": [], "rescan": 0}

    def _ensure_root(root_id, root_path):
        called["ensure_roots"].append((root_id, root_path))

    def _request_rescan():
        called["rescan"] += 1

    state = SimpleNamespace(
        db=SimpleNamespace(ensure_root=_ensure_root),
        indexer=SimpleNamespace(
            cfg=SimpleNamespace(workspace_roots=["/tmp/ws-a", "/tmp/ws-b"]),
            run_forever=lambda: None,
            request_rescan=_request_rescan,
        ),
        watcher=None,
        workspace_root="/tmp/ws-a",
        root_id="root-id-a",
    )

    SharedState.start(state)
    ensured_paths = [p for _rid, p in called["ensure_roots"]]
    assert "/tmp/ws-a" in ensured_paths
    assert "/tmp/ws-b" in ensured_paths
    assert called["rescan"] == 1


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
