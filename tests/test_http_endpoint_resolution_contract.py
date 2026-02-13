import json


def test_http_resolver_override_wins(monkeypatch, tmp_path):
    from sari.core.endpoint_resolver import resolve_http_endpoint

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("SARI_HTTP_API_HOST", "127.0.0.1")
    monkeypatch.setenv("SARI_HTTP_API_PORT", "60111")

    host, port = resolve_http_endpoint(
        workspace_root=str(workspace_root),
        host_override="127.0.0.1",
        port_override=60222,
    )
    assert host == "127.0.0.1"
    assert port == 60222


def test_http_resolver_env_over_registry(monkeypatch, tmp_path):
    from sari.core.endpoint_resolver import resolve_http_endpoint
    from sari.core.server_registry import ServerRegistry

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(tmp_path / "registry.json"))
    monkeypatch.setenv("SARI_HTTP_API_HOST", "127.0.0.1")
    monkeypatch.setenv("SARI_HTTP_API_PORT", "60333")

    registry = ServerRegistry()
    registry.register_daemon(
        "boot-a",
        "127.0.0.1",
        47779,
        12345,
        http_host="127.0.0.1",
        http_port=60444,
    )
    registry.set_workspace(str(workspace_root), "boot-a")
    monkeypatch.setattr(registry, "_is_process_alive", lambda pid: True)
    monkeypatch.setattr("sari.core.endpoint_resolver.ServerRegistry", lambda: registry)

    host, port = resolve_http_endpoint(workspace_root=str(workspace_root))
    assert host == "127.0.0.1"
    assert port == 60333


def test_http_resolver_default_when_no_registry_or_env(monkeypatch, tmp_path):
    from sari.core.endpoint_resolver import resolve_http_endpoint
    from sari.core.constants import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(tmp_path / "registry.json"))
    monkeypatch.delenv("SARI_HTTP_API_HOST", raising=False)
    monkeypatch.delenv("SARI_HTTP_HOST", raising=False)
    monkeypatch.delenv("SARI_HTTP_API_PORT", raising=False)
    monkeypatch.delenv("SARI_HTTP_PORT", raising=False)

    host, port = resolve_http_endpoint(workspace_root=str(workspace_root))
    assert host == DEFAULT_HTTP_HOST
    assert port == DEFAULT_HTTP_PORT


def test_load_server_info_ignores_legacy_when_strict_ssot_enabled(monkeypatch, tmp_path):
    from sari.mcp.cli.registry import load_server_info

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    data_dir = workspace_root / ".codex" / "tools" / "sari" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "server.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 62002}),
        encoding="utf-8",
    )

    monkeypatch.setenv("SARI_STRICT_SSOT", "1")
    info = load_server_info(str(workspace_root))
    assert info is None
