from sari.core.endpoint_resolver import resolve_http_endpoint_for_daemon


def test_resolve_http_endpoint_for_daemon_uses_overrides(monkeypatch):
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", "/tmp/ws")
    host, port = resolve_http_endpoint_for_daemon(
        daemon_host="127.0.0.1",
        daemon_port=47779,
        host_override="127.0.0.1",
        port_override=61111,
    )
    assert host == "127.0.0.1"
    assert port == 61111


def test_resolve_http_endpoint_for_daemon_prefers_registry_http_fields(monkeypatch, tmp_path):
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(tmp_path / "registry.json"))

    from sari.core.server_registry import ServerRegistry

    reg = ServerRegistry()
    reg.register_daemon(
        "boot-1",
        "127.0.0.1",
        47779,
        12345,
        http_host="127.0.0.1",
        http_port=61222,
    )
    reg.set_workspace(str(workspace_root), "boot-1")
    monkeypatch.setattr(reg, "_is_process_alive", lambda pid: True)
    monkeypatch.setattr("sari.core.endpoint_resolver.ServerRegistry", lambda: reg)

    host, port = resolve_http_endpoint_for_daemon(
        daemon_host="127.0.0.1",
        daemon_port=47779,
        workspace_root=str(workspace_root),
    )
    assert host == "127.0.0.1"
    assert port == 61222
