from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_HTTP_CLIENT = _REPO_ROOT / "src" / "sari" / "mcp" / "cli" / "http_client.py"


def test_http_client_uses_core_endpoint_resolver() -> None:
    source = _HTTP_CLIENT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_HTTP_CLIENT))

    imported = set()
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported.add((module, alias.name))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)

    assert ("sari.core.endpoint_resolver", "resolve_http_endpoint") in imported
    assert "resolve_http_endpoint" in calls


def test_http_client_does_not_depend_on_legacy_server_json_loader() -> None:
    source = _HTTP_CLIENT.read_text(encoding="utf-8")
    assert "load_server_info" not in source
    assert '".codex" / "tools" / "sari" / "data" / "server.json"' not in source
