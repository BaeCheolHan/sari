import os
from unittest.mock import patch

import sari.main as main_mod


def test_main_stdio_triggers_http_ensure(monkeypatch):
    with patch.object(main_mod, "validate_config_file", return_value=None):
        with patch.object(main_mod.WorkspaceManager, "resolve_config_path", return_value="/tmp/fake-config.json"):
            with patch.object(main_mod, "_ensure_http_daemon_for_stdio") as ensure_http:
                with patch("sari.mcp.server.LocalSearchMCPServer") as server_cls:
                    rc = main_mod.main(["--transport", "stdio", "--format", "pack"])
                    assert rc == 0
                    ensure_http.assert_called_once()
                    server_cls.return_value.run.assert_called_once()


def test_ensure_http_daemon_for_stdio_uses_daemon_start():
    with patch("sari.mcp.cli._get_http_host_port", return_value=("127.0.0.1", 47777)):
        with patch("sari.mcp.cli._is_http_running", return_value=False):
            with patch("sari.mcp.cli._start_daemon_background", return_value=True) as start_bg:
                with patch.object(main_mod, "_spawn_http_daemon") as fallback_spawn:
                    ns = type("Ns", (), {"http_api_port": None})()
                    with patch.dict(os.environ, {"SARI_ENABLE_HTTP_DAEMON_FOR_STDIO": "1"}):
                        main_mod._ensure_http_daemon_for_stdio(ns)
                    start_bg.assert_called_once()
                    fallback_spawn.assert_not_called()
