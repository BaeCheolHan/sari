from unittest.mock import patch

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
