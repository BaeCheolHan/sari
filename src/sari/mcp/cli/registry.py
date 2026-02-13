"""
Registry operations for Sari CLI.

This module handles server registry and server info operations.
"""

import json
import os
from pathlib import Path
from typing import Optional, TypeAlias

from sari.mcp.server_registry import ServerRegistry, get_registry_path

JsonMap: TypeAlias = dict[str, object]
InstancesMap: TypeAlias = dict[str, JsonMap]

def load_registry_instances() -> InstancesMap:
    """
    Load all daemon instances from registry.
    
    Returns:
        Dictionary of registry instances, or empty dict if unavailable
    """
    try:
        reg_file = get_registry_path()
        if reg_file.exists():
            return json.loads(reg_file.read_text(encoding="utf-8")).get("instances", {})
    except Exception:
        pass
    return {}


def load_server_info(workspace_root: str) -> Optional[dict]:
    """
    Load server info from legacy server.json location.
    
    This is for backward compatibility with older Sari versions.
    
    Args:
        workspace_root: Workspace root directory
    
    Returns:
        Server info dict or None if not found
    """
    strict_ssot = (os.environ.get("SARI_STRICT_SSOT") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if strict_ssot:
        return None

    server_json = Path(workspace_root) / ".codex" / "tools" / "sari" / "data" / "server.json"
    if not server_json.exists():
        return None
    try:
        info = json.loads(server_json.read_text(encoding="utf-8"))
    except Exception:
        return None

    # SSOT hardening: if registry has a workspace endpoint and legacy server.json
    # conflicts, ignore legacy to keep registry authoritative.
    try:
        endpoint = ServerRegistry().resolve_workspace_http(str(workspace_root))
        if endpoint:
            reg_host = str(endpoint.get("host") or "").strip()
            reg_port = endpoint.get("port")
            legacy_host = str(info.get("host") or "").strip()
            legacy_port = info.get("port")
            try:
                reg_port_i = int(reg_port) if reg_port is not None else None
                legacy_port_i = int(legacy_port) if legacy_port is not None else None
            except (TypeError, ValueError):
                reg_port_i = None
                legacy_port_i = None

            host_conflict = bool(legacy_host and reg_host and legacy_host != reg_host)
            port_conflict = (
                reg_port_i is not None
                and legacy_port_i is not None
                and reg_port_i != legacy_port_i
            )
            if host_conflict or port_conflict:
                return None
    except Exception:
        pass

    return info
