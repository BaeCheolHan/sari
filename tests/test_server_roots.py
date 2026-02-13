from __future__ import annotations

from types import SimpleNamespace

from sari.mcp.server_roots import collect_workspace_roots


def test_collect_workspace_roots_uses_config_roots_when_available():
    calls = {}

    def _resolve_config_path(ws):
        calls["ws"] = ws
        return "/tmp/cfg.json"

    def _config_load(_cfg_path, workspace_root_override=None):
        assert workspace_root_override == "/tmp/ws"
        return SimpleNamespace(workspace_roots=["/tmp/ws", "/tmp/other"])

    def _resolve_workspace_roots(root_uri, config_roots):
        assert root_uri is None
        assert config_roots == ["/tmp/ws", "/tmp/other"]
        return ["/tmp/ws", "/tmp/other"]

    roots = collect_workspace_roots(
        workspace_root="/tmp/ws",
        resolve_config_path=_resolve_config_path,
        config_load=_config_load,
        resolve_workspace_roots=_resolve_workspace_roots,
    )
    assert roots == ["/tmp/ws", "/tmp/other"]
    assert calls["ws"] == "/tmp/ws"


def test_collect_workspace_roots_falls_back_when_config_load_fails():
    roots = collect_workspace_roots(
        workspace_root="/tmp/ws",
        resolve_config_path=lambda _ws: "/tmp/cfg.json",
        config_load=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        resolve_workspace_roots=lambda _uri, config_roots: ["/tmp/ws"] if _uri is None and config_roots == ["/tmp/ws"] else [],
    )
    assert roots == ["/tmp/ws"]
