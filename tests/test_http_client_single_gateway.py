from sari.mcp.cli.http_client import get_http_host_port


def test_get_http_host_port_uses_resolved_workspace_http(monkeypatch):
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", "/tmp/ws")
    monkeypatch.setattr(
        "sari.core.endpoint_resolver.ServerRegistry.resolve_workspace_http",
        lambda self, ws: {"host": "127.0.0.1", "port": 61773, "boot_id": "boot-1"},
    )

    host, port = get_http_host_port()
    assert host == "127.0.0.1"
    assert port == 61773
