from sari.core.server_registry import ServerRegistry


def test_resolve_workspace_http_prefers_daemon_endpoint(tmp_path, monkeypatch):
    reg_file = tmp_path / "server.json"
    monkeypatch.setenv("SARI_REGISTRY_FILE", str(reg_file))

    reg = ServerRegistry()
    reg.register_daemon(
        "boot-1",
        "127.0.0.1",
        47779,
        12345,
        version="0.6.11",
        http_host="127.0.0.1",
        http_port=61773,
    )

    # Make process-alive check deterministic in test.
    monkeypatch.setattr(reg, "_is_process_alive", lambda pid: True)
    reg.set_workspace("/tmp/ws", "boot-1")

    info = reg.resolve_workspace_http("/tmp/ws")
    assert info is not None
    assert info["host"] == "127.0.0.1"
    assert info["port"] == 61773
    assert info["boot_id"] == "boot-1"

