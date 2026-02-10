from types import SimpleNamespace
from unittest.mock import patch

from sari.mcp.daemon import SariDaemon
from sari.mcp.workspace_registry import Registry


def test_registry_get_or_create_track_ref_false_does_not_increment_ref():
    reg = Registry()
    state = SimpleNamespace(ref_count=0, persistent=False, touch=lambda: None)
    reg._sessions["/tmp/ws"] = state

    out = reg.get_or_create("/tmp/ws", track_ref=False)

    assert out is state
    assert state.ref_count == 0


def test_daemon_autostart_workspace_is_not_persistent_and_not_tracked(monkeypatch):
    daemon = SariDaemon(host="127.0.0.1", port=49998)

    mock_registry = SimpleNamespace(
        get_or_create=lambda *args, **kwargs: None,
    )

    calls = {}

    def _fake_get_or_create(workspace_root, persistent=False, track_ref=True):
        calls["workspace_root"] = workspace_root
        calls["persistent"] = persistent
        calls["track_ref"] = track_ref
        return SimpleNamespace(workspace_root=workspace_root)

    mock_registry.get_or_create = _fake_get_or_create

    monkeypatch.setattr("sari.mcp.daemon.settings.DAEMON_AUTOSTART", True)
    monkeypatch.setattr("sari.mcp.daemon.settings.WORKSPACE_ROOT", "/tmp/ws")

    with patch("sari.mcp.workspace_registry.Registry.get_instance", return_value=mock_registry):
        with patch.object(daemon, "_start_http_gateway", return_value=None):
            with patch.object(daemon._registry, "set_workspace", return_value=None):
                daemon._autostart_workspace()

    assert calls["workspace_root"] == "/tmp/ws"
    assert calls["persistent"] is False
    assert calls["track_ref"] is False
