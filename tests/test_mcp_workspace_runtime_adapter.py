from types import SimpleNamespace
from unittest.mock import MagicMock

from sari.mcp.adapters.workspace_runtime import RegistryWorkspaceRuntime
from sari.mcp.server import LocalSearchMCPServer
from sari.mcp.session import Session


def test_registry_workspace_runtime_delegates_calls():
    fake_registry = SimpleNamespace(
        get_or_create=MagicMock(return_value=SimpleNamespace(db=object(), indexer=object(), config_data={})),
        touch_workspace=MagicMock(),
        release=MagicMock(),
    )
    runtime = RegistryWorkspaceRuntime(registry=fake_registry)

    state = runtime.get_or_create("/tmp/ws", persistent=True, track_ref=False)
    runtime.touch_workspace("/tmp/ws")
    runtime.release("/tmp/ws")

    assert state is not None
    fake_registry.get_or_create.assert_called_once_with("/tmp/ws", persistent=True, track_ref=False)
    fake_registry.touch_workspace.assert_called_once_with("/tmp/ws")
    fake_registry.release.assert_called_once_with("/tmp/ws")


def test_server_uses_injected_workspace_runtime_for_session_acquire():
    fake_db = MagicMock()
    fake_db.engine = object()
    fake_state = SimpleNamespace(db=fake_db, indexer=MagicMock(), config_data={"workspace_roots": ["/tmp/ws"]})
    fake_runtime = SimpleNamespace(
        get_or_create=MagicMock(return_value=fake_state),
        touch_workspace=MagicMock(),
        release=MagicMock(),
    )

    server = LocalSearchMCPServer("/tmp/ws", workspace_runtime=fake_runtime, start_worker=False)
    server._middlewares = []
    server._tool_registry.execute = lambda _name, _ctx, args: {"ok": True, "args": args}

    result = server.handle_tools_call({"name": "search", "arguments": {"query": "abc"}})

    assert result["ok"] is True
    fake_runtime.get_or_create.assert_called_once_with("/tmp/ws")
    server.shutdown()


def test_session_uses_injected_workspace_runtime_for_cleanup():
    fake_runtime = SimpleNamespace(
        get_or_create=MagicMock(),
        touch_workspace=MagicMock(),
        release=MagicMock(),
    )
    reader = MagicMock()
    writer = MagicMock()
    session = Session(reader, writer, workspace_runtime=fake_runtime)
    session.workspace_root = "/tmp/ws"

    session.cleanup()

    fake_runtime.release.assert_called_once_with("/tmp/ws")
