from sari.mcp.cli.http_client import get_http_host_port


def test_get_http_host_port_uses_resolved_workspace_http(monkeypatch):
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", "/tmp/ws")

    class _Cfg:
        http_api_host = "127.0.0.1"
        http_api_port = 47777

    monkeypatch.setattr("sari.mcp.cli.http_client.load_config", lambda *_: _Cfg())
    monkeypatch.setattr(
        "sari.mcp.cli.http_client.ServerRegistry.resolve_workspace_http",
        lambda self, ws: {"host": "127.0.0.1", "port": 61773, "boot_id": "boot-1"},
    )

    host, port = get_http_host_port()
    assert host == "127.0.0.1"
    assert port == 61773

