import pytest

from sari.mcp.cli.http_client import get_http_host_port, request_http


def test_get_http_host_port_uses_resolved_workspace_http(monkeypatch):
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", "/tmp/ws")
    monkeypatch.setattr(
        "sari.core.endpoint_resolver.ServerRegistry.resolve_workspace_http",
        lambda self, ws: {"host": "127.0.0.1", "port": 61773, "boot_id": "boot-1"},
    )

    host, port = get_http_host_port()
    assert host == "127.0.0.1"
    assert port == 61773


def test_request_http_rejects_non_absolute_path(monkeypatch):
    monkeypatch.setattr("sari.mcp.cli.http_client.get_http_host_port", lambda *_: ("127.0.0.1", 61773))
    with pytest.raises(RuntimeError, match="must start with '/'"):
        request_http("status", {}, "127.0.0.1", 61773)


def test_request_http_rejects_non_dict_params(monkeypatch):
    monkeypatch.setattr("sari.mcp.cli.http_client.get_http_host_port", lambda *_: ("127.0.0.1", 61773))
    with pytest.raises(RuntimeError, match="params must be an object"):
        request_http("/status", ["x"], "127.0.0.1", 61773)
