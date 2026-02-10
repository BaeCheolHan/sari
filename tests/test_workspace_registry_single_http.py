from types import SimpleNamespace

from sari.mcp.workspace_registry import SharedState


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

