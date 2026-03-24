from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from solidlsp.language_servers.taplo_server import TaploServer


def test_taplo_start_server_registers_workspace_request_handlers() -> None:
    server = object.__new__(TaploServer)
    server.repository_root_path = "/tmp/repo"
    server.server = MagicMock()
    server.server.send.initialize.return_value = {"capabilities": {}}
    handlers: dict[str, object] = {}

    def _on_request(name: str, fn) -> None:  # noqa: ANN001
        handlers[name] = fn

    server.server.on_request.side_effect = _on_request

    server._start_server()

    assert handlers["workspace/configuration"]({"items": [{"section": "toml"}, {"section": "taplo"}]}) == [{}, {}]
    assert handlers["workspace/workspaceFolders"]({}) == [
        {"uri": Path("/tmp/repo").as_uri(), "name": "repo"}
    ]
    server.server.start.assert_called_once()
    server.server.send.initialize.assert_called_once()
