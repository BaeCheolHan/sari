from types import SimpleNamespace
import time

from sari.mcp.workspace_registry import SharedState
from sari.mcp.workspace_registry import _resolve_workspace_roots_for_indexing
from sari.core.workspace_registry import Registry


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


def test_registry_get_or_create_normalizes_workspace_key(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    created = {"count": 0}

    class _FakeState:
        def __init__(self, workspace_root):
            created["count"] += 1
            self.workspace_root = workspace_root
            self.ref_count = 0
            self.persistent = False
            self.last_activity = time.time()

        def start(self):
            return None

        def touch(self):
            self.last_activity = time.time()

        def stop(self):
            return None

    monkeypatch.setattr("sari.core.workspace_registry.SharedState", _FakeState)

    reg = Registry()
    s1 = reg.get_or_create(str(ws), track_ref=False)
    s2 = reg.get_or_create(str(ws) + "/", track_ref=False)

    assert s1 is s2
    assert created["count"] == 1
    assert len(reg._sessions) == 1


def test_registry_reap_stale_refs_reaps_zero_ref_sessions():
    stopped = {"count": 0}
    now = time.time()
    stale = SimpleNamespace(
        persistent=False,
        ref_count=0,
        last_activity=now - 100,
        stop=lambda: stopped.__setitem__("count", stopped["count"] + 1),
    )
    reg = Registry()
    reg._sessions["/tmp/ws"] = stale

    reaped = reg.reap_stale_refs(max_idle_sec=10)

    assert reaped == 1
    assert stopped["count"] == 1
    assert not reg._sessions


def test_registry_get_or_create_force_baseline_requests_rescan_on_existing_session():
    called = {"rescan": 0}

    state = SimpleNamespace(
        ref_count=0,
        persistent=False,
        touch=lambda: None,
        indexer=SimpleNamespace(
            request_rescan=lambda: called.__setitem__("rescan", called["rescan"] + 1)
        ),
    )
    reg = Registry()
    reg._sessions["/tmp/ws"] = state

    out = reg.get_or_create("/tmp/ws", track_ref=False, force_baseline=True)

    assert out is state
    assert called["rescan"] == 1
