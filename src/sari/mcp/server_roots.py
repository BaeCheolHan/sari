"""Workspace root resolution helpers for MCP server."""

from __future__ import annotations

from typing import Callable


def collect_workspace_roots(
    *,
    workspace_root: str,
    resolve_config_path: Callable[[str], str],
    config_load: Callable[..., object],
    resolve_workspace_roots: Callable[[object, list[str]], list[str]],
) -> list[str]:
    cfg = None
    try:
        cfg_path = resolve_config_path(workspace_root)
        cfg = config_load(cfg_path, workspace_root_override=workspace_root)
    except Exception:
        cfg = None
    config_roots = list(getattr(cfg, "workspace_roots", []) or []) if cfg else []
    effective_roots = config_roots or [workspace_root]
    return resolve_workspace_roots(None, effective_roots)
