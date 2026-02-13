"""Method registry builders for MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping


def resolve_root_entries(roots: list[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for root in roots:
        name = Path(root).name or root
        result.append({"uri": f"file://{root}", "name": name})
    return result


def build_dispatch_methods(
    *,
    handle_initialize: Callable[[Mapping[str, object]], object],
    list_tools: Callable[[], list[dict[str, object]]],
    list_roots: Callable[[], list[dict[str, str]]],
    server_name: str,
    server_version: str,
    workspace_root: str,
    pid: int,
) -> dict[str, Callable[[Mapping[str, object]], object]]:
    return {
        "initialize": handle_initialize,
        "sari/identify": lambda _params: {
            "name": server_name,
            "version": server_version,
            "workspaceRoot": workspace_root,
            "pid": pid,
        },
        "tools/list": lambda _params: {"tools": list_tools()},
        "prompts/list": lambda _params: {"prompts": []},
        "resources/list": lambda _params: {"resources": []},
        "resources/templates/list": lambda _params: {"resourceTemplates": []},
        "roots/list": lambda _params: {"roots": list_roots()},
        "initialized": lambda _params: {},
        "notifications/initialized": lambda _params: {},
        "ping": lambda _params: {},
    }
