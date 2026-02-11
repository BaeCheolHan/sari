from unittest.mock import patch
import json

import sari.main as main_mod


def test_main_stdio_uses_proxy(monkeypatch):
    with patch.object(main_mod, "validate_config_file", return_value=None):
        with patch.object(main_mod.WorkspaceManager, "resolve_config_path", return_value="/tmp/fake-config.json"):
            with patch("sari.mcp.proxy.main") as proxy_main:
                with patch("sari.mcp.server.LocalSearchMCPServer") as server_cls:
                    rc = main_mod.main(["--transport", "stdio", "--format", "pack"])
                    assert rc == 0
                    proxy_main.assert_called_once()
                    server_cls.assert_not_called()


def test_main_http_transport_routes_http_server():
    with patch.object(main_mod, "validate_config_file", return_value=None):
        with patch.object(main_mod.WorkspaceManager, "resolve_config_path", return_value="/tmp/fake-config.json"):
            with patch.object(main_mod, "_run_http_server", return_value=17) as run_http:
                with patch.object(main_mod, "_should_http_daemon", return_value=False):
                    rc = main_mod.main(["--transport", "http"])
                    assert rc == 17
                    run_http.assert_called_once()


def test_main_http_daemon_routes_spawn():
    with patch.object(main_mod, "validate_config_file", return_value=None):
        with patch.object(main_mod.WorkspaceManager, "resolve_config_path", return_value="/tmp/fake-config.json"):
            with patch.object(main_mod, "_spawn_http_daemon", return_value=23) as spawn_http:
                with patch.object(main_mod, "_should_http_daemon", return_value=True):
                    rc = main_mod.main(["--transport", "http", "--http-daemon"])
                    assert rc == 23
                    spawn_http.assert_called_once()


def test_mcp_entrypoint_delegates_to_sari_main(monkeypatch):
    import sari.mcp.__main__ as mcp_main

    captured = {}

    def _fake_sari_main(argv=None, original_stdout=None):
        captured["argv"] = list(argv or [])
        return 31

    monkeypatch.setattr("sys.argv", ["sari.mcp", "--transport", "http"])
    monkeypatch.setattr("sari.main.main", _fake_sari_main)

    rc = mcp_main.main()
    assert rc == 31
    assert captured["argv"] == ["--transport", "http"]


def test_cmd_roots_add_rejects_missing_directory(tmp_path, capsys):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    missing = str(tmp_path / "Document" / "study")

    with patch.object(main_mod.WorkspaceManager, "resolve_config_path", return_value=str(cfg_path)):
        rc = main_mod._cmd_roots_add(missing)

    assert rc == 2
    err = capsys.readouterr().err
    assert "Root path does not exist" in err


def test_cmd_roots_add_stores_normalized_existing_path(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    root = tmp_path / "study"
    root.mkdir()

    with patch.object(main_mod.WorkspaceManager, "resolve_config_path", return_value=str(cfg_path)):
        rc = main_mod._cmd_roots_add(str(root))

    assert rc == 0
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["roots"] == [str(root)]
