import json

from sari.core.server_registry import ServerRegistry
from sari.mcp.cli.http_client import get_http_host_port


def test_http_endpoint_resolution_prefers_registry_over_legacy_server_json(monkeypatch, tmp_path):
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    data_dir = workspace_root / ".codex" / "tools" / "sari" / "data"
    data_dir.mkdir(parents=True)
    # Legacy endpoint intentionally conflicts with registry.
    (data_dir / "server.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 62002}),
        encoding="utf-8",
    )

    registry_path = tmp_path / "registry.json"
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("SARI_WORKSPACE_ROOT", str(workspace_root))

    registry = ServerRegistry()
    registry.register_daemon(
        "boot-1",
        "127.0.0.1",
        47779,
        12345,
        http_host="127.0.0.1",
        http_port=61001,
    )
    registry.set_workspace(str(workspace_root), "boot-1")
    monkeypatch.setattr(registry, "_is_process_alive", lambda pid: True)
    monkeypatch.setattr(
        "sari.core.endpoint_resolver.ServerRegistry",
        lambda: registry,
    )
    monkeypatch.setattr(
        "sari.mcp.cli.registry.ServerRegistry",
        lambda: registry,
    )

    host, port = get_http_host_port()

    assert host == "127.0.0.1"
    assert port == 61001


def test_load_server_info_keeps_legacy_when_registry_missing(monkeypatch, tmp_path):
    from sari.mcp.cli.registry import load_server_info

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    data_dir = workspace_root / ".codex" / "tools" / "sari" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "server.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 62002}),
        encoding="utf-8",
    )

    monkeypatch.setenv("SARI_REGISTRY_FILE", str(tmp_path / "registry.json"))

    info = load_server_info(str(workspace_root))
    assert info is not None
    assert info.get("port") == 62002


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
