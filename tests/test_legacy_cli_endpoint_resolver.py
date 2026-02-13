from sari.mcp.cli import legacy_cli


def test_legacy_cli_get_http_host_port_delegates_to_core_resolver(monkeypatch):
    called = {"ok": False}

    def _fake_resolver(workspace_root=None, host_override=None, port_override=None):
        called["ok"] = True
        assert workspace_root is not None
        assert host_override == "127.0.0.1"
        assert port_override == 60001
        return "127.0.0.1", 60001

    monkeypatch.setenv("SARI_WORKSPACE_ROOT", "/tmp/ws")
    monkeypatch.setattr("sari.mcp.cli.compat_cli.resolve_http_endpoint", _fake_resolver)

    host, port = legacy_cli._get_http_host_port("127.0.0.1", 60001)
    assert called["ok"] is True
    assert host == "127.0.0.1"
    assert port == 60001
