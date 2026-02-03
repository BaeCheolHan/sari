import types

import mcp.registry as registry_mod


def test_shared_state_init_and_shutdown(monkeypatch):
    calls = {"init": 0, "shutdown": 0}

    class DummyServer:
        SERVER_VERSION = "1"
        def __init__(self, root):
            self.cfg = types.SimpleNamespace(server_host="127.0.0.1", server_port=0)
            self.db = object()
            self.indexer = object()
        def _ensure_initialized(self):
            calls["init"] += 1
        def shutdown(self):
            calls["shutdown"] += 1

    class DummyHTTPD:
        def __init__(self):
            self.closed = False
        def shutdown(self):
            self.closed = True
        def server_close(self):
            self.closed = True

    monkeypatch.setattr(registry_mod, "serve_forever", lambda **kwargs: (DummyHTTPD(), 1234))
    import mcp.server as server_mod
    monkeypatch.setattr(server_mod, "LocalSearchMCPServer", DummyServer)

    class DummyRegistry:
        def unregister(self, _root):
            calls["unreg"] = True

    monkeypatch.setattr(registry_mod, "ServerRegistry", lambda: DummyRegistry())

    state = registry_mod.SharedState("/tmp")
    assert calls["init"] == 1
    state.acquire()
    assert state.ref_count == 1
    state.release()
    assert state.ref_count == 0
    state.shutdown()
    assert calls["shutdown"] == 1


def test_shared_state_init_failure(monkeypatch):
    class DummyServer:
        SERVER_VERSION = "1"
        def __init__(self, root):
            self.cfg = types.SimpleNamespace(server_host="127.0.0.1", server_port=0)
            self.db = object()
            self.indexer = object()
        def _ensure_initialized(self):
            raise RuntimeError("boom")
        def shutdown(self):
            return None

    import mcp.server as server_mod
    monkeypatch.setattr(server_mod, "LocalSearchMCPServer", DummyServer)
    monkeypatch.setattr(registry_mod, "serve_forever", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail")))
    state = registry_mod.SharedState("/tmp")
    assert state.workspace_root == "/tmp"


def test_registry_lifecycle(monkeypatch, tmp_path):
    class DummyState:
        def __init__(self, root):
            self.workspace_root = root
            self.ref_count = 0
        def acquire(self):
            self.ref_count += 1
            return self.ref_count
        def release(self):
            self.ref_count -= 1
            return self.ref_count
        def shutdown(self):
            self.closed = True

    monkeypatch.setattr(registry_mod, "SharedState", DummyState)
    reg = registry_mod.Registry()
    root = str(tmp_path / "ws")
    state = reg.get_or_create(root)
    assert state.ref_count == 1
    assert reg.active_count() == 1
    assert reg.get(root) is state
    assert reg.list_workspaces()[str((tmp_path / "ws").resolve())] == 1

    reg.release(root)
    assert reg.active_count() == 0

    reg.release(root)
    reg.shutdown_all()


def test_registry_singleton_and_reset(monkeypatch):
    class DummyState:
        def __init__(self, root):
            self.workspace_root = root
            self.ref_count = 0
        def acquire(self):
            self.ref_count += 1
            return self.ref_count
        def release(self):
            self.ref_count -= 1
            return self.ref_count
        def shutdown(self):
            return None

    monkeypatch.setattr(registry_mod, "SharedState", DummyState)
    inst1 = registry_mod.Registry.get_instance()
    inst2 = registry_mod.Registry.get_instance()
    assert inst1 is inst2
    registry_mod.Registry.reset_instance()
    inst3 = registry_mod.Registry.get_instance()
    assert inst3 is not inst1
